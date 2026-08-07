from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta, timezone as fixed_timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ..harness import HarnessRuntime
from ..schemas import (
    PlanningExecutionFeedbackRequest,
    PlanningExecutionFeedbackResponse,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
)
from ..services.cognitive_planning.contracts import (
    CognitivePlanningMetadata,
    CognitivePlanningState,
    EvidencePack,
    ExecutionBlueprint,
    GoalAssumption,
    GoalCompletionResult,
    GoalModelingInput,
    MemoryHint,
    PlanCritiqueReport,
    RealityAssessment,
    RealityAssessmentInput,
    SafePlanningError,
    StrategyPortfolio,
    UserGoalModel,
)
from ..services.cognitive_planning.orchestration.persistence import json_object
from ..services.cognitive_planning.orchestration.runtime import PlanningRuntimeFoundation
from .agents import (
    CognitiveModelClient,
    CriticAgent,
    EvidenceAgent,
    ExecutionAgent,
    GoalCompletionJudge,
    GoalIntelligenceAgent,
    PlanningModelUnavailable,
    RealityAgent,
    StrategyAgent,
    extract_obvious_facts,
)
from .graph import build_planning_graph
from .memory import UserModelMemoryRepository
from .contracts.planning import (
    CalendarProposal,
    ConstraintSet,
    ContextPack,
    ExecutionOutcome,
    FinalApprovalBundle,
    LearningObservation,
    PlanBlueprint,
    QualityReport,
    RepairOperation,
    RepairProposal,
    ScheduleBlueprint,
    UnderstandingSnapshot,
    new_artifact_id,
)
from .planning_services import (
    CalendarMaterializer,
    ConstraintCompiler,
    ContextBuilder,
    ExecutionFeedbackService,
    FeedbackRouter,
    FinalApprovalService,
    PlanCompatibilityAdapter,
    PlanHardValidator,
    PatchGuard,
    ScheduleGenerator,
    ScheduleValidator,
    UnderstandingAdapter,
    UnderstandingReadinessService,
    quality_from_review,
)


_ACTIVE_SESSION_RUNS: dict[str, tuple[str, tuple[tuple[str, int], ...]]] = {}
_ACTIVE_SESSION_RUNS_LOCK = threading.Lock()


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


