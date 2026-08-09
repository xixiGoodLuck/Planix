from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone as fixed_timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ..db import get_conn
from ..harness import HarnessRuntime
from ..schemas import (
    CreatePlanningSessionRequest,
    PlanningExecutionFeedbackRequest,
    PlanningExecutionFeedbackResponse,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
)
from ..services.calendar_snapshot import calendar_snapshot, normalize_timezone, timezone_info
from .artifact_audit import PlanningArtifactAuditStore
from .session_api import SessionApiAdapter
from .agents import (
    CognitiveModelClient,
    LearningAgent,
    PlanGenerator,
    PlanRepairAgent,
    PlanReviewer,
    PlanningModelUnavailable,
    UnderstandingAgent,
)
from .contracts import (
    CalendarProposal,
    CognitivePlanningMetadata,
    CognitivePlanningState,
    ConstraintSet,
    ContextClaim,
    ContextPack,
    ExecutionOutcome,
    FinalApprovalBundle,
    FinalRevisionPatch,
    LearningObservation,
    PlanBlueprint,
    QualityIssue,
    QualityReport,
    ReplanProposal,
    ScheduleBlueprint,
    UnderstandingSnapshot,
    new_artifact_id,
)
from .graph import build_planning_graph
from .memory import UserModelMemoryRepository
from .persistence import PlanningPersistence, json_object
from .planning_services import (
    CalendarMaterializer,
    ConstraintCompiler,
    ContextBuilder,
    ExecutionFeedbackService,
    FeedbackRouter,
    FinalApprovalService,
    PatchGuard,
    PlanHardValidator,
    ScheduleGenerator,
    SchedulePatchGuard,
    ScheduleValidator,
    UnderstandingReadinessService,
)


_ACTIVE_SESSION_RUNS: set[str] = set()
_ACTIVE_SESSION_RUNS_LOCK = threading.Lock()
_V2_ARTIFACTS = {
    "understanding_snapshot": UnderstandingSnapshot,
    "constraint_set": ConstraintSet,
    "context_pack": ContextPack,
    "plan_blueprint": PlanBlueprint,
    "plan_quality_report": QualityReport,
    "schedule_blueprint": ScheduleBlueprint,
    "schedule_quality_report": QualityReport,
    "calendar_proposal": CalendarProposal,
    "final_approval_bundle": FinalApprovalBundle,
}


