from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol
from uuid import uuid4

from .adapters import build_cognitive_agent_registry
from .artifacts import HarnessArtifactStore
from .contracts import (
    ArtifactKind,
    ArtifactRef,
    HarnessDecision,
    MemoryCandidate,
    MemoryControllerResult,
    MemoryEvaluation,
    PolicyDecision,
    RecoveryAction as RecoveryActionRecord,
)
from .controllers import (
    ConservativeMemoryEvaluator,
    CriticController,
    HumanApprovalController,
    MemoryController,
    MemoryEvaluator,
)
from .observability import HarnessObservability
from .persistence import HarnessStateNotFound, HarnessStateRepository
from .policy import PolicyEngine
from .recovery import RecoveryAction, RecoveryDecision, RecoveryManager
from .registry import ARTIFACT_STATE_KEYS, MEMORY_EVALUATOR_CONTRACT, AgentContractRegistry
from .scheduler import AgentScheduler, SchedulerAction, SchedulerDecision
from .state import HarnessCheckpoint, HarnessError, PersistentCognitiveState


_RECOVERY_ACTION_RECORD = {
    RecoveryAction.JSON_REPAIR: "json_repair",
    RecoveryAction.RETRY_STAGE: "retry",
    RecoveryAction.SWITCH_MODEL: "model_switch",
    RecoveryAction.RESUME_CHECKPOINT: "checkpoint_resume",
    RecoveryAction.GRACEFUL_DEGRADATION: "graceful_degradation",
}

_NODE_BY_AGENT = {
    "goal_intelligence": "goal_intelligence",
    "goal_completion": "goal_completion",
    "reality": "reality",
    "evidence": "evidence",
    "strategy": "strategy",
    "execution": "execution",
    "critic": "critic",
    "feedback_learning": "feedback_learning",
}

_WAIT_BY_NODE = {
    "wait_for_goal_answer": "user_input",
    "wait_for_strategy_approval": "strategy_approval",
    "wait_for_execution_approval": "execution_approval",
}


class CompiledHarnessGraph(Protocol):
    def invoke(self, state: dict[str, Any]) -> dict[str, Any]: ...


GraphBuilder = Callable[..., CompiledHarnessGraph]