class CognitiveOSRuntime(PlanningRuntimeFoundation):
    """AI-native planning runtime. Rules protect; templates never decide."""

    engine_version = "planning-engine-2"

    def __init__(
        self,
        *,
        model_client: CognitiveModelClient | None = None,
        persistence=None,
        user_model: UserModelMemoryRepository | None = None,
    ):
        model = model_client or CognitiveModelClient()
        super().__init__(model_client=model, persistence=persistence)
        self.harness = HarnessRuntime()
        self.user_model = user_model or UserModelMemoryRepository()
        self.goal_agent = GoalIntelligenceAgent(model)
        self.goal_completion_judge = GoalCompletionJudge()
        self.reality_agent = RealityAgent(model)
        self.evidence_agent = EvidenceAgent(model=model, user_model=self.user_model)
        self.strategy_agent = StrategyAgent(model)
        self.execution_agent = ExecutionAgent(model)
        self.critic_agent = CriticAgent(model)
        self.hypotheses = self.user_model
        self.understanding_adapter = UnderstandingAdapter()
        self.understanding_readiness = UnderstandingReadinessService()
        self.constraint_compiler = ConstraintCompiler()
        self.context_builder = ContextBuilder()
        self.plan_adapter = PlanCompatibilityAdapter()
        self.plan_validator = PlanHardValidator()
        self.patch_guard = PatchGuard()
        self.schedule_generator = ScheduleGenerator()
        self.schedule_validator = ScheduleValidator()
        self.calendar_materializer = CalendarMaterializer()
        self.feedback_router = FeedbackRouter()
        self.final_approval_service = FinalApprovalService()
        self.execution_feedback_service = ExecutionFeedbackService()

    def _metadata(
        self,
        *,
        mode: str,
        stage: str,
        confidence: float | None = None,
        rules: list[str] | None = None,
        repair_count: int = 0,
    ) -> CognitivePlanningMetadata:
        return CognitivePlanningMetadata(
            engineVersion=self.engine_version,
            planningMode=mode,
            currentStage=stage,
            agentConfidence=confidence,
            appliedUserRules=rules or [],
            repairCount=repair_count,
        )

    def _state_from_row(self, row, *, action: str, user_input: str = "") -> CognitivePlanningState:
        state = super()._state_from_row(row, action=action, user_input=user_input)
        if "reality_assessment_json" in row.keys():
            raw = json_object(row["reality_assessment_json"])
            if raw:
                state["reality_assessment"] = RealityAssessment.model_validate(raw)
        state = self.harness.restore_graph_state(state)
        latest: dict[str, Any] = {}
        for artifact in self.agent_runtime.list_artifacts(row["id"]):
            latest[artifact.artifact_type] = artifact.content_json
        planning_contracts = {
            "understanding_snapshot": ("understanding_snapshot", UnderstandingSnapshot),
            "constraint_set": ("constraint_set", ConstraintSet),
            "context_pack": ("context_pack", ContextPack),
            "plan_blueprint": ("plan_blueprint", PlanBlueprint),
            "plan_quality_report": ("plan_quality_report", QualityReport),
            "schedule_blueprint": ("schedule_blueprint", ScheduleBlueprint),
            "schedule_quality_report": ("schedule_quality_report", QualityReport),
            "calendar_proposal": ("calendar_proposal", CalendarProposal),
            "final_approval_bundle": ("final_approval_bundle", FinalApprovalBundle),
        }
        for artifact_type, (state_key, contract) in planning_contracts.items():
            raw = latest.get(artifact_type)
            if raw:
                state[state_key] = contract.model_validate(raw)
        # The formal flow stores the same model-backed artifacts through the Harness. When
        # a user returns with final-review feedback, the legacy feedback
        # learner still declares these typed inputs in its Agent contract.
        # Restore them from the artifact heads as well, so the Harness can
        # validate the real inputs instead of treating a persisted session
        # as incomplete.
        feedback_contracts = {
            "user_goal_model": ("goal_model", UserGoalModel),
            "evidence_pack": ("evidence_pack", EvidencePack),
            "strategy_portfolio": ("strategy_portfolio", StrategyPortfolio),
            "execution_blueprint": ("execution_blueprint", ExecutionBlueprint),
            "critique_report": ("critique_report", PlanCritiqueReport),
        }
        for artifact_type, (state_key, contract) in feedback_contracts.items():
            raw = latest.get(artifact_type)
            # Prefer the compatibility projection when it is present: it
            # carries the persisted internally selected approach, while the
            # immutable artifact head is intentionally still a draft.
            if raw and state_key not in state:
                state[state_key] = contract.model_validate(raw)
        metadata = json_object(row["cognitive_metadata_json"])
        recorded_engine = str(metadata.get("engineVersion") or "")
        state["planning_flow_version"] = "formal"
        state["planning_engine_version"] = recorded_engine or self.engine_version
        # Authority migration is a safety gate, so it has higher priority than
        # an older Harness checkpoint (for example pending_agent=strategy).
        if state.get("evidence_requires_authority_refresh"):
            state["user_action"] = "continue_current_stage"
            state["business_status"] = "planning"
            state["runtime_status"] = "running"
            state["resume_node"] = "evidence"
        return state

    def _invoke(self, state: CognitivePlanningState) -> PlanningSessionResponse:
        session_id = str(state["session_id"])
        with _ACTIVE_SESSION_RUNS_LOCK:
            if session_id in _ACTIVE_SESSION_RUNS:
                return self.get_session(session_id)
            persistent = self.harness.bootstrap(state)
            run_key = (
                str(persistent.current_stage),
                tuple(sorted((str(key), int(value)) for key, value in persistent.artifact_versions.items())),
            )
            _ACTIVE_SESSION_RUNS[session_id] = run_key
        try:
            result = self.harness.invoke(
                adapter=self,
                graph_builder=build_planning_graph,
                state=state,
            )
            return self.get_session(str(result.get("session_id") or session_id))
        finally:
            with _ACTIVE_SESSION_RUNS_LOCK:
                if _ACTIVE_SESSION_RUNS.get(session_id) == run_key:
                    _ACTIVE_SESSION_RUNS.pop(session_id, None)

    def _record_planning_artifact(
        self,
        state: CognitivePlanningState,
        *,
        agent: str,
        artifact_type: str,
        artifact: Any,
        decision: str,
        reason: str,
        summary: str,
        status: str = "draft",
        inputs: tuple[str, ...] = (),
    ) -> str:
        return self._record_artifact(
            state,
            agent=agent,
            artifact_type=artifact_type,
            artifact=artifact,
            model_usage={},
            decision=decision,
            reason=reason,
            summary=summary,
            status=status,
            input_artifact_types=inputs,
        )

    def understanding_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        previous = state.get("understanding_snapshot")
        state = self.semantic_understanding_node(state)
        if state.get("status") == "MODEL_UNAVAILABLE" or not state.get("goal_model"):
            return state
        goal = state["goal_model"]
        question_rounds = int(previous.readiness.question_rounds_used if previous else 0)
        if goal.questions:
            question_rounds += 1
        snapshot = self.understanding_adapter.from_goal_model(
            goal,
            previous=previous,
            source_ref=f"conversation-turn:{len(state.get('conversation_history', []))}",
            question_rounds_used=question_rounds,
        )
        if previous:
            # A revised understanding is the new authority.  Do not let a
            # prior Reality/Evidence decision keep the user in a stale
            # clarification state; those stages are recomputed after the new
            # Understanding confirmation.
            for key in (
                "reality_assessment",
                "evidence_pack",
                "strategy_portfolio",
                "execution_blueprint",
                "critique_report",
            ):
                state.pop(key, None)
            self.persistence.update(
                state["session_id"],
                clear=(
                    "reality_assessment",
                    "evidence_pack",
                    "strategy_portfolio",
                    "execution_blueprint",
                    "critique_report",
                ),
            )
        state["understanding_snapshot"] = snapshot
        state["understanding_updated"] = True
        self._record_planning_artifact(
            state,
            agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            artifact=snapshot,
            decision="produce_artifact",
            reason="The current user turn was merged into one typed semantic understanding.",
            summary="Understanding was updated without changing the approved meaning of prior facts.",
            inputs=("user_goal_model", "understanding_snapshot"),
        )
        return state

    def understanding_readiness_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        if not snapshot:
            return state
        state = self.readiness_judgment_node(state)
        if state.get("status") == "MODEL_UNAVAILABLE":
            return state
        goal = state.get("goal_model")
        blocking_keys = [
            item.key
            for item in (goal.decision_relevant_unknowns if goal else [])
            if item.priority == "blocking"
        ]
        snapshot = self.understanding_readiness.assess(
            snapshot,
            blocking_unknown_keys=blocking_keys,
        )
        state["understanding_snapshot"] = snapshot
        self._record_planning_artifact(
            state,
            agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            artifact=snapshot,
            decision="request_user_input",
            reason=(
                "; ".join(snapshot.readiness.blocking_reasons)
                or "The semantic goal is ready for one explicit confirmation."
            ),
            summary="Understanding readiness was evaluated with a bounded dynamic question budget.",
            status="approved" if snapshot.readiness.confirmed else "draft",
            inputs=("understanding_snapshot", "goal_completion"),
        )
        return state

    def wait_for_understanding_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        needs_more_input = bool(
            not snapshot
            or not snapshot.readiness.ready_for_confirmation
            or (
                not state.get("understanding_updated")
                and state.get("reality_assessment")
                and not state["reality_assessment"].can_proceed_to_evidence
            )
            or (
                not state.get("understanding_updated")
                and state.get("evidence_pack")
                and not state["evidence_pack"].can_proceed_to_strategy
            )
        )
        status = "needs_goal_clarification" if needs_more_input else "waiting_understanding_confirmation"
        state.update(
            status=status,
            business_status="goal_clarification" if needs_more_input else "goal_understood",
            runtime_status="idle",
        )
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=state["business_status"],
            runtime_status="idle",
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="understanding_confirmation",
                confidence=state.get("goal_model").confidence if state.get("goal_model") else None,
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def compile_constraints_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        if not snapshot or not snapshot.readiness.confirmed:
            raise HTTPException(status_code=409, detail={"message": "understanding is not confirmed"})
        constraints = self.constraint_compiler.compile(snapshot)
        state["constraint_set"] = constraints
        self._record_planning_artifact(
            state,
            agent="Constraint Compiler",
            artifact_type="constraint_set",
            artifact=constraints,
            decision="produce_artifact",
            reason="Confirmed semantic constraints were compiled into deterministic planning inputs.",
            summary="Constraints are now version-bound to the confirmed understanding.",
            status="approved",
            inputs=("understanding_snapshot",),
        )
        return state

    def build_context_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        if not snapshot or not constraints:
            return state
        request_context = state.get("request_context", {})
        context = self.context_builder.build(
            snapshot,
            constraints,
            reality=state.get("reality_assessment"),
            evidence=state.get("evidence_pack"),
            calendar_snapshot_ref=str(request_context.get("calendarSnapshotRef") or "calendar:1"),
        )
        state["context_pack"] = context
        self._record_planning_artifact(
            state,
            agent="Context Builder",
            artifact_type="context_pack",
            artifact=context,
            decision="produce_artifact",
            reason="Evidence and reality claims were normalized with explicit provenance.",
            summary="The plan context contains only traceable claims and current Calendar identity.",
            status="approved",
            inputs=("understanding_snapshot", "constraint_set", "reality_assessment", "evidence_pack"),
        )
        return state

    def select_approach_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        portfolio = state.get("strategy_portfolio")
        if not portfolio:
            return state
        selected = portfolio.model_copy(
            update={
                "approved_strategy_id": portfolio.recommended_strategy_id,
                "status": "approved",
            }
        )
        state["strategy_portfolio"] = selected
        state["approved_strategy_id"] = selected.recommended_strategy_id
        state["runtime_status"] = "running"
        self.persistence.update(
            state["session_id"],
            approved_strategy_id=selected.recommended_strategy_id,
            strategy_portfolio=selected,
            runtime_status="running",
        )
        self.agent_runtime.record_decision(
            state["session_id"],
            agent="Plan Generator",
            decision="produce_artifact",
            reason="The formal flow has one final approval phase; the recommended option is an internal plan input, not a user approval.",
            summary="The recommended strategy was selected for plan generation and remains subject to final review.",
            confidence=1,
            input_artifact_ids=self._latest_artifact_ids(state["session_id"], ("strategy_portfolio",)),
        )
        return state

    def validate_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        required = (
            state.get("understanding_snapshot"),
            state.get("constraint_set"),
            state.get("context_pack"),
            state.get("strategy_portfolio"),
            state.get("execution_blueprint"),
        )
        if not all(required):
            return state
        snapshot, constraints, context, strategy, execution = required
        plan = self.plan_adapter.from_artifacts(
            snapshot=snapshot,
            constraints=constraints,
            context=context,
            strategy=strategy,
            execution=execution,
        )
        quality = self.plan_validator.validate(
            plan,
            snapshot=snapshot,
            constraints=constraints,
            context=context,
            repair_round=int(state.get("repair_count", 0)),
        )
        state["plan_blueprint"] = plan
        state["plan_quality_report"] = quality
        self._record_planning_artifact(
            state,
            agent="Plan Generator",
            artifact_type="plan_blueprint",
            artifact=plan,
            decision="produce_artifact",
            reason="The selected strategy and Execution artifact were projected into one canonical PlanBlueprint.",
            summary="The canonical plan preserves task identity, dependencies, effort, evidence, and fallbacks.",
            inputs=("understanding_snapshot", "constraint_set", "context_pack", "strategy_portfolio", "execution_blueprint"),
        )
        self._record_planning_artifact(
            state,
            agent="Plan Quality Reviewer",
            artifact_type="plan_quality_report",
            artifact=quality,
            decision="approve" if quality.passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "All deterministic plan rules passed.",
            summary="Plan hard rules were evaluated before semantic review.",
            status="approved" if quality.passed else "needs_revision",
            inputs=("plan_blueprint",),
        )
        return state

    def prepare_plan_repair_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        quality = state.get("plan_quality_report")
        if not quality or quality.passed:
            return state
        count = min(2, int(state.get("repair_count", 0)) + 1)
        state["repair_count"] = count
        state["repair_loop"] = True
        state["repair_instructions"] = [
            {
                "instruction": issue.description,
                "expectedChange": f"Resolve {issue.rule_id} for {issue.target_id} without changing confirmed goal semantics.",
            }
            for issue in quality.issues
            if issue.severity in {"blocker", "major"}
        ]
        plan = state.get("plan_blueprint")
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        repaired = False
        if plan and snapshot and constraints and context:
            for issue in list(quality.issues):
                if issue.rule_id != "provenance" or "replace_resource" not in issue.allowed_operations:
                    continue
                proposal = RepairProposal(
                    artifactId=plan.artifact_id,
                    artifactVersion=plan.version,
                    issueId=issue.issue_id,
                    operations=[
                        RepairOperation(
                            operation="replace_resource",
                            targetId=issue.target_id,
                            payload={"resourceRefs": []},
                        )
                    ],
                )
                candidate, result = self.patch_guard.apply_plan(
                    plan,
                    proposal,
                    issue,
                    validator=self.plan_validator,
                    snapshot=snapshot,
                    constraints=constraints,
                    context=context,
                )
                if result.accepted:
                    plan = candidate
                    repaired = True
            if repaired:
                quality = self.plan_validator.validate(
                    plan,
                    snapshot=snapshot,
                    constraints=constraints,
                    context=context,
                    repair_round=count,
                )
                state["plan_blueprint"] = plan
                state["plan_quality_report"] = quality
                self._record_planning_artifact(
                    state,
                    agent="Plan Generator",
                    artifact_type="plan_blueprint",
                    artifact=plan,
                    decision="produce_artifact",
                    reason="Unverified resource references were removed through issue-scoped domain operations.",
                    summary="The repair preserved task identity and plan semantics and passed regression validation.",
                    inputs=("plan_blueprint", "plan_quality_report", "context_pack"),
                )
        self.persistence.update(state["session_id"], repair_count=count, runtime_status="running")
        return state

    def review_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        critic = state.get("critique_report")
        hard = state.get("plan_quality_report")
        if not plan or not critic or not hard:
            return state
        semantic = quality_from_review(critic, plan)
        quality = semantic.model_copy(
            update={
                "hard_rules_passed": hard.hard_rules_passed,
                "issues": [*hard.issues, *semantic.issues],
                "repair_round": int(state.get("repair_count", 0)),
            }
        )
        state["plan_quality_report"] = quality
        self._record_planning_artifact(
            state,
            agent="Plan Quality Reviewer",
            artifact_type="plan_quality_report",
            artifact=quality,
            decision="approve" if quality.passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "Hard and semantic reviews passed.",
            summary="Deterministic and independent semantic review results were combined without hiding remaining risk.",
            status="approved" if quality.passed else "needs_revision",
            inputs=("plan_blueprint", "critique_report"),
        )
        return state

    def generate_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not plan or not constraints or not context:
            return state
        timezone = str(state.get("request_context", {}).get("timezone") or "Asia/Shanghai")
        try:
            zone = ZoneInfo(timezone)
        except Exception:
            # Windows Python installations may not ship the IANA database.
            # Planix's supported default remains timezone-aware without adding tzdata.
            zone = fixed_timezone(timedelta(hours=8)) if timezone == "Asia/Shanghai" else fixed_timezone.utc
        start_date = constraints.core.required_start_date
        start = datetime.combine(
            date.fromisoformat(start_date) if start_date else date.today() + timedelta(days=1),
            datetime.min.time().replace(hour=9),
            tzinfo=zone,
        )
        schedule = self.schedule_generator.generate(
            plan,
            constraints,
            start=start,
            timezone=timezone,
            calendar_snapshot_ref=context.calendar_snapshot_ref,
        )
        state["schedule_blueprint"] = schedule
        self._record_planning_artifact(
            state,
            agent="Schedule Agent",
            artifact_type="schedule_blueprint",
            artifact=schedule,
            decision="produce_artifact",
            reason="Tasks were topologically ordered and split into bounded sessions.",
            summary="A timezone-aware schedule was generated from the canonical plan and constraints.",
            inputs=("plan_blueprint", "constraint_set", "context_pack"),
        )
        return state

    def validate_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        schedule = state.get("schedule_blueprint")
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not schedule or not plan or not constraints or not context:
            return state
        quality = self.schedule_validator.validate(
            schedule,
            plan=plan,
            constraints=constraints,
            current_calendar_snapshot_ref=context.calendar_snapshot_ref,
        )
        state["schedule_quality_report"] = quality
        self._record_planning_artifact(
            state,
            agent="Schedule Quality Reviewer",
            artifact_type="schedule_quality_report",
            artifact=quality,
            decision="approve" if quality.passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "All deterministic schedule rules passed.",
            summary="Schedule feasibility, conflicts, capacity, dependencies, and timezone invariants were checked.",
            status="approved" if quality.passed else "needs_revision",
            inputs=("schedule_blueprint", "plan_blueprint", "constraint_set"),
        )
        return state

    def repair_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        schedule = state.get("schedule_blueprint")
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not schedule or not plan or not constraints or not context:
            return state
        repair_count = int(state.get("schedule_repair_count", 0))
        if repair_count >= 2:
            return state
        if schedule.sessions:
            start = datetime.fromisoformat(schedule.sessions[0].start)
        else:
            start = datetime.now(fixed_timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
        repaired = self.schedule_generator.generate(
            plan,
            constraints,
            start=start,
            timezone=schedule.planning_timezone,
            calendar_snapshot_ref=context.calendar_snapshot_ref,
        ).model_copy(update={"version": schedule.version + 1})
        state["schedule_blueprint"] = repaired
        state["schedule_repair_count"] = repair_count + 1
        self._record_planning_artifact(
            state,
            agent="Schedule Agent",
            artifact_type="schedule_blueprint",
            artifact=repaired,
            decision="produce_artifact",
            reason="The schedule was regenerated within the deterministic two-round repair budget.",
            summary="Only session timing changed; task identity, deliverables, and total effort were preserved.",
            inputs=("schedule_blueprint", "schedule_quality_report", "plan_blueprint", "constraint_set"),
        )
        return state

    def materialize_calendar_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        schedule = state.get("schedule_blueprint")
        context = state.get("context_pack")
        if not plan or not schedule or not context:
            return state
        proposal = self.calendar_materializer.materialize(
            plan,
            schedule,
            timezone=schedule.planning_timezone,
            current_calendar_snapshot_ref=context.calendar_snapshot_ref,
        )
        state["calendar_proposal"] = proposal
        self._record_planning_artifact(
            state,
            agent="Calendar Materializer",
            artifact_type="calendar_proposal",
            artifact=proposal,
            decision="produce_artifact",
            reason="Calendar rows were materialized deterministically from validated schedule sessions.",
            summary="Every proposed Calendar event has stable source lineage and no AI-side database write occurred.",
            status="approved",
            inputs=("plan_blueprint", "schedule_blueprint", "schedule_quality_report"),
        )
        return state

    def wait_for_final_review_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan_quality = state.get("plan_quality_report")
        schedule_quality = state.get("schedule_quality_report")
        ready = bool(plan_quality and plan_quality.passed and schedule_quality and schedule_quality.passed)
        status = "waiting_final_review" if ready else "final_revision"
        state.update(status=status, business_status="calendar_pending" if ready else "planning", runtime_status="idle")
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=state["business_status"],
            runtime_status="idle",
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="final_review",
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def feedback_router_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        route = self.feedback_router.route(state.get("user_input", ""))
        state["feedback_route"] = route
        state["next_node"] = {
            "understanding_change": "understanding",
            "plan_change": "record_learning",
            "resource_change": "record_learning",
            "schedule_change": "generate_schedule",
            "presentation_change": "wait_for_final_review",
            "approve": "wait_for_final_review",
            "reject": "wait_for_final_review",
        }[route.category]
        self.agent_runtime.record_decision(
            state["session_id"],
            agent="Final Review Controller",
            decision="handoff",
            reason=route.reason,
            summary="Final-review feedback was routed to the smallest responsible planning stage.",
            confidence=route.confidence,
        )
        return state

    def _block_model(
        self,
        state: CognitivePlanningState,
        *,
        agent: str,
        error: SafePlanningError,
    ) -> CognitivePlanningState:
        recovery = self.harness.decide_model_failure(state, error)
        business_status = recovery.business_status
        state["planning_mode"] = recovery.planning_mode
        state["errors"] = [*state.get("errors", []), error]
        state["status"] = recovery.compatibility_status
        state["business_status"] = business_status
        state["runtime_status"] = recovery.runtime_status
        state["resume_node"] = recovery.resume_node
        self.agent_runtime.record_message(
            state["session_id"],
            from_agent=agent,
            to_agent=agent,
            message_type="block",
            reason=error.message,
            payload={
                "errorType": error.error_type,
                "retryable": error.retryable,
                "attempts": error.attempts,
                "resumeNode": state["resume_node"],
                "recoveryAction": recovery.action.value,
                "allowReadOnly": recovery.allow_read_only,
            },
            resolved=False,
        )
        self.persistence.update(
            state["session_id"],
            status=recovery.compatibility_status,
            business_status=business_status,
            runtime_status=recovery.runtime_status,
            cognitive_metadata=self._metadata(
                mode=recovery.planning_mode,
                stage=error.stage,
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def _is_formal_session(self, row) -> bool:
        metadata = json_object(row["cognitive_metadata_json"])
        if str(metadata.get("engineVersion") or "") in {self.engine_version, "cognitive-os-v2"}:
            return True
        return any(
            item.artifact_type == "understanding_snapshot"
            for item in self.agent_runtime.list_artifacts(row["id"])
        )

    def _planning_storage_versions(self, session_id: str, *, calendar_snapshot_version: int) -> dict[str, int]:
        artifact_names = {
            "understanding_snapshot": "understanding",
            "constraint_set": "constraint",
            "context_pack": "context",
            "plan_blueprint": "plan",
            "plan_quality_report": "quality_report",
            "schedule_blueprint": "schedule",
            "calendar_proposal": "calendar_proposal",
        }
        versions: dict[str, int] = {}
        for artifact in self.agent_runtime.list_artifacts(session_id):
            target = artifact_names.get(artifact.artifact_type)
            if target:
                versions[target] = max(versions.get(target, 0), artifact.version)
        persistent = self.harness.bootstrap({"session_id": session_id})
        versions["calendar_snapshot"] = calendar_snapshot_version
        # Bind to the artifact checkpoint, not the observability event counter;
        # recording the subsequent Calendar permission must not stale itself.
        checkpoint_kinds = {
            "user_goal_model",
            "goal_completion",
            "reality_assessment",
            "evidence_pack",
            "strategy_portfolio",
            "execution_blueprint",
            "critique_report",
            "understanding_snapshot",
            "constraint_set",
            "context_pack",
            "plan_blueprint",
            "plan_quality_report",
            "schedule_blueprint",
            "schedule_quality_report",
            "calendar_proposal",
        }
        versions["checkpoint"] = sum(
            version
            for kind, version in persistent.artifact_versions.items()
            if kind in checkpoint_kinds
        )
        return versions

    def confirm_understanding(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        if not self._is_formal_session(row):
            raise HTTPException(status_code=410, detail={"message": "Archived planning sessions cannot be resumed; create a new planning session."})
        state = self._state_from_row(row, action="confirm_understanding")
        snapshot = state.get("understanding_snapshot")
        if row["status"] != "waiting_understanding_confirmation" or not snapshot:
            raise HTTPException(status_code=409, detail={"message": "understanding is not waiting for confirmation"})
        if not snapshot.readiness.ready_for_confirmation:
            raise HTTPException(status_code=409, detail={"message": "understanding still has blocking unknowns"})
        confirmed = snapshot.model_copy(
            update={
                "artifact_id": new_artifact_id("understanding"),
                "version": snapshot.version + 1,
                "readiness": snapshot.readiness.model_copy(update={"confirmed": True}),
            }
        )
        state["understanding_snapshot"] = confirmed
        self._record_planning_artifact(
            state,
            agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            artifact=confirmed,
            decision="approve",
            reason="The user explicitly confirmed the complete UnderstandingSnapshot.",
            summary="Understanding is frozen as the authority for plan generation.",
            status="approved",
            inputs=("understanding_snapshot",),
        )
        self.persistence.update(session_id, runtime_status="running", business_status="goal_understood")
        return self._invoke(state)

    def revise_understanding(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if row and self._is_formal_session(row):
            return self.clarify(session_id, payload)
        raise HTTPException(status_code=410, detail={"message": "Archived planning sessions cannot be revised; create a new planning session."})

    def approve_final(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if row and self._is_formal_session(row):
            return self._prepare_final_approval(
                session_id,
                accept_missing_resources=accept_missing_resources,
            )
        raise HTTPException(status_code=410, detail={"message": "Archived planning sessions cannot be approved; create a new planning session."})

    def revise_final(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        if not self._is_formal_session(row):
            raise HTTPException(status_code=410, detail={"message": "Archived planning sessions cannot be revised; create a new planning session."})
        self.persistence.append_user_turn(session_id, payload.text)
        row = self.persistence.get_row(session_id)
        state = self._state_from_row(row, action="give_feedback", user_input=payload.text)
        state["runtime_status"] = "running"
        self.persistence.update(session_id, runtime_status="running")
        return self._invoke(state)

    def _prepare_final_approval(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        if not self._is_formal_session(row):
            raise HTTPException(status_code=410, detail={"message": "Archived planning sessions cannot be written; create a new planning session."})
        if row["status"] != "waiting_final_review":
            raise HTTPException(status_code=409, detail={"message": "final plan is not ready for approval"})
        state = self._state_from_row(row, action="write_calendar")
        plan_quality = state.get("plan_quality_report")
        schedule_quality = state.get("schedule_quality_report")
        if not plan_quality or not plan_quality.passed or not schedule_quality or not schedule_quality.passed:
            raise HTTPException(status_code=409, detail={"message": "final plan quality gates have not passed"})
        snapshot_version = int(state.get("request_context", {}).get("calendarSnapshotVersion") or 1)
        versions = self._planning_storage_versions(
            session_id,
            calendar_snapshot_version=snapshot_version,
        )
        required = {
            "understanding",
            "constraint",
            "context",
            "plan",
            "quality_report",
            "schedule",
            "calendar_proposal",
        }
        if required - versions.keys():
            raise HTTPException(status_code=409, detail={"message": "final approval artifacts are incomplete"})
        approval = self.final_approval_service.create(
            session_id=session_id,
            understanding_version=versions["understanding"],
            constraint_version=versions["constraint"],
            context_version=versions["context"],
            plan_version=versions["plan"],
            quality_report_version=versions["quality_report"],
            schedule_version=versions["schedule"],
            calendar_proposal_version=versions["calendar_proposal"],
            calendar_snapshot_version=versions["calendar_snapshot"],
            checkpoint_version=versions["checkpoint"],
        )
        state["final_approval_bundle"] = approval
        self._record_planning_artifact(
            state,
            agent="Final Review Controller",
            artifact_type="final_approval_bundle",
            artifact=approval,
            decision="approve",
            reason="The user approved the complete version-bound final review bundle.",
            summary="Final approval binds Understanding, Plan, Quality, Schedule, Calendar snapshot, and checkpoint versions.",
            status="approved",
            inputs=(
                "understanding_snapshot",
                "constraint_set",
                "context_pack",
                "plan_blueprint",
                "plan_quality_report",
                "schedule_blueprint",
                "calendar_proposal",
            ),
        )
        return self._invoke(state)

    def _assert_final_approval(self, session_id: str) -> None:
        row = self.persistence.get_row(session_id)
        if not row or not self._is_formal_session(row):
            raise HTTPException(status_code=410, detail={"message": "Archived planning sessions have no valid final approval."})
        state = self._state_from_row(row, action="continue_current_stage")
        approval = state.get("final_approval_bundle")
        if not approval:
            raise HTTPException(status_code=409, detail={"message": "final approval is missing"})
        versions = self._planning_storage_versions(
            session_id,
            calendar_snapshot_version=int(state.get("request_context", {}).get("calendarSnapshotVersion") or 1),
        )
        try:
            self.final_approval_service.assert_current(approval, versions)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc

    def approve_calendar_write(
        self,
        session_id: str,
        *,
        execution_artifact_ref: dict | None = None,
    ) -> None:
        self._assert_final_approval(session_id)
        super().approve_calendar_write(
            session_id,
            execution_artifact_ref=execution_artifact_ref,
        )

    def assert_calendar_write_allowed(
        self,
        session_id: str,
        *,
        execution_artifact_ref: dict | None = None,
    ) -> None:
        self._assert_final_approval(session_id)
        super().assert_calendar_write_allowed(
            session_id,
            execution_artifact_ref=execution_artifact_ref,
        )

    def mark_calendar_written(
        self,
        session_id: str,
        *,
        execution_artifact_ref: dict | None = None,
    ) -> None:
        row = self.persistence.get_row(session_id)
        is_formal = bool(row and self._is_formal_session(row))
        state = self._state_from_row(row, action="continue_current_stage") if is_formal else None
        approval = state.get("final_approval_bundle") if state else None
        super().mark_calendar_written(
            session_id,
            execution_artifact_ref=execution_artifact_ref,
        )
        if approval:
            consumed = approval.model_copy(update={"consumed": True})
            state["final_approval_bundle"] = consumed
            self._record_planning_artifact(
                state,
                agent="Final Review Controller",
                artifact_type="final_approval_bundle",
                artifact=consumed,
                decision="approve",
                reason="The approved Calendar mutation completed successfully.",
                summary="The one-time final approval was consumed after Calendar write.",
                status="approved",
                inputs=("final_approval_bundle",),
            )

    def execution_to_structured_plan(self, session: PlanningSessionResponse) -> dict[str, Any]:
        if not session.plan_blueprint or not session.calendar_proposal:
            return super().execution_to_structured_plan(session)
        plan = PlanBlueprint.model_validate(session.plan_blueprint)
        proposal = CalendarProposal.model_validate(session.calendar_proposal)
        tasks = [
            {
                "title": event.title,
                "description": event.description,
                "estimatedMinutes": max(
                    1,
                    int(
                        (datetime.fromisoformat(event.end) - datetime.fromisoformat(event.start)).total_seconds()
                        // 60
                    ),
                ),
                "dueDate": datetime.fromisoformat(event.start).date().isoformat(),
                "priority": "medium",
                "sourceKey": event.source_key,
            }
            for event in proposal.events
        ]
        dates = [date.fromisoformat(task["dueDate"]) for task in tasks]
        duration = max(1, (max(dates) - min(dates)).days + 1) if dates else 1
        return {
            "goalTitle": plan.goal_summary,
            "goalDescription": plan.goal_summary,
            "durationDays": duration,
            "milestones": [
                {
                    "title": milestone.title,
                    "description": milestone.purpose,
                    "tasks": [
                        task
                        for task, event in zip(tasks, proposal.events)
                        if next(
                            (item.milestone_id for item in plan.tasks if item.id == event.source_task_id),
                            None,
                        )
                        == milestone.id
                    ],
                }
                for milestone in plan.milestones
            ],
            "reviewPlan": {
                "frequency": "weekly",
                "questions": ["What was completed?", "What is blocked?", "What should be replanned?"],
            },
        }

    def record_execution_feedback(
        self,
        session_id: str,
        payload: PlanningExecutionFeedbackRequest,
    ) -> PlanningExecutionFeedbackResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        if not self._is_formal_session(row):
            raise HTTPException(status_code=409, detail={"message": "execution feedback requires a formal planning session"})
        state = self._state_from_row(row, action="continue_current_stage")
        plan = state.get("plan_blueprint")
        task = next((item for item in plan.tasks if item.id == payload.task_id), None) if plan else None
        if not task:
            raise HTTPException(status_code=404, detail={"message": "plan task not found"})
        try:
            outcome = self.execution_feedback_service.record(
                task=task,
                status=payload.status,
                actual_minutes=payload.actual_minutes,
                completion_evidence=payload.completion_evidence,
                blocker_reason=payload.blocker_reason,
                failure_reason=payload.failure_reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc
        outcome_artifact_id = self._record_planning_artifact(
            state,
            agent="Execution Feedback Evaluator",
            artifact_type="execution_outcome",
            artifact=outcome,
            decision="produce_artifact",
            reason=f"Task {task.id} reported execution status {outcome.status}.",
            summary="Execution outcome was recorded separately from its Calendar projection.",
            status="approved",
            inputs=("plan_blueprint",),
        )
        outcomes: dict[str, ExecutionOutcome] = {}
        for artifact in self.agent_runtime.list_artifacts(session_id):
            if artifact.artifact_type == "execution_outcome":
                item = ExecutionOutcome.model_validate(artifact.content_json)
                outcomes[item.task_id] = item
        replan = self.execution_feedback_service.propose_replan(
            session_id=session_id,
            outcomes=list(outcomes.values()),
        )
        if replan:
            self._record_planning_artifact(
                state,
                agent="Execution Feedback Evaluator",
                artifact_type="replan_proposal",
                artifact=replan,
                decision="request_user_input",
                reason=replan.reason,
                summary="Execution deviation requires Final Review; Calendar was not changed automatically.",
                status="draft",
                inputs=("execution_outcome", "plan_blueprint", "schedule_blueprint"),
            )
        ratio = (
            round(payload.actual_minutes / task.effort_estimate.expected_minutes, 3)
            if payload.actual_minutes is not None
            else None
        )
        observation = LearningObservation(
            sessionId=session_id,
            category="duration" if ratio is not None else "execution_status",
            statement=(
                f"Task {task.id} actual/estimated duration ratio was {ratio}."
                if ratio is not None
                else f"Task {task.id} execution status was {payload.status}."
            ),
            sourceRefs=[outcome_artifact_id],
        )
        self._record_planning_artifact(
            state,
            agent="Learning Observer",
            artifact_type="learning_observation",
            artifact=observation,
            decision="produce_artifact",
            reason="A typed observation was recorded without directly changing user-level parameters or memory.",
            summary="Learning remains evidence-only until repeated observations pass evaluation and promotion policy.",
            status="draft",
            inputs=("execution_outcome",),
        )
        return PlanningExecutionFeedbackResponse(
            outcome=outcome.model_dump(by_alias=True),
            replanProposal=replan.model_dump(by_alias=True) if replan else None,
            learningObservation=observation.model_dump(by_alias=True),
        )

    def skip_current_stage(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        state = self._state_from_row(row, action="skip_current_stage")
        goal = state.get("goal_model")
        completion = state.get("goal_completion")
        if (
            row["status"] != "needs_goal_clarification"
            or state.get("business_status") != "goal_clarification"
            or not goal
            or not completion
            or completion.complete
        ):
            raise HTTPException(
                status_code=409,
                detail={"message": "only an incomplete goal clarification step can be skipped"},
            )

        critical_unknowns = [
            item
            for item in goal.decision_relevant_unknowns
            if item.priority == "blocking" and item.impact in {"safety", "feasibility"}
        ]
        if goal.consistency_warnings or critical_unknowns:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "goal consistency, safety, and feasibility blockers must be resolved before planning can continue"
                    )
                },
            )

        skipped_unknowns = [
            item for item in goal.decision_relevant_unknowns if item.priority == "blocking"
        ]
        if not skipped_unknowns:
            raise HTTPException(
                status_code=409,
                detail={"message": "there are no ordinary goal-clarification blockers to skip"},
            )

        existing_assumptions = {item.statement.casefold() for item in goal.assumptions}
        assumptions = list(goal.assumptions)
        for item in skipped_unknowns:
            if item.description.casefold() in existing_assumptions:
                continue
            assumptions.append(
                GoalAssumption(
                    statement=item.description,
                    confidence=0.4,
                    needsUserConfirmation=False,
                )
            )
            existing_assumptions.add(item.description.casefold())

        goal = goal.model_copy(
            update={
                "decision_relevant_unknowns": [
                    item.model_copy(update={"priority": "optional"})
                    if item.priority == "blocking"
                    else item
                    for item in goal.decision_relevant_unknowns
                ],
                "assumptions": assumptions,
                # The explicit control accepts ordinary unresolved Goal-stage
                # information as best-effort assumptions. Its text remains
                # auditable below and in optionalUnknowns, but it is no longer
                # an unresolved uncertainty when Reality revalidates the Goal.
                "uncertainties": [],
                "questions": [],
                "can_proceed_to_evidence": True,
            }
        )
        skipped_questions = [item.question for item in completion.blocking_unknowns]
        completion = GoalCompletionResult(
            complete=True,
            blockingUnknowns=[],
            optionalUnknowns=_dedupe_text(
                [
                    *completion.optional_unknowns,
                    *(item.description for item in skipped_unknowns),
                    *skipped_questions,
                ]
            ),
            nextStage="strategy",
        )
        state["goal_model"] = goal
        state["goal_completion"] = completion
        state["business_status"] = "goal_understood"
        state["runtime_status"] = "running"
        state["planning_mode"] = "model_backed"
        self._record_artifact(
            state,
            agent=self.goal_completion_judge.name,
            artifact_type=self.goal_completion_judge.artifact_type,
            artifact=completion,
            model_usage={},
            decision="approve",
            reason="The user explicitly skipped ordinary goal clarification and accepted best-effort assumptions.",
            summary="Goal clarification was skipped using the saved goal and known facts; planning may continue.",
            status="approved",
            input_artifact_types=("user_goal_model", "goal_completion"),
        )
        self.persistence.update(
            session_id,
            business_status="goal_understood",
            runtime_status="running",
            goal_model=goal,
            goal_completion=completion,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_completion",
                confidence=goal.confidence,
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=(
                "reality_assessment",
                "evidence_pack",
                "strategy_portfolio",
                "execution_blueprint",
                "critique_report",
            ),
        )
        self._handoff(
            state,
            self.goal_completion_judge.name,
            self.reality_agent.name,
            "The user accepted best-effort assumptions and asked planning to continue with saved context.",
        )
        return self._invoke(state)

    def semantic_understanding_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        previous = state.get("goal_model")
        domain = previous.domain if previous else ""
        user_model_memories = self.user_model.relevant(domain)
        pre_extracted_facts = extract_obvious_facts(state.get("user_input", ""))
        request_context = state.get("request_context", {})
        prior_understanding = request_context.get("goalUnderstanding") if isinstance(request_context, dict) else None
        if isinstance(prior_understanding, dict):
            allowed = {
                "intentState",
                "understoodIntent",
                "possibleDomains",
                "knownFacts",
                "uncertainties",
                "consistencyWarnings",
                "nextQuestion",
                "clarificationOptions",
                "confidence",
            }
            pre_extracted_facts["goalUnderstanding"] = {
                key: prior_understanding[key]
                for key in allowed
                if key in prior_understanding
            }
        payload = GoalModelingInput(
            conversationHistory=state.get("conversation_history", []),
            previousGoalModel=previous,
            preExtractedFacts=pre_extracted_facts,
            relevantMemoryHints=[
                MemoryHint(
                    sourceId=item.id,
                    kind=item.category,
                    statement=item.statement,
                    confidence=item.confidence,
                )
                for item in user_model_memories
            ],
        )
        try:
            result = self.goal_agent.run(payload)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.goal_agent.name, error=exc.error)
        goal = result.artifact
        state["goal_model"] = goal
        state["planning_mode"] = "model_backed"
        state["runtime_status"] = "running"
        self._record_artifact(
            state,
            agent=self.goal_agent.name,
            artifact_type=self.goal_agent.artifact_type,
            artifact=goal,
            model_usage=result.model_usage,
            decision="produce_artifact",
            reason=(goal.questions[0].why_this_question_matters if goal.questions else goal.desired_change),
            summary="Goal Intelligence updated the semantic goal model; completion is judged separately.",
            status="draft",
            input_artifact_types=("user_goal_model",),
        )
        self.persistence.update(
            state["session_id"],
            runtime_status="running",
            goal_model=goal,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_intelligence",
                confidence=goal.confidence,
                rules=[item.statement for item in user_model_memories],
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        return state

    def readiness_judgment_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        goal = state.get("goal_model")
        if not goal:
            return state
        completion = self.goal_completion_judge.evaluate(goal)
        if goal.can_proceed_to_evidence != completion.complete:
            goal = goal.model_copy(update={"can_proceed_to_evidence": completion.complete})
            state["goal_model"] = goal
        state["goal_completion"] = completion
        status = "needs_goal_clarification" if not completion.complete else state.get("status", "needs_goal_clarification")
        business_status = "goal_understood" if completion.complete else "goal_clarification"
        runtime_status = "running" if completion.complete else "idle"
        state["status"] = status
        state["business_status"] = business_status
        state["runtime_status"] = runtime_status
        self._record_artifact(
            state,
            agent=self.goal_completion_judge.name,
            artifact_type=self.goal_completion_judge.artifact_type,
            artifact=completion,
            model_usage={},
            decision="approve" if completion.complete else "request_user_input",
            reason=(
                "Only non-blocking unknowns remain."
                if completion.complete
                else completion.blocking_unknowns[0].impact
            ),
            summary=(
                "Goal completion passed; planning may continue toward strategy."
                if completion.complete
                else "Goal completion is waiting only on decision-blocking information."
            ),
            status="approved" if completion.complete else "blocked",
            input_artifact_types=("user_goal_model",),
        )
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=business_status,
            runtime_status=runtime_status,
            goal_model=goal,
            goal_completion=completion,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="goal_completion",
                confidence=goal.confidence,
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=("reality_assessment", "evidence_pack", "strategy_portfolio", "execution_blueprint", "critique_report")
            if not completion.complete
            else (),
        )
        if completion.complete:
            self._handoff(state, self.goal_agent.name, self.reality_agent.name, "Goal understanding is reliable enough for reality assessment.")
        elif completion.blocking_unknowns:
            state["conversation_history"] = self.persistence.append_assistant_turn(
                state["session_id"],
                "\n".join(item.question for item in completion.blocking_unknowns),
            )
        return state

    def assess_context_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        goal = state.get("goal_model")
        if not goal:
            return state
        memories = self.user_model.relevant(goal.domain)
        payload = RealityAssessmentInput(
            goalModel=goal,
            conversationHistory=state.get("conversation_history", []),
            userModelMemories=[
                MemoryHint(sourceId=item.id, kind=item.category, statement=item.statement, confidence=item.confidence)
                for item in memories
            ],
            requestContext=state.get("request_context", {}),
        )
        try:
            result = self.reality_agent.run(payload)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.reality_agent.name, error=exc.error)
        reality = result.artifact
        state["reality_assessment"] = reality
        state["status"] = "needs_goal_clarification" if not reality.can_proceed_to_evidence else state.get("status", "needs_goal_clarification")
        state["business_status"] = "planning"
        state["runtime_status"] = "running" if reality.can_proceed_to_evidence else "idle"
        self._record_artifact(
            state,
            agent=self.reality_agent.name,
            artifact_type=self.reality_agent.artifact_type,
            artifact=reality,
            model_usage=result.model_usage,
            decision="approve" if reality.can_proceed_to_evidence else "request_user_input",
            reason=reality.feasibility_summary,
            summary=(
                "The goal passed reality assessment."
                if reality.can_proceed_to_evidence
                else "Reality assessment found a decision that the user must resolve."
            ),
            status="approved" if reality.can_proceed_to_evidence else "blocked",
            input_artifact_types=("user_goal_model", "reality_assessment"),
        )
        self.persistence.update(
            state["session_id"],
            status=state["status"],
            business_status="planning",
            runtime_status=state["runtime_status"],
            reality_assessment=reality,
            cognitive_metadata=self._metadata(
                mode="model_backed",
                stage="reality_assessment",
                confidence=reality.confidence,
                rules=[item.statement for item in memories],
                repair_count=int(state.get("repair_count", 0)),
            ),
            clear=("evidence_pack", "strategy_portfolio", "execution_blueprint", "critique_report")
            if not reality.can_proceed_to_evidence
            else (),
        )
        if reality.can_proceed_to_evidence:
            self._handoff(state, self.reality_agent.name, self.evidence_agent.name, "The realistic scope is ready for evidence synthesis.")
        elif reality.important_questions:
            state["conversation_history"] = self.persistence.append_assistant_turn(
                state["session_id"],
                "\n".join(item.question for item in reality.important_questions),
            )
        return state

    def repair_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        state = super().repair_plan_node(state)
        state["next_node"] = {
            "goal_modeling": "understanding",
            "context_evidence": "synthesize_context",
            "strategy_architect": "design_approach",
            "execution_designer": "generate_plan",
            "independent_critic": "semantic_review",
        }.get(str(state.get("next_node") or ""), state.get("next_node", "__end__"))
        return state

    def _persist_learning_hypothesis(self, state, update) -> None:
        # Long-term rules are evaluated independently from the Critic that
        # diagnosed the feedback. Rejection or evaluator failure never blocks
        # the current-plan repair.
        self.harness.evaluate_memory_candidate(
            state["session_id"],
            learning_update=update,
            memory_repository=self.user_model,
        )

__all__ = ["CognitiveOSRuntime"]