class CognitiveOSRuntime:
    """The single native Planix planning runtime."""

    engine_version = "planning-engine-2"

    def __init__(
        self,
        *,
        model_client: CognitiveModelClient | None = None,
        persistence: PlanningPersistence | None = None,
        user_model: UserModelMemoryRepository | None = None,
    ):
        model = model_client or CognitiveModelClient()
        self.persistence = persistence or PlanningPersistence()
        self.agent_runtime = PlanningArtifactAuditStore()
        self.api_adapter = SessionApiAdapter(self.agent_runtime)
        self.harness = HarnessRuntime(artifact_runtime=self.agent_runtime)
        self.user_model = user_model or UserModelMemoryRepository()
        self.understanding_agent = UnderstandingAgent(model)
        self.learning_agent = LearningAgent(model)
        self.plan_generator = PlanGenerator(model)
        self.plan_repair_agent = PlanRepairAgent(model)
        self.plan_reviewer = PlanReviewer(model)
        self.understanding_readiness = UnderstandingReadinessService()
        self.constraint_compiler = ConstraintCompiler()
        self.context_builder = ContextBuilder()
        self.plan_validator = PlanHardValidator()
        self.patch_guard = PatchGuard()
        self.schedule_generator = ScheduleGenerator()
        self.schedule_patch_guard = SchedulePatchGuard()
        self.schedule_validator = ScheduleValidator()
        self.calendar_materializer = CalendarMaterializer()
        self.feedback_router = FeedbackRouter()
        self.final_approval_service = FinalApprovalService()
        self.execution_feedback_service = ExecutionFeedbackService()

    def _metadata(self, *, mode: str, stage: str, repair_count: int = 0) -> CognitivePlanningMetadata:
        return CognitivePlanningMetadata(
            engineVersion=self.engine_version,
            planningMode=mode,
            currentStage=stage,
            repairCount=repair_count,
        )

    def _state_from_row(self, row, *, action: str, user_input: str = "") -> CognitivePlanningState:
        metadata = json_object(row["cognitive_metadata_json"])
        state: CognitivePlanningState = {
            "session_id": row["id"],
            "thread_id": row["thread_id"],
            "user_input": user_input or row["user_input"],
            "conversation_history": self.persistence.conversation(row),
            "request_context": json_object(row["request_context_json"]),
            "user_action": action,
            "status": row["status"],
            "business_status": row["business_status"],
            "runtime_status": row["runtime_status"],
            "planning_mode": str(metadata.get("planningMode") or "model_backed"),
            "resume_node": str(metadata.get("currentStage") or "understanding"),
            "repair_count": int(row["repair_count"] or 0),
            "schedule_repair_count": int(row["schedule_repair_count"] or 0),
            "errors": [],
        }
        latest: dict[str, Any] = {}
        for artifact in self.agent_runtime.list_artifacts(row["id"]):
            if artifact.artifact_type in _V2_ARTIFACTS:
                latest[artifact.artifact_type] = artifact.content_json
        for kind, contract in _V2_ARTIFACTS.items():
            if kind in latest:
                state[kind] = contract.model_validate(latest[kind])
        outcomes = [
            ExecutionOutcome.model_validate(item.content_json)
            for item in self.agent_runtime.list_artifacts(row["id"])
            if item.artifact_type == "execution_outcome"
        ]
        if outcomes:
            state["execution_outcomes"] = outcomes
        return self.harness.restore_graph_state(state)

    def _invoke(self, state: CognitivePlanningState) -> PlanningSessionResponse:
        session_id = str(state["session_id"])
        with _ACTIVE_SESSION_RUNS_LOCK:
            if session_id in _ACTIVE_SESSION_RUNS:
                return self.get_session(session_id)
            _ACTIVE_SESSION_RUNS.add(session_id)
        try:
            self.harness.invoke(adapter=self, graph_builder=build_planning_graph, state=state)
            return self.get_session(session_id)
        finally:
            with _ACTIVE_SESSION_RUNS_LOCK:
                _ACTIVE_SESSION_RUNS.discard(session_id)

    def _latest_artifact_ids(self, session_id: str, kinds: tuple[str, ...]) -> list[str]:
        latest: dict[str, Any] = {}
        for artifact in self.agent_runtime.list_artifacts(session_id):
            if artifact.artifact_type in kinds:
                current = latest.get(artifact.artifact_type)
                if current is None or artifact.version > current.version:
                    latest[artifact.artifact_type] = artifact
        return [latest[kind].id for kind in kinds if kind in latest]

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
        model_usage: dict[str, Any] | None = None,
    ) -> Any:
        item = self.agent_runtime.record_artifact(
            state["session_id"],
            owner_agent=agent,
            artifact_type=artifact_type,
            content=artifact,
            status=status,
        )
        self.agent_runtime.record_decision(
            state["session_id"],
            agent=agent,
            decision=decision,
            reason=reason,
            summary=summary,
            input_artifact_ids=self._latest_artifact_ids(state["session_id"], inputs),
            output_artifact_ids=[item.id],
            model_usage=model_usage or {},
        )
        return artifact

    def _record_rejected_repair(self, state: CognitivePlanningState, *, issue: QualityIssue, reason: str, model_usage: dict[str, Any]) -> None:
        self.agent_runtime.record_decision(
            state["session_id"],
            agent=self.plan_generator.name,
            decision="request_agent_revision",
            reason=reason,
            summary=f"Repair attempt for {issue.issue_id} was rejected by PatchGuard.",
            input_artifact_ids=self._latest_artifact_ids(
                state["session_id"],
                ("plan_blueprint", "plan_quality_report", "constraint_set", "context_pack"),
            ),
            output_artifact_ids=[],
            model_usage=model_usage,
        )

    def _block_model(self, state: CognitivePlanningState, *, agent: str, error) -> CognitivePlanningState:
        recovery = self.harness.decide_model_failure(state, error)
        state.update(
            status="MODEL_UNAVAILABLE",
            business_status=recovery.business_status,
            runtime_status="blocked_model",
            planning_mode="blocked_model_unavailable",
            resume_node=recovery.resume_node,
            errors=[*state.get("errors", []), error],
        )
        self.persistence.update(
            state["session_id"],
            status="MODEL_UNAVAILABLE",
            business_status=recovery.business_status,
            runtime_status="blocked_model",
            repair_count=int(state.get("repair_count", 0)),
            cognitive_metadata=self._metadata(
                mode="blocked_model_unavailable",
                stage=recovery.resume_node,
                repair_count=int(state.get("repair_count", 0)),
            ),
        )
        self.agent_runtime.record_message(
            state["session_id"],
            from_agent=agent,
            to_agent=agent,
            message_type="block",
            reason=error.message,
            payload={
                "errorType": error.error_type,
                "attempts": error.attempts,
                "resumeNode": recovery.resume_node,
            },
        )
        return state

    @staticmethod
    def _blocking_unknown_keys(snapshot: UnderstandingSnapshot) -> set[str]:
        critical = {"core_goal", "safety", "feasibility", "hard_constraint"}
        keys = {item.key for item in snapshot.unknowns if item.blocking_category in critical}
        question = snapshot.next_question
        if question and question.priority == "blocking" and question.target_unknown_key:
            keys.add(question.target_unknown_key)
        return keys

    def session_guard_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        if state.get("status") in {"ARCHIVED", "cancelled", "written_to_calendar"}:
            state["next_node"] = "__end__"
            return state
        state["runtime_status"] = "running"
        return state

    def understanding_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        previous = state.get("understanding_snapshot")
        try:
            result = self.understanding_agent.run(state.get("conversation_history", []), previous=previous)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.understanding_agent.name, error=exc.error)
        snapshot = result.artifact
        state["understanding_snapshot"] = self._record_planning_artifact(
            state,
            agent=self.understanding_agent.name,
            artifact_type="understanding_snapshot",
            artifact=snapshot,
            decision="produce_artifact",
            reason="The current thread was normalized directly into the native UnderstandingSnapshot.",
            summary="Planix updated the current understanding without creating an intermediate goal artifact.",
            model_usage=result.model_usage,
        )
        state["understanding_updated"] = True
        return state

    def understanding_readiness_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        if not snapshot:
            return state
        assessed = self.understanding_readiness.assess(
            snapshot,
            blocking_unknown_keys=self._blocking_unknown_keys(snapshot),
        )
        if assessed != snapshot:
            assessed = assessed.model_copy(update={"version": snapshot.version + 1})
            assessed = self._record_planning_artifact(
                state,
                agent="Understanding Agent",
                artifact_type="understanding_snapshot",
                artifact=assessed,
                decision="approve" if assessed.readiness.ready_for_confirmation else "request_user_input",
                reason="Code-owned readiness normalized blocking authority and the question budget.",
                summary="Understanding readiness was evaluated deterministically.",
                inputs=("understanding_snapshot",),
            )
            state["understanding_snapshot"] = assessed
        return state

    def wait_for_understanding_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        ready = bool(snapshot and snapshot.readiness.ready_for_confirmation)
        status = "waiting_understanding_confirmation" if ready else "needs_goal_clarification"
        state.update(
            status=status,
            business_status="goal_understood" if ready else "goal_clarification",
            runtime_status="idle",
            planning_mode="model_backed",
        )
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=state["business_status"],
            runtime_status="idle",
            cognitive_metadata=self._metadata(mode="model_backed", stage="understanding", repair_count=int(state.get("repair_count", 0))),
        )
        if snapshot and snapshot.next_question and not ready:
            self.persistence.append_assistant_turn(state["session_id"], snapshot.next_question.question)
        return state

    def compile_constraints_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        if not snapshot or not snapshot.readiness.confirmed:
            raise HTTPException(status_code=409, detail={"message": "understanding is not confirmed"})
        request_context = state.get("request_context", {})
        zone = timezone_info(normalize_timezone(str(request_context.get("timezone") or "Asia/Shanghai")))
        constraints = self.constraint_compiler.compile(snapshot, today=datetime.now(zone).date())
        state["constraint_set"] = self._record_planning_artifact(
            state,
            agent="Constraint Compiler",
            artifact_type="constraint_set",
            artifact=constraints,
            decision="produce_artifact",
            reason="Confirmed constraints were compiled deterministically.",
            summary="Constraints are bound to the approved understanding version.",
            status="approved",
            inputs=("understanding_snapshot",),
        )
        return state

    def build_context_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        if not snapshot or not constraints:
            return state
        claims: list[ContextClaim] = []
        memory_refs: list[str] = []
        for index, memory in enumerate(self.user_model.relevant(snapshot.goal_summary.casefold(), limit=5)):
            confirmed = memory.status == "confirmed"
            claims.append(
                ContextClaim(
                    id=f"memory:{index}",
                    claim=memory.statement,
                    sourceType="memory_confirmed" if confirmed else "model_assumption",
                    sourceRef=f"memory:{memory.id}",
                    verificationStatus="verified" if confirmed else "inference",
                    credibility=memory.confidence,
                )
            )
            memory_refs.append(memory.id)
        request_context = state.get("request_context", {})
        for index, raw in enumerate(request_context.get("contextClaims") or []):
            if not isinstance(raw, dict) or not str(raw.get("claim") or "").strip():
                continue
            source_ref = str(raw.get("sourceRef") or f"request-context:{index}")
            verified = str(raw.get("verificationStatus") or "inference") == "verified"
            claims.append(
                ContextClaim(
                    id=str(raw.get("id") or f"context:{index}"),
                    claim=str(raw["claim"]),
                    sourceType="tool_verified" if verified else "model_assumption",
                    sourceRef=source_ref,
                    verificationStatus="verified" if verified else "inference",
                    credibility=float(raw.get("credibility") or (0.8 if verified else 0.5)),
                )
            )
        context = self.context_builder.build(
            snapshot,
            constraints,
            claims=claims,
            memory_refs=memory_refs,
            tool_run_refs=[str(item) for item in request_context.get("toolRunRefs") or []],
            calendar_snapshot_ref=str(request_context.get("calendarSnapshotRef") or "calendar:0"),
        )
        state["context_pack"] = self._record_planning_artifact(
            state,
            agent="Context Builder",
            artifact_type="context_pack",
            artifact=context,
            decision="produce_artifact",
            reason="Current memory, tool, request, and Calendar context was normalized with provenance.",
            summary="The ContextPack contains only traceable claims; inference is explicitly marked.",
            status="approved",
            inputs=("understanding_snapshot", "constraint_set"),
        )
        return state

    def generate_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not snapshot or not constraints or not context:
            return state
        try:
            result = self.plan_generator.run(snapshot, constraints, context)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.plan_generator.name, error=exc.error)
        state["plan_blueprint"] = self._record_planning_artifact(
            state,
            agent=self.plan_generator.name,
            artifact_type="plan_blueprint",
            artifact=result.artifact,
            decision="produce_artifact",
            reason="The approved Understanding, Constraint, and Context produced PlanBlueprint directly.",
            summary="The canonical plan was generated without Strategy or Execution intermediates.",
            inputs=("understanding_snapshot", "constraint_set", "context_pack"),
            model_usage=result.model_usage,
        )
        return state

    def _hard_validate(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not plan or not snapshot or not constraints or not context:
            return state
        previous = state.get("plan_quality_report")
        quality = self.plan_validator.validate(
            plan,
            snapshot=snapshot,
            constraints=constraints,
            context=context,
            repair_round=int(state.get("repair_count", 0)),
        ).model_copy(
            update={
                "artifact_id": previous.artifact_id if previous else new_artifact_id("quality"),
                "version": previous.version + 1 if previous else 1,
                "semantic_review_required": True,
                "semantic_review_completed": False,
            }
        )
        rejected_user_revision = next(
            (issue for issue in (previous.issues if previous else []) if issue.rule_id == "repair_rejected"),
            None,
        )
        if state.get("user_revision_issue") and rejected_user_revision:
            quality = quality.model_copy(
                update={"hard_rules_passed": False, "issues": [*quality.issues, rejected_user_revision]}
            )
        state["plan_quality_report"] = self._record_planning_artifact(
            state,
            agent="Plan Quality Reviewer",
            artifact_type="plan_quality_report",
            artifact=quality,
            decision="approve" if quality.hard_rules_passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "All deterministic plan rules passed.",
            summary="Hard validation is code-owned and cannot be overridden by the model.",
            status="draft" if quality.hard_rules_passed else "needs_revision",
            inputs=("plan_blueprint", "understanding_snapshot", "constraint_set", "context_pack"),
        )
        return state

    def validate_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        return self._hard_validate(state)

    def validate_repaired_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        return self._hard_validate(state)

    def semantic_review_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        snapshot = state.get("understanding_snapshot")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        plan = state.get("plan_blueprint")
        hard = state.get("plan_quality_report")
        if not snapshot or not constraints or not context or not plan or not hard:
            return state
        try:
            result = self.plan_reviewer.run(snapshot, constraints, context, plan, hard)
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.plan_reviewer.name, error=exc.error)
        semantic = result.artifact
        quality = hard.model_copy(
            update={
                "version": hard.version + 1,
                "semantic_review_required": True,
                "semantic_review_completed": True,
                "issues": [*hard.issues, *semantic.issues],
                "score": semantic.score,
                "remaining_risks": [
                    *semantic.remaining_risks,
                    *(issue.description for issue in semantic.issues if issue.severity == "minor"),
                ],
            }
        )
        state["plan_quality_report"] = self._record_planning_artifact(
            state,
            agent=self.plan_reviewer.name,
            artifact_type="plan_quality_report",
            artifact=quality,
            decision="approve" if quality.passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "Hard and semantic quality checks passed.",
            summary="Plan quality depends only on hard rules and the absence of blocker/major issues.",
            status="approved" if quality.passed else "needs_revision",
            inputs=("understanding_snapshot", "constraint_set", "context_pack", "plan_blueprint", "plan_quality_report"),
            model_usage=result.model_usage,
        )
        return state

    def repair_plan_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        quality = state.get("plan_quality_report")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        snapshot = state.get("understanding_snapshot")
        if not plan or not quality or not constraints or not context or not snapshot:
            return state
        user_revision = state.get("user_revision_issue")
        if user_revision:
            attempts = int(state.get("user_revision_attempts", 0))
            if attempts >= 2:
                return state
            state["user_revision_attempts"] = attempts + 1
        elif int(state.get("repair_count", 0)) >= 2:
            return state
        issue = user_revision or next((item for item in quality.issues if item.severity in {"blocker", "major"}), None)
        if issue is None:
            return state
        try:
            result = self.plan_repair_agent.run(plan, issue, constraints, context)
            if user_revision:
                state["final_revision_patch"] = self._record_planning_artifact(
                    state,
                    agent="Final Review Controller",
                    artifact_type="final_revision_patch",
                    artifact=FinalRevisionPatch(
                        category=state["feedback_route"].category,
                        feedback=state["feedback_route"].normalized_instruction,
                        baseArtifactId=plan.artifact_id,
                        baseVersion=plan.version,
                        operations=result.artifact.operations,
                    ),
                    decision="produce_artifact",
                    reason="Final Review feedback was converted into a version-bound typed patch.",
                    summary="Only the requested Plan layer may change; downstream artifacts are regenerated.",
                    status="approved",
                    inputs=("plan_blueprint", "plan_quality_report"),
                    model_usage=result.model_usage,
                )
            repaired, repair = self.patch_guard.apply_plan(
                plan,
                result.artifact,
                issue,
                validator=self.plan_validator,
                snapshot=snapshot,
                constraints=constraints,
                context=context,
            )
        except PlanningModelUnavailable as exc:
            return self._block_model(state, agent=self.plan_generator.name, error=exc.error)
        except ValueError as exc:
            self._record_rejected_repair(state, issue=issue, reason=str(exc), model_usage=result.model_usage)
            if user_revision:
                state["user_revision_issue"] = user_revision.model_copy(
                    update={"description": f"{user_revision.description}\nPrevious repair rejected: {exc}"}
                )
            invalid = QualityIssue(
                issueId=f"repair:{issue.issue_id}",
                category=issue.category,
                severity="blocker",
                ruleId="repair_rejected",
                targetType=issue.target_type,
                targetId=issue.target_id,
                description=str(exc),
                evidenceRefs=[issue.issue_id],
                allowedOperations=[],
                repairBasis="patch_guard",
            )
            state["plan_quality_report"] = quality.model_copy(
                update={"issues": [*quality.issues, invalid], "repair_round": min(2, int(state.get("repair_count", 0)) + 1)}
            )
            if not user_revision:
                state["repair_count"] = min(2, int(state.get("repair_count", 0)) + 1)
                self.persistence.update(state["session_id"], repair_count=state["repair_count"], runtime_status="running")
            elif int(state.get("user_revision_attempts", 0)) < 2:
                return self.repair_plan_node(state)
            return state
        if not repair.accepted:
            self._record_rejected_repair(state, issue=issue, reason=repair.reason, model_usage=result.model_usage)
            rejected = QualityIssue(
                issueId=f"repair:{issue.issue_id}",
                category=issue.category,
                severity="blocker",
                ruleId="repair_rejected",
                targetType=issue.target_type,
                targetId=issue.target_id,
                description=repair.reason,
                evidenceRefs=[issue.issue_id],
                allowedOperations=[],
                repairBasis="patch_guard",
            )
            state["plan_quality_report"] = quality.model_copy(update={"issues": [*quality.issues, rejected]})
            if user_revision:
                state["user_revision_issue"] = user_revision.model_copy(
                    update={"description": f"{user_revision.description}\nPrevious repair rejected: {repair.reason}"}
                )
            else:
                state["repair_count"] = min(2, int(state.get("repair_count", 0)) + 1)
                self.persistence.update(state["session_id"], repair_count=state["repair_count"], runtime_status="running")
            self.agent_runtime.record_message(
                state["session_id"],
                from_agent="Plan Generator",
                to_agent="Plan Quality Reviewer",
                message_type="revision_request",
                reason=repair.reason,
                payload={"issueId": issue.issue_id, "repairAttempt": state.get("user_revision_attempts") if user_revision else state["repair_count"]},
            )
            if user_revision and int(state.get("user_revision_attempts", 0)) < 2:
                return self.repair_plan_node(state)
            return state
        if not user_revision:
            state["repair_count"] = min(2, int(state.get("repair_count", 0)) + 1)
        state["plan_blueprint"] = self._record_planning_artifact(
            state,
            agent=self.plan_generator.name,
            artifact_type="plan_blueprint",
            artifact=repaired,
            decision="produce_artifact",
            reason=f"Issue-scoped repair resolved {issue.issue_id} through PatchGuard.",
            summary="Only allowed operations were applied; immutable goal and constraints remained bound.",
            inputs=("plan_blueprint", "plan_quality_report", "constraint_set", "context_pack"),
            model_usage=result.model_usage,
        )
        self.persistence.update(state["session_id"], repair_count=int(state.get("repair_count", 0)), runtime_status="running")
        state["user_revision_issue"] = None
        return state

    def generate_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not plan or not constraints or not context:
            return state
        request_context = state.get("request_context", {})
        timezone = normalize_timezone(str(request_context.get("timezone") or "Asia/Shanghai"))
        zone = timezone_info(timezone)
        start_date = constraints.core.required_start_date
        start = datetime.combine(
            date.fromisoformat(start_date) if start_date else date.today() + timedelta(days=1),
            datetime.min.time().replace(hour=9),
            tzinfo=zone,
        )
        effective_constraints = constraints
        previous = state.get("schedule_blueprint")
        route = state.get("feedback_route")
        if route and route.category == "schedule_change" and previous:
            excluded = self._feedback_excluded_weekdays(route.normalized_instruction)
            core = constraints.core.model_copy(
                update={"excluded_weekdays": sorted(set([*constraints.core.excluded_weekdays, *excluded]))}
            )
            effective_constraints = constraints.model_copy(update={"core": core})
            if any(token in route.normalized_instruction.casefold() for token in ("周末", "weekend")):
                start += timedelta(days=(5 - start.weekday()) % 7)
            state["final_revision_patch"] = self._record_planning_artifact(
                state,
                agent="Final Review Controller",
                artifact_type="final_revision_patch",
                artifact=FinalRevisionPatch(
                    category="schedule_change",
                    feedback=route.normalized_instruction,
                    baseArtifactId=previous.artifact_id,
                    baseVersion=previous.version,
                    operations=[
                        {"operation": "move_schedule_session", "targetId": previous.artifact_id, "payload": {"excludedWeekdays": excluded, "preferWeekend": "周末" in route.normalized_instruction or "weekend" in route.normalized_instruction.casefold()}},
                    ],
                ),
                decision="produce_artifact",
                reason="Final Review schedule feedback was bound to the current Schedule version.",
                summary="Plan semantics remain unchanged while Schedule and downstream artifacts are regenerated.",
                status="approved",
                inputs=("schedule_blueprint", "schedule_quality_report"),
            )
        schedule = self.schedule_generator.generate(
            plan,
            effective_constraints,
            start=start,
            timezone=timezone,
            calendar_snapshot_ref=context.calendar_snapshot_ref,
            calendar_busy=self._calendar_busy(request_context),
        )
        if previous:
            schedule = schedule.model_copy(update={"artifact_id": new_artifact_id("schedule"), "version": previous.version + 1})
        state["schedule_blueprint"] = self._record_planning_artifact(
            state,
            agent="Schedule Agent",
            artifact_type="schedule_blueprint",
            artifact=schedule,
            decision="produce_artifact",
            reason="Validated Plan tasks were topologically ordered into bounded sessions.",
            summary="Schedule generation changed timing only, never Plan semantics.",
            inputs=("plan_blueprint", "constraint_set", "context_pack", "plan_quality_report"),
        )
        return state

    def validate_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        schedule = state.get("schedule_blueprint")
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not schedule or not plan or not constraints or not context:
            return state
        previous = state.get("schedule_quality_report")
        quality = self.schedule_validator.validate(
            schedule,
            plan=plan,
            constraints=constraints,
            current_calendar_snapshot_ref=context.calendar_snapshot_ref,
            calendar_busy=self._calendar_busy(state.get("request_context", {})),
        ).model_copy(
            update={
                "artifact_id": previous.artifact_id if previous else new_artifact_id("quality"),
                "version": previous.version + 1 if previous else 1,
            }
        )
        revision = state.get("final_revision_patch")
        if revision and revision.category == "schedule_change":
            excluded = set(self._feedback_excluded_weekdays(revision.feedback))
            violations = [item for item in schedule.sessions if datetime.fromisoformat(item.start).weekday() in excluded]
            if violations:
                issue = QualityIssue(
                    issueId=f"schedule:user-feedback:{schedule.artifact_id}", category="schedule", severity="major",
                    ruleId="user_schedule_revision", targetType="schedule", targetId=schedule.artifact_id,
                    description="Schedule still violates the excluded weekday requested in Final Review.",
                    evidenceRefs=[revision.id], allowedOperations=["move_schedule_session"], repairBasis="user_final_review",
                )
                quality = quality.model_copy(update={"hard_rules_passed": False, "issues": [*quality.issues, issue]})
        state["schedule_quality_report"] = self._record_planning_artifact(
            state,
            agent="Schedule Quality Reviewer",
            artifact_type="schedule_quality_report",
            artifact=quality,
            decision="approve" if quality.passed else "request_agent_revision",
            reason="; ".join(item.description for item in quality.issues) or "All deterministic schedule rules passed.",
            summary="Capacity, conflict, timezone, dependency, and Plan-semantics guards were checked.",
            status="approved" if quality.passed else "needs_revision",
            inputs=("schedule_blueprint", "plan_blueprint", "constraint_set", "context_pack"),
        )
        return state

    def repair_schedule_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        schedule = state.get("schedule_blueprint")
        plan = state.get("plan_blueprint")
        constraints = state.get("constraint_set")
        context = state.get("context_pack")
        if not schedule or not plan or not constraints or not context:
            return state
        count = int(state.get("schedule_repair_count", 0))
        if count >= 2:
            return state
        quality = state.get("schedule_quality_report")
        issue = next((item for item in (quality.issues if quality else []) if item.severity in {"blocker", "major"}), None)
        if issue is None:
            return state
        repaired = self.schedule_patch_guard.apply(
            schedule,
            issue,
            constraints=constraints,
            calendar_busy=self._calendar_busy(state.get("request_context", {})),
        )
        state["schedule_repair_count"] = count + 1
        state["schedule_blueprint"] = self._record_planning_artifact(
            state,
            agent="Schedule Agent",
            artifact_type="schedule_blueprint",
            artifact=repaired,
            decision="produce_artifact",
            reason=f"Issue-scoped schedule repair applied {issue.allowed_operations} to {issue.issue_id}.",
            summary="Only the targeted sessions moved or split; task identity and total effort were preserved.",
            inputs=("schedule_blueprint", "schedule_quality_report", "plan_blueprint", "constraint_set"),
        )
        self.persistence.update(state["session_id"], schedule_repair_count=state["schedule_repair_count"], runtime_status="running")
        return state

    def materialize_calendar_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan = state.get("plan_blueprint")
        schedule = state.get("schedule_blueprint")
        context = state.get("context_pack")
        if not plan or not schedule or not context:
            return state
        previous = state.get("calendar_proposal")
        proposal = self.calendar_materializer.materialize(
            plan,
            schedule,
            timezone=schedule.planning_timezone,
            current_calendar_snapshot_ref=context.calendar_snapshot_ref,
        )
        route = state.get("feedback_route")
        if route and route.category == "presentation_change" and previous:
            state["final_revision_patch"] = self._record_planning_artifact(
                state,
                agent="Final Review Controller",
                artifact_type="final_revision_patch",
                artifact=FinalRevisionPatch(
                    category="presentation_change",
                    feedback=route.normalized_instruction,
                    baseArtifactId=previous.artifact_id,
                    baseVersion=previous.version,
                    operations=[{"operation": "update_calendar_presentation", "targetId": previous.artifact_id, "payload": {"titleStyle": "concise"}}],
                ),
                decision="produce_artifact",
                reason="Calendar presentation feedback was bound to the current Calendar Proposal.",
                summary="Only Calendar titles changed; Plan and Schedule semantics remain identical.",
                status="approved",
                inputs=("calendar_proposal",),
            )
            proposal = proposal.model_copy(
                update={
                    "artifact_id": new_artifact_id("calendar"),
                    "version": previous.version + 1,
                    "events": [event.model_copy(update={"title": self._concise_title(event.title)}) for event in proposal.events],
                }
            )
        elif previous:
            proposal = proposal.model_copy(update={"artifact_id": new_artifact_id("calendar"), "version": previous.version + 1})
        state["calendar_proposal"] = self._record_planning_artifact(
            state,
            agent="Calendar Materializer",
            artifact_type="calendar_proposal",
            artifact=proposal,
            decision="produce_artifact",
            reason="Calendar rows were materialized deterministically from the validated Schedule.",
            summary="Every event has stable source lineage; no Calendar write occurred.",
            status="approved",
            inputs=("plan_blueprint", "plan_quality_report", "schedule_blueprint", "schedule_quality_report"),
        )
        return state

    def wait_for_final_review_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        plan_quality = state.get("plan_quality_report")
        schedule_quality = state.get("schedule_quality_report")
        ready = bool(plan_quality and plan_quality.passed and schedule_quality and schedule_quality.passed and state.get("calendar_proposal"))
        status = "waiting_final_review" if ready else "final_revision"
        state.update(status=status, business_status="calendar_pending" if ready else "planning", runtime_status="idle")
        self.persistence.update(
            state["session_id"],
            status=status,
            business_status=state["business_status"],
            runtime_status="idle",
            repair_count=int(state.get("repair_count", 0)),
            cognitive_metadata=self._metadata(mode="model_backed", stage="final_review", repair_count=int(state.get("repair_count", 0))),
        )
        return state

    def feedback_router_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        route = self.feedback_router.route(state.get("user_input", ""))
        state["feedback_route"] = route
        if route.category in {"plan_change", "resource_change"}:
            plan = state.get("plan_blueprint")
            if plan:
                operations = (
                    ["replace_resource", "update_task"]
                    if route.category == "resource_change"
                    else ["update_task", "split_task", "move_task", "add_task", "remove_optional_task"]
                )
                state["user_revision_issue"] = QualityIssue(
                    issueId=f"final-review:{route.category}:{plan.artifact_id}:{plan.version}",
                    category="evidence" if route.category == "resource_change" else "content",
                    severity="major",
                    ruleId="user_final_revision",
                    targetType="plan",
                    targetId=plan.artifact_id,
                    description=route.normalized_instruction,
                    evidenceRefs=[f"user-feedback:{state['session_id']}"],
                    allowedOperations=operations,
                    repairBasis="user_final_review",
                )
                state["user_revision_attempts"] = 0
        state["next_node"] = {
            "understanding_change": "understanding",
            "plan_change": "repair_plan",
            "resource_change": "repair_plan",
            "schedule_change": "generate_schedule",
            "presentation_change": "materialize_calendar",
            "approve": "wait_for_final_review",
            "reject": "wait_for_final_review",
        }[route.category]
        return state

    @staticmethod
    def _feedback_excluded_weekdays(text: str) -> list[int]:
        lower = text.casefold()
        names = {"周一": 0, "monday": 0, "周二": 1, "tuesday": 1, "周三": 2, "wednesday": 2, "周四": 3, "thursday": 3, "周五": 4, "friday": 4, "周六": 5, "saturday": 5, "周日": 6, "周天": 6, "sunday": 6}
        if not any(token in lower for token in ("不要", "避开", "排除", "exclude", "not on")):
            return []
        return sorted({number for name, number in names.items() if name in lower})

    @staticmethod
    def _concise_title(title: str) -> str:
        words = title.split()
        if len(words) > 4:
            return " ".join(words[:4])
        if len(title) > 8:
            return title[:8].rstrip()
        return title[:-1].rstrip() if len(title) > 4 else title

    def record_learning_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        observation = LearningObservation(
            sessionId=state["session_id"],
            category="execution_feedback",
            statement=state.get("user_input", ""),
            sourceRefs=[f"session:{state['session_id']}:feedback"],
        )
        state["learning_observations"] = [*state.get("learning_observations", []), observation]
        self._record_planning_artifact(
            state,
            agent="Learning Observer",
            artifact_type="learning_observation",
            artifact=observation,
            decision="produce_artifact",
            reason="Execution feedback was recorded as a tentative observation.",
            summary="The observation is not durable memory until independent Memory Evaluation approves it.",
            inputs=("final_approval_bundle",),
        )
        return state

    def calendar_gate_node(self, state: CognitivePlanningState) -> CognitivePlanningState:
        self._assert_final_authority(state["session_id"], require_permission=False)
        state.update(status="waiting_calendar_write_approval", business_status="calendar_pending", runtime_status="idle")
        self.persistence.update(
            state["session_id"],
            status="waiting_calendar_write_approval",
            business_status="calendar_pending",
            runtime_status="idle",
            cognitive_metadata=self._metadata(mode="model_backed", stage="calendar_gate", repair_count=int(state.get("repair_count", 0))),
        )
        return state

    def create_session(self, payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
        session_id = self.persistence.create(
            thread_id=payload.thread_id or "",
            user_input=payload.user_input,
            context=payload.context,
        )
        row = self.persistence.get_row(session_id)
        return self._invoke(self._state_from_row(row, action="create"))

    def answer_understanding(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] not in {"needs_goal_clarification", "MODEL_UNAVAILABLE"}:
            raise HTTPException(status_code=409, detail={"message": "session is not waiting for understanding input"})
        self.persistence.append_user_turn(session_id, payload.text)
        row = self.persistence.get_row(session_id)
        return self._invoke(self._state_from_row(row, action="answer_question", user_input=payload.text))

    clarify = answer_understanding

    def confirm_understanding(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] != "waiting_understanding_confirmation":
            raise HTTPException(status_code=409, detail={"message": "understanding is not ready for confirmation"})
        state = self._state_from_row(row, action="confirm_understanding")
        snapshot = state.get("understanding_snapshot")
        if not snapshot or not snapshot.readiness.ready_for_confirmation:
            raise HTTPException(status_code=409, detail={"message": "understanding is not ready"})
        approved = snapshot.model_copy(
            update={
                "version": snapshot.version + 1,
                "readiness": snapshot.readiness.model_copy(update={"confirmed": True}),
            }
        )
        state["understanding_snapshot"] = self._record_planning_artifact(
            state,
            agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            artifact=approved,
            decision="approve",
            reason="The user explicitly confirmed the current UnderstandingSnapshot.",
            summary="Planning may now compile constraints and context.",
            status="approved",
            inputs=("understanding_snapshot",),
        )
        state.update(status="planning", business_status="planning", runtime_status="running")
        self.persistence.update(session_id, status="planning", business_status="planning", runtime_status="running")
        return self._invoke(state)

    def revise_understanding(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        self.persistence.append_user_turn(session_id, payload.text)
        row = self.persistence.get_row(session_id)
        return self._invoke(self._state_from_row(row, action="answer_question", user_input=payload.text))

    def revise_final(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] not in {"waiting_final_review", "final_revision", "waiting_calendar_write_approval"}:
            raise HTTPException(status_code=409, detail={"message": "session is not in final review"})
        self.persistence.append_user_turn(session_id, payload.text)
        row = self.persistence.get_row(session_id)
        return self._invoke(self._state_from_row(row, action="give_feedback", user_input=payload.text))

    def approve_final(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] != "waiting_final_review":
            raise HTTPException(status_code=409, detail={"message": "session is not ready for final approval"})
        state = self._state_from_row(row, action="write_calendar")
        plan_quality = state.get("plan_quality_report")
        schedule_quality = state.get("schedule_quality_report")
        if not plan_quality or not plan_quality.passed or not schedule_quality or not schedule_quality.passed:
            raise HTTPException(status_code=409, detail={"message": "current plan or schedule quality has not passed"})
        heads = self.harness.bootstrap(state).checkpoint.artifact_refs
        required = (
            "understanding_snapshot",
            "constraint_set",
            "context_pack",
            "plan_blueprint",
            "plan_quality_report",
            "schedule_blueprint",
            "schedule_quality_report",
            "calendar_proposal",
        )
        if any(kind not in heads for kind in required):
            raise HTTPException(status_code=409, detail={"message": "final approval is missing a current artifact"})
        snapshot_version = int(state.get("request_context", {}).get("calendarSnapshotVersion") or 0)
        persistent = self.harness.bootstrap(state)
        approval = self.final_approval_service.create(
            session_id=session_id,
            understanding_version=heads["understanding_snapshot"].version,
            constraint_version=heads["constraint_set"].version,
            context_version=heads["context_pack"].version,
            plan_version=heads["plan_blueprint"].version,
            quality_report_version=heads["plan_quality_report"].version,
            schedule_version=heads["schedule_blueprint"].version,
            schedule_quality_report_version=heads["schedule_quality_report"].version,
            calendar_proposal_version=heads["calendar_proposal"].version,
            calendar_snapshot_version=snapshot_version,
            checkpoint_version=persistent.checkpoint_version,
        )
        state["final_approval_bundle"] = self._record_planning_artifact(
            state,
            agent="Final Review Controller",
            artifact_type="final_approval_bundle",
            artifact=approval,
            decision="approve",
            reason="The user approved the exact current Plan, Quality, Schedule, Calendar, and context versions.",
            summary="Final Approval is version-bound and still requires Calendar permission.",
            status="approved",
            inputs=required,
        )
        state.update(status="waiting_calendar_write_approval", business_status="calendar_pending", runtime_status="idle")
        self.persistence.update(
            session_id,
            status="waiting_calendar_write_approval",
            business_status="calendar_pending",
            runtime_status="idle",
            cognitive_metadata=self._metadata(mode="model_backed", stage="calendar_gate", repair_count=int(state.get("repair_count", 0))),
        )
        self.harness.bootstrap(state)
        return self.get_session(session_id)

    def _current_versions(self, state: CognitivePlanningState, approval: FinalApprovalBundle) -> dict[str, int]:
        heads = self.harness.bootstrap(state).checkpoint.artifact_refs
        return {
            "understanding": heads.get("understanding_snapshot").version if heads.get("understanding_snapshot") else 0,
            "constraint": heads.get("constraint_set").version if heads.get("constraint_set") else 0,
            "context": heads.get("context_pack").version if heads.get("context_pack") else 0,
            "plan": heads.get("plan_blueprint").version if heads.get("plan_blueprint") else 0,
            "quality_report": heads.get("plan_quality_report").version if heads.get("plan_quality_report") else 0,
            "schedule": heads.get("schedule_blueprint").version if heads.get("schedule_blueprint") else 0,
            "schedule_quality": heads.get("schedule_quality_report").version if heads.get("schedule_quality_report") else 0,
            "calendar_proposal": heads.get("calendar_proposal").version if heads.get("calendar_proposal") else 0,
            "calendar_snapshot": int(calendar_snapshot(str(state.get("request_context", {}).get("timezone") or "Asia/Shanghai"))["calendarSnapshotVersion"]),
        }

    @staticmethod
    def _calendar_busy(request_context: dict[str, Any]) -> list[tuple[datetime, datetime]]:
        busy: list[tuple[datetime, datetime]] = []
        for item in request_context.get("calendarBusy") or []:
            if not isinstance(item, dict):
                continue
            try:
                busy.append((datetime.fromisoformat(str(item["start"])), datetime.fromisoformat(str(item["end"]))))
            except (KeyError, ValueError):
                continue
        return busy

    def _assert_final_authority(self, session_id: str, *, require_permission: bool) -> None:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="planning session not found")
        state = self._state_from_row(row, action="continue_current_stage")
        approval = state.get("final_approval_bundle")
        proposal = state.get("calendar_proposal")
        plan = state.get("plan_blueprint")
        schedule = state.get("schedule_blueprint")
        plan_quality = state.get("plan_quality_report")
        schedule_quality = state.get("schedule_quality_report")
        if not approval or not proposal or not plan or not schedule:
            raise HTTPException(status_code=409, detail={"message": "current Final Approval or Calendar Proposal is missing"})
        try:
            self.final_approval_service.assert_current(approval, self._current_versions(state, approval))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
        if not plan_quality or not plan_quality.passed or not schedule_quality or not schedule_quality.passed:
            raise HTTPException(status_code=409, detail={"message": "current quality reports do not allow Calendar write"})
        if proposal.plan_ref != plan.artifact_id or proposal.schedule_ref != schedule.artifact_id:
            raise HTTPException(status_code=409, detail={"message": "Calendar Proposal is stale"})
        if any(event.source_plan_version != plan.version or event.source_schedule_version != schedule.version for event in proposal.events):
            raise HTTPException(status_code=409, detail={"message": "Calendar event lineage is stale"})
        if require_permission:
            decision = self.harness.calendar_write_policy(
                session_id,
                planning_mode="model_backed",
                plan_quality_passed=plan_quality.passed,
                schedule_quality_passed=schedule_quality.passed,
            )
            if not decision.allowed:
                raise HTTPException(status_code=409, detail={"message": decision.reason})

    def approve_calendar_write(self, session_id: str, *, final_approval_ref: dict[str, Any]) -> None:
        self.harness.assert_current_artifact(session_id, kind="final_approval_bundle", expected_ref=final_approval_ref)
        self._assert_final_authority(session_id, require_permission=False)
        self.harness.record_approval(session_id)

    def assert_calendar_write_allowed(self, session_id: str, *, final_approval_ref: dict[str, Any]) -> None:
        self.harness.assert_current_artifact(session_id, kind="final_approval_bundle", expected_ref=final_approval_ref)
        self._assert_final_authority(session_id, require_permission=True)

    def mark_calendar_written(self, session_id: str, *, final_approval_ref: dict[str, Any] | None = None, mutation_revision: int | None = None) -> None:
        if mutation_revision is not None:
            if final_approval_ref is None:
                raise HTTPException(status_code=409, detail={"message": "Final Approval reference is required"})
            self.harness.assert_current_artifact(session_id, kind="final_approval_bundle", expected_ref=final_approval_ref)
            current = int(calendar_snapshot()["calendarSnapshotVersion"])
            if current != mutation_revision:
                raise HTTPException(status_code=409, detail={"message": "Calendar changed during approved write"})
        elif final_approval_ref is not None:
            self.assert_calendar_write_allowed(session_id, final_approval_ref=final_approval_ref)
        else:
            self._assert_final_authority(session_id, require_permission=True)
        row = self.persistence.get_row(session_id)
        state = self._state_from_row(row, action="continue_current_stage")
        approval = state.get("final_approval_bundle")
        self.harness.consume_calendar_approval(session_id)
        if approval:
            consumed = approval.model_copy(update={"consumed": True})
            state["final_approval_bundle"] = self._record_planning_artifact(
                state,
                agent="Final Review Controller",
                artifact_type="final_approval_bundle",
                artifact=consumed,
                decision="approve",
                reason="The approved Calendar mutation completed successfully.",
                summary="The one-time Final Approval was consumed after Calendar write.",
                status="approved",
                inputs=("final_approval_bundle",),
            )
        self.persistence.mark_written(session_id)

    def plan_to_structured_plan(self, session: PlanningSessionResponse) -> dict[str, Any]:
        if not session.plan_blueprint or not session.calendar_proposal:
            raise RuntimeError("Calendar preview requires current PlanBlueprint and CalendarProposal")
        plan = PlanBlueprint.model_validate(session.plan_blueprint)
        proposal = CalendarProposal.model_validate(session.calendar_proposal)
        tasks = [
            {
                "title": event.title,
                "description": event.description,
                "estimatedMinutes": max(1, int((datetime.fromisoformat(event.end) - datetime.fromisoformat(event.start)).total_seconds() // 60)),
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
                        task for task, event in zip(tasks, proposal.events)
                        if next((item.milestone_id for item in plan.tasks if item.id == event.source_task_id), None) == milestone.id
                    ],
                }
                for milestone in plan.milestones
            ],
            "reviewPlan": {"frequency": "weekly", "questions": ["What was completed?", "What is blocked?", "What should be replanned?"]},
        }

    def record_execution_feedback(self, session_id: str, payload: PlanningExecutionFeedbackRequest) -> PlanningExecutionFeedbackResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="planning session not found")
        state = self._state_from_row(row, action="continue_current_stage")
        plan = state.get("plan_blueprint")
        if not plan:
            raise HTTPException(status_code=409, detail={"message": "current PlanBlueprint is missing"})
        task = next((item for item in plan.tasks if item.id == payload.task_id), None)
        if not task:
            raise HTTPException(status_code=404, detail="plan task not found")
        outcome = self.execution_feedback_service.record(
            task=task,
            status=payload.status,
            actual_minutes=payload.actual_minutes,
            completion_evidence=payload.completion_evidence,
            blocker_reason=payload.blocker_reason,
            failure_reason=payload.failure_reason,
        )
        self._record_planning_artifact(
            state,
            agent="Execution Feedback Evaluator",
            artifact_type="execution_outcome",
            artifact=outcome,
            decision="produce_artifact",
            reason="Task completion feedback was recorded against the current Plan task.",
            summary="Execution feedback is immutable evidence for replan and learning decisions.",
            status="approved",
            inputs=("plan_blueprint",),
        )
        proposal = self.execution_feedback_service.propose_replan(session_id=session_id, outcomes=[outcome])
        if proposal:
            self._record_planning_artifact(
                state,
                agent="Execution Feedback Evaluator",
                artifact_type="replan_proposal",
                artifact=proposal,
                decision="produce_artifact",
                reason="A failed or blocked task requires a scoped replan proposal.",
                summary="Replanning remains versioned and requires Final Review.",
                inputs=("execution_outcome", "plan_blueprint"),
            )
        outcome_artifact = max(
            (item for item in self.agent_runtime.list_artifacts(session_id) if item.artifact_type == "execution_outcome"),
            key=lambda item: item.version,
        )
        source_ref = f"artifact:{outcome_artifact.id}:v{outcome_artifact.version}"
        learning = self.learning_agent.run(
            outcome,
            session_id=session_id,
            source_ref=source_ref,
            domain_scope=[plan.goal_summary.casefold()],
        )
        observation = learning.artifact
        self._record_planning_artifact(
            state,
            agent="Learning Observer",
            artifact_type="learning_observation",
            artifact=observation,
            decision="produce_artifact",
            reason="The execution outcome produced a tentative learning observation.",
            summary="No durable memory is written without independent Memory Evaluation.",
            inputs=("execution_outcome",),
            model_usage=learning.model_usage,
        )
        self.harness.evaluate_memory_candidate(
            session_id,
            learning_observation=observation,
            memory_repository=self.user_model,
        )
        return PlanningExecutionFeedbackResponse(
            outcome=outcome.model_dump(by_alias=True),
            replanProposal=proposal.model_dump(by_alias=True) if proposal else None,
            learningObservation=observation.model_dump(by_alias=True),
        )

    def skip_current_stage(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] != "needs_goal_clarification":
            raise HTTPException(status_code=409, detail={"message": "skip is only valid during understanding"})
        state = self._state_from_row(row, action="confirm_understanding")
        snapshot = state.get("understanding_snapshot")
        if not snapshot or snapshot.conflicts:
            raise HTTPException(status_code=409, detail={"message": "conflicts cannot be skipped"})
        non_assumable = {"core_goal", "safety", "feasibility", "hard_constraint"}
        if any(item.blocking_category in non_assumable for item in snapshot.unknowns):
            raise HTTPException(status_code=409, detail={"message": "core, safety, feasibility, or hard-constraint questions cannot be skipped"})
        assumptions = [*snapshot.assumptions]
        for item in snapshot.unknowns:
            assumptions.append(item.model_copy(update={"source_type": "model_assumption", "mutation_policy": "user_confirmation_required"}))
        skipped = snapshot.model_copy(
            update={
                "version": snapshot.version + 1,
                "unknowns": [],
                "assumptions": assumptions,
                "next_question": None,
                "readiness": snapshot.readiness.model_copy(update={"ready_for_confirmation": True, "blocking_reasons": [], "confirmed": True}),
            }
        )
        state["understanding_snapshot"] = self._record_planning_artifact(
            state,
            agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            artifact=skipped,
            decision="approve",
            reason="Noncritical unknowns were converted to explicit assumptions after the user skipped clarification.",
            summary="Planning continues without inventing user facts.",
            status="approved",
            inputs=("understanding_snapshot",),
        )
        state.update(status="planning", business_status="planning", runtime_status="running")
        self.persistence.update(session_id, status="planning", business_status="planning", runtime_status="running")
        return self._invoke(state)

    def continue_current_stage(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row or row["status"] != "MODEL_UNAVAILABLE":
            return self.get_session(session_id)
        return self._invoke(self._state_from_row(row, action="continue_current_stage"))

    def cancel(self, session_id: str) -> PlanningSessionResponse:
        if not self.persistence.get_row(session_id):
            raise HTTPException(status_code=404, detail="planning session not found")
        self.persistence.mark_cancelled(session_id)
        return self.get_session(session_id)

    def latest_for_thread(self, thread_id: str) -> PlanningSessionResponse | None:
        row = self.persistence.latest_active(thread_id)
        return self.api_adapter.from_row(row) if row else None

    def get_session(self, session_id: str) -> PlanningSessionResponse:
        row = self.persistence.get_row(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="planning session not found")
        return self.api_adapter.from_row(row)


__all__ = ["CognitiveOSRuntime"]