class HarnessRuntime:
    """Lifecycle owner around existing cognitive Agent node adapters.

    Existing Agent classes and prompts remain untouched. The supplied adapter
    exposes node callables, while Scheduler and RecoveryManager own control and
    failure decisions. LangGraph is only the execution mechanism.
    """

    def __init__(
        self,
        *,
        scheduler: AgentScheduler | None = None,
        recovery: RecoveryManager | None = None,
        repository: HarnessStateRepository | None = None,
        observability: HarnessObservability | None = None,
        registry: AgentContractRegistry | None = None,
        artifact_runtime: Any | None = None,
        policy: PolicyEngine | None = None,
        critic: CriticController | None = None,
        memory_evaluator: MemoryEvaluator | None = None,
    ) -> None:
        self.scheduler = scheduler or AgentScheduler()
        self.recovery = recovery or RecoveryManager()
        self.repository = repository or HarnessStateRepository()
        self.observability = observability or HarnessObservability(self.repository)
        self.registry = registry or build_cognitive_agent_registry()
        self.policy = policy or PolicyEngine()
        self.critic = critic or CriticController()
        self.memory_evaluator = memory_evaluator or ConservativeMemoryEvaluator()
        if artifact_runtime is None:
            from ..services.planning_agent_runtime import PlanningAgentRuntime

            artifact_runtime = PlanningAgentRuntime()
        self.artifact_runtime = artifact_runtime
        self.artifact_store = HarnessArtifactStore(artifact_runtime)
        self._compiled_graph: CompiledHarnessGraph | None = None
        self._adapter_identity: int | None = None
        self._builder_identity: int | None = None

    def bind(self, adapter: Any, graph_builder: GraphBuilder) -> CompiledHarnessGraph:
        adapter_identity = id(adapter)
        builder_identity = id(graph_builder)
        if (
            self._compiled_graph is None
            or self._adapter_identity != adapter_identity
            or self._builder_identity != builder_identity
        ):
            self._compiled_graph = graph_builder(adapter, scheduler=self.scheduler)
            self._adapter_identity = adapter_identity
            self._builder_identity = builder_identity
        return self._compiled_graph

    def invoke(
        self,
        *,
        adapter: Any,
        graph_builder: GraphBuilder,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return self.bind(adapter, graph_builder).invoke(state)

    def decide_model_failure(self, state: dict[str, Any], error: Any) -> RecoveryDecision:
        return self.recovery.decide_model_failure(state, error)

    def restore_graph_state(self, graph_state: dict[str, Any]) -> dict[str, Any]:
        """Restore the pending Agent pointer from a durable checkpoint."""

        session_id = str(graph_state.get("session_id") or "")
        if not session_id:
            return graph_state
        try:
            persistent = self.repository.recover(session_id)
        except HarnessStateNotFound:
            return graph_state
        if persistent.pending_agent:
            resume_node = _NODE_BY_AGENT.get(persistent.pending_agent)
            if resume_node:
                graph_state["resume_node"] = resume_node
        return graph_state

    def bootstrap(self, graph_state: Mapping[str, Any]) -> PersistentCognitiveState:
        session_id = str(graph_state.get("session_id") or "")
        if not session_id:
            raise ValueError("Harness state requires a planning session id")
        persistent = self.repository.create_or_load(session_id)
        heads = self._artifact_heads(session_id)
        versions = {kind: ref.version for kind, ref in heads.items()}
        current_refs = persistent.checkpoint.artifact_refs
        changed = (
            set(current_refs) != set(heads)
            or any(
                not ref.same_version(current_refs.get(kind))
                or ref.status != current_refs[kind].status
                for kind, ref in heads.items()
            )
        )
        if not changed and persistent.artifact_versions == versions:
            return persistent
        approvals = persistent.approvals
        if persistent.approvals:
            controller = HumanApprovalController(persistent.approvals)
            for kind, ref in heads.items():
                previous = current_refs.get(kind)
                if previous and not ref.same_version(previous):
                    controller.invalidate_after_repair(
                        session_id=session_id,
                        repaired_artifact=kind,
                        reason=f"{kind} changed from v{previous.version} to v{ref.version}.",
                    )
            approvals = controller.records
        updated = persistent.model_copy(
            update={
                "artifact_versions": versions,
                "approvals": approvals,
                "checkpoint": HarnessCheckpoint(
                    artifactRefs=heads,
                    artifactVersions=versions,
                ),
            }
        )
        result = self.observability.record(
            updated,
            event_type="artifact_changed",
            decision="bootstrap_heads",
            payload={
                "artifactHeads": {
                    kind: ref.model_dump(by_alias=True, exclude_none=True)
                    for kind, ref in heads.items()
                }
            },
        )
        return result.state

    def record_scheduler_decision(
        self,
        graph_state: Mapping[str, Any],
        decision: SchedulerDecision,
    ) -> str:
        persistent = self.bootstrap(graph_state)
        decision, policy_decision = self._apply_scheduler_policy(
            persistent,
            graph_state,
            decision,
        )
        lifecycle = persistent.lifecycle
        waiting_state = persistent.waiting_state
        pending_agent = decision.agent_id
        if decision.action in {
            SchedulerAction.INVOKE_AGENT,
            SchedulerAction.INVOKE_CONTROLLER,
            SchedulerAction.REPAIR,
        }:
            lifecycle = "active"
            waiting_state = "none"
        elif decision.action == SchedulerAction.WAIT_USER:
            lifecycle = "waiting"
            waiting_state = "user_input"
            pending_agent = None
        elif decision.action == SchedulerAction.WAIT_APPROVAL:
            lifecycle = "waiting"
            waiting_state = _WAIT_BY_NODE.get(decision.next_node, "none")
            pending_agent = None
        elif decision.action == SchedulerAction.COMPLETE:
            pending_agent = None
        elif decision.action == SchedulerAction.RECOVER:
            lifecycle = "active"
            waiting_state = "none"
        elif decision.action == SchedulerAction.BLOCK:
            lifecycle = "blocked"
            pending_agent = persistent.pending_agent

        directive = {
            SchedulerAction.INVOKE_AGENT: "invoke_agent",
            SchedulerAction.INVOKE_CONTROLLER: "invoke_agent",
            SchedulerAction.WAIT_USER: "wait_user",
            SchedulerAction.WAIT_APPROVAL: "wait_approval",
            SchedulerAction.REPAIR: "repair_artifact",
            SchedulerAction.RECOVER: "invoke_agent",
            SchedulerAction.BLOCK: "block_runtime",
            SchedulerAction.COMPLETE: "finish",
        }[decision.action]
        harness_decision = HarnessDecision(
            directive=directive,
            nextAgent=decision.agent_id,
            graphNode=decision.next_node,
            reason=decision.reason_code,
            waitState=waiting_state,
            repairTarget=(decision.agent_id if decision.action == SchedulerAction.REPAIR else None),
            policyDecision=policy_decision,
        )
        updated = persistent.model_copy(
            update={
                "lifecycle": lifecycle,
                "current_stage": (
                    persistent.current_stage
                    if decision.action == SchedulerAction.BLOCK
                    else decision.next_node
                ),
                "pending_agent": pending_agent,
                "waiting_state": waiting_state,
                "last_decision": harness_decision,
            }
        )
        result = self.observability.harness_decision(updated, harness_decision)
        if decision.action == SchedulerAction.RECOVER:
            action = RecoveryActionRecord(
                action="checkpoint_resume",
                stage=decision.next_node,
                reason=decision.reason_code,
                retryable=True,
                payload={"resumeNode": decision.next_node},
            )
            self.observability.recovery_action(
                result.state,
                action,
                agent_id=decision.agent_id,
            )
        return decision.next_node

    def _apply_scheduler_policy(
        self,
        persistent: PersistentCognitiveState,
        graph_state: Mapping[str, Any],
        decision: SchedulerDecision,
    ) -> tuple[SchedulerDecision, PolicyDecision]:
        approvals = HumanApprovalController(persistent.approvals)
        strategy = persistent.checkpoint.artifact_refs.get("strategy_portfolio")
        execution = persistent.checkpoint.artifact_refs.get("execution_blueprint")

        runtime_blocked = persistent.lifecycle == "blocked"
        if runtime_blocked and decision.action != SchedulerAction.RECOVER:
            decision = SchedulerDecision(
                action=SchedulerAction.BLOCK,
                next_node="__end__",
                reason_code="runtime_recovery_required",
                agent_id=persistent.pending_agent,
            )
            return decision, self.policy.decide_planning_progress(
                session_id=persistent.session_id,
                runtime_blocked=True,
                next_agent=persistent.pending_agent,
            )

        if decision.next_node in {"execution", "calendar_gate"}:
            if not strategy or not approvals.is_approved(
                session_id=persistent.session_id,
                gate="strategy",
                artifact=strategy,
            ):
                decision = SchedulerDecision(
                    action=SchedulerAction.WAIT_APPROVAL,
                    next_node="wait_for_strategy_approval",
                    reason_code="strategy_approval_required_for_current_version",
                )
        if decision.next_node == "calendar_gate":
            if not execution or not approvals.is_approved(
                session_id=persistent.session_id,
                gate="execution",
                artifact=execution,
            ):
                decision = SchedulerDecision(
                    action=SchedulerAction.WAIT_APPROVAL,
                    next_node="wait_for_execution_approval",
                    reason_code="execution_approval_required_for_current_version",
                )

        if decision.action == SchedulerAction.WAIT_APPROVAL:
            gate = (
                "strategy"
                if decision.next_node == "wait_for_strategy_approval"
                else "execution"
            )
            artifact = strategy if gate == "strategy" else execution
            if artifact and approvals.is_approved(
                session_id=persistent.session_id,
                gate=gate,
                artifact=artifact,
            ):
                decision = SchedulerDecision(
                    action=SchedulerAction.INVOKE_CONTROLLER,
                    next_node=decision.next_node,
                    reason_code=f"{gate}_approval_verified_for_current_version",
                )
                policy = PolicyDecision(
                    subject="planning_progress",
                    action="allow",
                    allowed=True,
                    reason=decision.reason_code,
                    sessionId=persistent.session_id,
                    requiredGates=(
                        "strategy_approval" if gate == "strategy" else "execution_approval",
                    ),
                )
            else:
                policy = self.policy.decide_planning_progress(
                    session_id=persistent.session_id,
                    runtime_blocked=False,
                    approval_gate=gate,
                )
        elif decision.action == SchedulerAction.WAIT_USER:
            completion = graph_state.get("goal_completion")
            raw_unknowns = list(getattr(completion, "blocking_unknowns", []) or [])
            blocking = tuple(
                str(getattr(item, "question", None) or getattr(item, "description", None) or item)
                for item in raw_unknowns
            )
            if blocking:
                policy = self.policy.decide_planning_progress(
                    session_id=persistent.session_id,
                    runtime_blocked=False,
                    blocking_unknowns=blocking,
                )
            elif decision.reason_code.startswith("critic_"):
                policy = PolicyDecision(
                    subject="critic_review",
                    action="wait_user",
                    allowed=False,
                    reason=decision.reason_code,
                    sessionId=persistent.session_id,
                    requiredGates=("critic",),
                    failedGates=("critic",),
                )
            else:
                policy = PolicyDecision(
                    subject="user_question",
                    action="wait_user",
                    allowed=False,
                    reason=decision.reason_code,
                    sessionId=persistent.session_id,
                )
        elif decision.action == SchedulerAction.REPAIR:
            policy = PolicyDecision(
                subject="critic_review",
                action="repair_artifact",
                allowed=False,
                reason=decision.reason_code,
                sessionId=persistent.session_id,
                failedGates=("critic",),
            )
        elif decision.action == SchedulerAction.COMPLETE:
            policy = self.policy.decide_planning_progress(
                session_id=persistent.session_id,
                runtime_blocked=False,
            )
        elif decision.action == SchedulerAction.BLOCK:
            policy = self.policy.decide_planning_progress(
                session_id=persistent.session_id,
                runtime_blocked=True,
                next_agent=persistent.pending_agent,
            )
        elif decision.action == SchedulerAction.INVOKE_CONTROLLER:
            policy = PolicyDecision(
                subject="planning_progress",
                action="allow",
                allowed=True,
                reason=decision.reason_code,
                sessionId=persistent.session_id,
            )
        else:
            policy = self.policy.decide_planning_progress(
                session_id=persistent.session_id,
                runtime_blocked=False,
                next_agent=decision.agent_id,
            )
        return decision, policy

    def wrap_session_guard(self, node: Callable[[dict[str, Any]], dict[str, Any]]):
        def wrapped(state: dict[str, Any]) -> dict[str, Any]:
            self.bootstrap(state)
            return node(state)

        return wrapped

    def wrap_agent_node(
        self,
        node_name: str,
        node: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        agent_id = _NODE_BY_AGENT[node_name]

        def wrapped(state: dict[str, Any]) -> dict[str, Any]:
            persistent = self.bootstrap(state)
            contract = self.registry.get(agent_id)
            missing = list(dict.fromkeys([
                *self.registry.missing_inputs(agent_id, state),
                *[
                    kind
                    for kind in contract.input_artifacts
                    if kind not in persistent.checkpoint.artifact_refs
                ],
            ]))
            invocation_id = str(uuid4())
            input_versions = {
                kind: persistent.checkpoint.artifact_refs[kind].version
                for kind in contract.input_artifacts
                if kind in persistent.checkpoint.artifact_refs
            }
            if missing:
                return self._fail_missing_inputs(
                    state,
                    persistent=persistent,
                    agent_id=agent_id,
                    node_name=node_name,
                    invocation_id=invocation_id,
                    missing=missing,
                    input_versions=input_versions,
                )

            previous_output = (
                persistent.checkpoint.artifact_refs.get(contract.output_artifact)
                if contract.output_artifact
                else None
            )
            running = persistent.model_copy(
                update={
                    "lifecycle": "active",
                    "current_stage": node_name,
                    "pending_agent": agent_id,
                    "waiting_state": "none",
                }
            )
            running_result = self.observability.agent_invocation(
                running,
                agent_id=agent_id,
                status="running",
                invocation_id=invocation_id,
                stage=node_name,
                input_artifacts=input_versions,
            )
            result_state = node(state)
            errors = list(result_state.get("errors") or [])
            if result_state.get("planning_mode") != "model_backed" and errors:
                self._record_failed_invocation(
                    result_state,
                    persistent=running_result.state,
                    agent_id=agent_id,
                    node_name=node_name,
                    invocation_id=invocation_id,
                    input_versions=input_versions,
                    error=errors[-1],
                )
                return result_state

            persistent = self.bootstrap(result_state)
            output = (
                persistent.checkpoint.artifact_refs.get(contract.output_artifact)
                if contract.output_artifact
                else None
            )
            finalized_critique = bool(
                agent_id == "critic"
                and output is not None
                and state.get("finalized_critique_artifact_id") == output.id
            )
            if contract.output_artifact and (
                output is None
                or (
                    previous_output
                    and output.same_version(previous_output)
                    and not finalized_critique
                )
            ):
                return self._fail_missing_output(
                    result_state,
                    persistent=persistent,
                    agent_id=agent_id,
                    node_name=node_name,
                    invocation_id=invocation_id,
                    output_kind=contract.output_artifact,
                    input_versions=input_versions,
                )
            succeeded = persistent.model_copy(
                update={
                    "lifecycle": "active",
                    "current_stage": node_name,
                    "pending_agent": None,
                    "waiting_state": "none",
                }
            )
            success_result = self.observability.agent_invocation(
                succeeded,
                agent_id=agent_id,
                status="succeeded",
                invocation_id=invocation_id,
                stage=node_name,
                input_artifacts=input_versions,
                output_artifact=(
                    output.model_dump(by_alias=True, exclude_none=True)
                    if output
                    else {}
                ),
            )
            self._record_successful_model_routes(
                success_result.state,
                agent_id=agent_id,
                agent_name=contract.name,
                invocation_id=invocation_id,
            )
            return result_state

        return wrapped

    def wrap_wait_node(
        self,
        node_name: str,
        node: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        def wrapped(state: dict[str, Any]) -> dict[str, Any]:
            result_state = node(state)
            persistent = self.bootstrap(result_state)
            waiting = _WAIT_BY_NODE[node_name]
            approval_consumed = (
                node_name == "wait_for_execution_approval"
                and result_state.get("status") == "ready_to_write_calendar"
            )
            approvals = persistent.approvals
            gate = {
                "wait_for_strategy_approval": "strategy",
                "wait_for_execution_approval": "execution",
            }.get(node_name)
            if gate and not approval_consumed:
                kind = "strategy_portfolio" if gate == "strategy" else "execution_blueprint"
                artifact = persistent.checkpoint.artifact_refs.get(kind)
                if artifact:
                    controller = HumanApprovalController(persistent.approvals)
                    controller.request(
                        session_id=persistent.session_id,
                        gate=gate,
                        artifact=artifact,
                    )
                    approvals = controller.records
            updated = persistent.model_copy(
                update={
                    "lifecycle": "active" if approval_consumed else "waiting",
                    "current_stage": node_name,
                    "pending_agent": None,
                    "waiting_state": "none" if approval_consumed else waiting,
                    "approvals": approvals,
                }
            )
            self.observability.record(
                updated,
                event_type="harness_decision",
                decision="approval_applied" if approval_consumed else "wait_checkpoint",
                payload={
                    "graphNode": node_name,
                    "waitingState": "none" if approval_consumed else waiting,
                },
            )
            return result_state

        return wrapped

    def wrap_controller_node(
        self,
        node_name: str,
        node: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        def wrapped(state: dict[str, Any]) -> dict[str, Any]:
            persistent = self.bootstrap(state)
            active = persistent.model_copy(
                update={"current_stage": node_name, "waiting_state": "none"}
            )
            checkpoint = self.observability.record(
                active,
                event_type="harness_decision",
                decision="controller_running",
                payload={"graphNode": node_name},
            )
            result_state = node(state)
            synced = self.bootstrap(result_state)
            if node_name == "calendar_gate":
                execution = synced.checkpoint.artifact_refs.get("execution_blueprint")
                approvals = synced.approvals
                if execution:
                    controller = HumanApprovalController(synced.approvals)
                    controller.request(
                        session_id=synced.session_id,
                        gate="calendar",
                        artifact=execution,
                    )
                    approvals = controller.records
                policy = self.policy.decide_planning_progress(
                    session_id=synced.session_id,
                    runtime_blocked=False,
                    approval_gate="calendar",
                )
                waiting = synced.model_copy(
                    update={
                        "lifecycle": "waiting",
                        "current_stage": "calendar_gate",
                        "waiting_state": "calendar_approval",
                        "approvals": approvals,
                    }
                )
                self.observability.typed_policy_decision(waiting, policy)
            return result_state

        return wrapped

    def record_approval(self, session_id: str, gate: str):
        persistent = self.bootstrap({"session_id": session_id})
        artifact_kind = "strategy_portfolio" if gate == "strategy" else "execution_blueprint"
        artifact = persistent.checkpoint.artifact_refs.get(artifact_kind)
        if artifact is None:
            raise ValueError(f"cannot approve {gate}: {artifact_kind} artifact is missing")
        controller = HumanApprovalController(persistent.approvals)
        pending = controller.request(session_id=session_id, gate=gate, artifact=artifact)
        if pending.status == "pending":
            approved = controller.decide(pending.id, approved=True)
        else:
            approved = pending
        updated = persistent.model_copy(update={"approvals": controller.records})
        policy_gate = {
            "strategy": "strategy_approval",
            "execution": "execution_approval",
            "calendar": "calendar_approval",
        }[gate]
        policy = PolicyDecision(
            subject="calendar_write" if gate == "calendar" else "planning_progress",
            action="allow",
            allowed=True,
            reason=f"Human approval is bound to {artifact.kind} v{artifact.version}.",
            sessionId=session_id,
            requiredGates=(policy_gate,),
        )
        self.observability.typed_policy_decision(
            updated,
            policy,
            context={"artifactId": artifact.id, "artifactVersion": artifact.version},
        )
        return approved

    def consume_calendar_approval(self, session_id: str) -> None:
        persistent = self.bootstrap({"session_id": session_id})
        execution = persistent.checkpoint.artifact_refs.get("execution_blueprint")
        if not execution:
            raise ValueError("cannot consume Calendar approval without an Execution artifact")
        controller = HumanApprovalController(persistent.approvals)
        approval = next(
            (
                item
                for item in reversed(controller.records)
                if item.approves(
                    session_id=session_id,
                    gate="calendar",
                    artifact=execution,
                )
            ),
            None,
        )
        if not approval:
            raise ValueError("current Execution artifact has no Calendar approval")
        controller.consume(approval.id)
        updated = persistent.model_copy(update={"approvals": controller.records})
        self.observability.policy_decision(
            updated,
            policy="calendar_approval",
            allowed=True,
            reason="Calendar approval was consumed after a successful write.",
            context={"artifactId": execution.id, "artifactVersion": execution.version},
        )

    def calendar_write_policy(
        self,
        session_id: str,
        *,
        planning_mode: str,
        critique_report: Any,
    ) -> PolicyDecision:
        persistent = self.bootstrap({"session_id": session_id})
        refs = persistent.checkpoint.artifact_refs
        strategy = refs.get("strategy_portfolio")
        execution = refs.get("execution_blueprint")
        critique = refs.get("critique_report")
        critic_gate = None
        if execution and critique:
            try:
                critique_body = self.artifact_store.load(critique)
            except Exception:
                critique_body = None
        else:
            critique_body = None
        if execution and critique and critique_body is not None:
            evaluated = self._critic_evaluated_execution(
                session_id,
                execution=execution,
                critique=critique,
            )
            critic_gate = self.critic.assess(
                report=critique_body,
                critique_artifact=critique,
                execution_artifact=execution,
                evaluated_execution_artifact=evaluated,
            )
        decision = self.policy.authorize_calendar_write(
            session_id=session_id,
            planning_mode=planning_mode,
            strategy_artifact=strategy,
            execution_artifact=execution,
            critic=critic_gate,
            approvals=persistent.approvals,
        )
        self.observability.typed_policy_decision(
            persistent,
            decision,
            context={
                "strategyVersion": strategy.version if strategy else None,
                "executionVersion": execution.version if execution else None,
                "critiqueVersion": critique.version if critique else None,
            },
        )
        return decision

    def assert_current_artifact(
        self,
        session_id: str,
        *,
        kind: ArtifactKind,
        expected_ref: Mapping[str, Any] | ArtifactRef,
    ) -> ArtifactRef:
        persistent = self.bootstrap({"session_id": session_id})
        current = persistent.checkpoint.artifact_refs.get(kind)
        try:
            expected = (
                expected_ref
                if isinstance(expected_ref, ArtifactRef)
                else ArtifactRef.model_validate(expected_ref)
            )
        except Exception as exc:
            raise ValueError(f"invalid {kind} artifact binding") from exc
        if not current or not current.same_version(expected):
            raise ValueError(f"{kind} changed after this action was prepared")
        return current

    def critic_policy(
        self,
        session_id: str,
        *,
        critique_report: Any,
    ) -> PolicyDecision:
        persistent = self.bootstrap({"session_id": session_id})
        execution = persistent.checkpoint.artifact_refs.get("execution_blueprint")
        critique = persistent.checkpoint.artifact_refs.get("critique_report")
        try:
            critique_body = self.artifact_store.load(critique) if critique else None
        except Exception:
            critique_body = None
        if not execution or not critique or critique_body is None:
            decision = PolicyDecision(
                subject="critic_review",
                action="deny",
                allowed=False,
                reason="Current Execution and Critique artifacts are required.",
                sessionId=session_id,
                requiredGates=("critic",),
                failedGates=("critic",),
            )
        else:
            evaluated = self._critic_evaluated_execution(
                session_id,
                execution=execution,
                critique=critique,
            )
            gate = self.critic.assess(
                report=critique_body,
                critique_artifact=critique,
                execution_artifact=execution,
                evaluated_execution_artifact=evaluated,
            )
            decision = self.critic.policy_decision(gate)
        self.observability.typed_policy_decision(
            persistent,
            decision,
            agent_id="critic",
        )
        return decision

    def _critic_evaluated_execution(
        self,
        session_id: str,
        *,
        execution: ArtifactRef,
        critique: ArtifactRef,
    ) -> ArtifactRef:
        events = self.repository.events(session_id, limit=5000)
        for event in reversed(events):
            if (
                event.event_type != "agent_invocation"
                or event.agent_id != "critic"
                or event.decision != "succeeded"
            ):
                continue
            output = event.payload.get("outputArtifact")
            inputs = event.payload.get("inputArtifacts")
            if not isinstance(output, Mapping) or not isinstance(inputs, Mapping):
                continue
            if str(output.get("id") or "") != critique.id:
                continue
            try:
                version = int(inputs.get("execution_blueprint") or 0)
            except (TypeError, ValueError):
                version = 0
            if version == execution.version:
                return execution
            return ArtifactRef(
                id=f"stale-execution-v{version}",
                sessionId=session_id,
                kind="execution_blueprint",
                version=max(1, version),
                owner=execution.owner,
                status="superseded",
            )
        # Compatibility adapter for pre-Harness cognitive sessions. Their
        # immutable AgentDecision lineage still identifies the exact Execution
        # and Critique artifact IDs even though no harness invocation event
        # existed yet.
        for decision in reversed(self.artifact_runtime.list_decisions(session_id)):
            if decision.agent not in {
                "Critic Agent",
                "Independent Critic & Learning Agent",
            }:
                continue
            if critique.id not in decision.output_artifact_ids:
                continue
            if execution.id in decision.input_artifact_ids:
                return execution
            break
        return ArtifactRef(
            id="unbound-critic-execution",
            sessionId=session_id,
            kind="execution_blueprint",
            version=execution.version,
            owner=execution.owner,
            status="superseded",
        )

    def evaluate_memory_candidate(
        self,
        session_id: str,
        *,
        learning_update: Any,
        memory_repository: Any,
    ) -> MemoryControllerResult | None:
        persistent = self.bootstrap({"session_id": session_id})
        source = persistent.checkpoint.artifact_refs.get("planning_learning_update")
        if not source:
            return None
        try:
            from ..services.cognitive_planning.contracts import PlanningLearningUpdate

            bound_update = PlanningLearningUpdate.model_validate(
                self.artifact_store.load(source)
            )
        except Exception:
            return None
        hypothesis = bound_update.user_model_hypothesis
        if not bound_update.should_persist or hypothesis is None:
            return None
        candidate = MemoryCandidate(
            id=f"memory-candidate:{source.id}",
            sessionId=session_id,
            sourceArtifact=source,
            category=getattr(hypothesis, "category", "planning_hypothesis"),
            statement=str(getattr(hypothesis, "rule", "") or ""),
            evidence=str(getattr(hypothesis, "evidence", "") or ""),
            domainScope=list(getattr(hypothesis, "domain_scope", []) or []),
            confidence=float(getattr(hypothesis, "confidence", 0) or 0),
            evidencePolarity=getattr(hypothesis, "evidence_polarity", "positive"),
            expiresAt=getattr(hypothesis, "expires_at", None),
        )
        invocation_id = str(uuid4())
        running = persistent.model_copy(
            update={
                "lifecycle": "active",
                "current_stage": "memory_evaluation",
                "pending_agent": "memory_evaluator",
                "waiting_state": "none",
            }
        )
        running_result = self.observability.agent_invocation(
            running,
            agent_id="memory_evaluator",
            status="running",
            invocation_id=invocation_id,
            stage="memory_evaluation",
            input_artifacts={"planning_learning_update": source.version},
        )
        controller = MemoryController(
            evaluator=self.memory_evaluator,
            repository=memory_repository,
            policy=self.policy,
        )
        evaluation, error = controller.evaluate(candidate)
        if evaluation is None:
            harness_error = HarnessError(
                stage="memory_evaluation",
                errorType="memory_evaluation_failed",
                message=error or "Memory Evaluation failed.",
                retryable=True,
            )
            failed_state = running_result.state.model_copy(
                update={
                    "pending_agent": None,
                    "errors": [*running_result.state.errors, harness_error],
                }
            )
            failed = self.observability.agent_invocation(
                failed_state,
                agent_id="memory_evaluator",
                status="failed",
                invocation_id=invocation_id,
                stage="memory_evaluation",
                input_artifacts={"planning_learning_update": source.version},
                error=harness_error.model_dump(by_alias=True),
            )
            self.observability.recovery_action(
                failed.state,
                RecoveryActionRecord(
                    action="graceful_degradation",
                    stage="memory_evaluation",
                    reason=harness_error.message,
                    retryable=True,
                    payload={"planRepairContinues": True},
                ),
                agent_id="memory_evaluator",
            )
            decision = self.policy.authorize_memory_persistence(
                candidate=candidate,
                evaluation=None,
            )
            return MemoryControllerResult(
                persisted=False,
                evaluation=None,
                policyDecision=decision,
                error=harness_error.message,
            )

        admission = self.policy.authorize_memory_persistence(
            candidate=candidate,
            evaluation=evaluation,
        )
        try:
            evaluation_ref = self.artifact_store.record_output(
                MEMORY_EVALUATOR_CONTRACT,
                session_id=session_id,
                content=evaluation,
                status="approved" if admission.allowed else "blocked",
                input_refs=(source,),
            )
        except Exception as exc:
            harness_error = HarnessError(
                stage="memory_evaluation",
                errorType="memory_evaluation_artifact_failed",
                message=str(exc),
                retryable=True,
            )
            failed = self.observability.agent_invocation(
                running_result.state.model_copy(
                    update={
                        "pending_agent": None,
                        "errors": [*running_result.state.errors, harness_error],
                    }
                ),
                agent_id="memory_evaluator",
                status="failed",
                invocation_id=invocation_id,
                stage="memory_evaluation",
                input_artifacts={"planning_learning_update": source.version},
                error=harness_error.model_dump(by_alias=True),
            )
            self.observability.recovery_action(
                failed.state,
                RecoveryActionRecord(
                    action="graceful_degradation",
                    stage="memory_evaluation",
                    reason=str(exc),
                    retryable=True,
                    payload={"planRepairContinues": True, "memoryWrite": False},
                ),
                agent_id="memory_evaluator",
            )
            return MemoryControllerResult(
                persisted=False,
                evaluation=evaluation,
                policyDecision=admission,
                error=str(exc),
            )
        synced = self.bootstrap({"session_id": session_id})
        completed = self.observability.agent_invocation(
            synced.model_copy(update={"pending_agent": None}),
            agent_id="memory_evaluator",
            status="succeeded",
            invocation_id=invocation_id,
            stage="memory_evaluation",
            input_artifacts={"planning_learning_update": source.version},
            output_artifact=evaluation_ref.model_dump(by_alias=True),
        )
        result = controller.persist_evaluated(candidate, evaluation)
        self.observability.typed_policy_decision(
            completed.state,
            result.policy_decision,
            agent_id="memory_evaluator",
            context={
                "candidateId": candidate.id,
                "evaluationArtifactId": evaluation_ref.id,
                "persisted": result.persisted,
                "memoryId": result.memory_id,
                "error": result.error,
            },
        )
        if result.error:
            latest = self.repository.recover(session_id)
            self.observability.recovery_action(
                latest,
                RecoveryActionRecord(
                    action="graceful_degradation",
                    stage="memory_evaluation",
                    reason=result.error,
                    retryable=True,
                    payload={"planRepairContinues": True, "memoryWrite": False},
                ),
                agent_id="memory_evaluator",
            )
        return result

    def _artifact_heads(self, session_id: str) -> dict[str, ArtifactRef]:
        heads: dict[str, ArtifactRef] = {}
        for artifact in self.artifact_runtime.list_artifacts(session_id):
            kind = str(artifact.artifact_type)
            if kind not in ARTIFACT_STATE_KEYS:
                continue
            current = heads.get(kind)
            if current and current.version >= int(artifact.version):
                continue
            heads[kind] = ArtifactRef(
                id=artifact.id,
                sessionId=session_id,
                kind=kind,
                version=int(artifact.version),
                owner=artifact.owner_agent,
                status=artifact.status,
            )
        return heads

    def _fail_missing_inputs(
        self,
        graph_state: dict[str, Any],
        *,
        persistent: PersistentCognitiveState,
        agent_id: str,
        node_name: str,
        invocation_id: str,
        missing: list[str],
        input_versions: dict[str, int],
    ) -> dict[str, Any]:
        message = "Missing required Harness input artifacts: " + ", ".join(missing)
        error = HarnessError(
            stage=node_name,
            errorType="missing_input_artifact",
            message=message,
            retryable=False,
        )
        blocked = persistent.model_copy(
            update={
                "lifecycle": "blocked",
                "current_stage": node_name,
                "pending_agent": agent_id,
                "waiting_state": "model_recovery",
                "errors": [*persistent.errors, error],
            }
        )
        self.observability.agent_invocation(
            blocked,
            agent_id=agent_id,
            status="failed",
            invocation_id=invocation_id,
            stage=node_name,
            input_artifacts=input_versions,
            error=error.model_dump(by_alias=True),
        )
        graph_state["planning_mode"] = "blocked_model_unavailable"
        graph_state["runtime_status"] = "retry_required"
        graph_state["status"] = "MODEL_UNAVAILABLE"
        graph_state["resume_node"] = node_name
        return graph_state

    def _fail_missing_output(
        self,
        graph_state: dict[str, Any],
        *,
        persistent: PersistentCognitiveState,
        agent_id: str,
        node_name: str,
        invocation_id: str,
        output_kind: str,
        input_versions: dict[str, int],
    ) -> dict[str, Any]:
        return self._fail_missing_inputs(
            graph_state,
            persistent=persistent,
            agent_id=agent_id,
            node_name=node_name,
            invocation_id=invocation_id,
            missing=[f"output:{output_kind}"],
            input_versions=input_versions,
        )

    def _record_failed_invocation(
        self,
        graph_state: Mapping[str, Any],
        *,
        persistent: PersistentCognitiveState,
        agent_id: str,
        node_name: str,
        invocation_id: str,
        input_versions: dict[str, int],
        error: Any,
    ) -> None:
        attempts = [
            {
                str(key): value
                for key, value in dict(item).items()
                if isinstance(value, (str, int, bool)) or value is None
            }
            for item in list(getattr(error, "attempts", []) or [])
            if isinstance(item, Mapping)
        ]
        harness_error = HarnessError(
            stage=str(getattr(error, "stage", node_name) or node_name),
            errorType=str(getattr(error, "error_type", "model_unavailable")),
            message=str(getattr(error, "message", "Agent invocation failed.")),
            retryable=bool(getattr(error, "retryable", True)),
            attempts=attempts,
        )
        blocked = persistent.model_copy(
            update={
                "lifecycle": "blocked",
                "current_stage": harness_error.stage,
                "pending_agent": agent_id,
                "waiting_state": "model_recovery",
                "errors": [*persistent.errors, harness_error],
            }
        )
        failed = self.observability.agent_invocation(
            blocked,
            agent_id=agent_id,
            status="failed",
            invocation_id=invocation_id,
            stage=node_name,
            input_artifacts=input_versions,
            error=harness_error.model_dump(by_alias=True),
        )
        decision = self.recovery.decide_model_failure(graph_state, error)
        action = RecoveryActionRecord(
            action=_RECOVERY_ACTION_RECORD[decision.action],
            stage=harness_error.stage,
            reason=harness_error.message,
            retryable=harness_error.retryable,
            payload={
                "resumeNode": decision.resume_node,
                "allowReadOnly": decision.allow_read_only,
            },
        )
        recovered = self.observability.recovery_action(
            failed.state,
            action,
            agent_id=agent_id,
        )
        self._record_route_attempts(
            recovered.state,
            agent_id=agent_id,
            invocation_id=invocation_id,
            attempts=attempts,
            fallback_used=any(index > 0 for index, _ in enumerate(attempts)),
        )

    def _record_successful_model_routes(
        self,
        persistent: PersistentCognitiveState,
        *,
        agent_id: str,
        agent_name: str,
        invocation_id: str,
    ) -> None:
        decision = next(
            (
                item
                for item in reversed(self.artifact_runtime.list_decisions(persistent.session_id))
                if item.agent == agent_name and item.model_usage
            ),
            None,
        )
        if not decision or not decision.model_usage:
            return
        usage = decision.model_usage
        attempts = [item.model_dump(by_alias=True, exclude_none=True) for item in usage.attempts]
        if not attempts and usage.provider:
            attempts = [
                {
                    "provider": usage.provider,
                    "model": usage.model,
                    "status": "success",
                    "latencyMs": usage.latency_ms,
                }
            ]
        self._record_route_attempts(
            persistent,
            agent_id=agent_id,
            invocation_id=invocation_id,
            attempts=attempts,
            fallback_used=bool(usage.fallback_used),
        )

    def _record_route_attempts(
        self,
        persistent: PersistentCognitiveState,
        *,
        agent_id: str,
        invocation_id: str,
        attempts: list[dict[str, Any]],
        fallback_used: bool,
    ) -> None:
        current = persistent
        for index, attempt in enumerate(attempts, start=1):
            raw_status = str(attempt.get("status") or "error")
            status = raw_status if raw_status in {"success", "error", "skipped"} else "error"
            latency_value = attempt.get("latencyMs")
            if latency_value is None:
                latency_value = attempt.get("latency_ms")
            result = self.observability.model_routing(
                current,
                agent_id=agent_id,
                invocation_id=invocation_id,
                provider=str(attempt.get("provider") or ""),
                model=str(attempt.get("model") or ""),
                status=status,
                attempt=index,
                error_type=str(attempt.get("errorType") or attempt.get("error_type") or "") or None,
                latency_ms=int(latency_value) if latency_value is not None else None,
                fallback_used=fallback_used,
            )
            current = result.state


__all__ = ["CompiledHarnessGraph", "GraphBuilder", "HarnessRuntime"]
