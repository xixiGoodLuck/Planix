from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import Field

from .base import CognitiveContract
from .planning import (
    CalendarProposal,
    ConstraintSet,
    ContextPack,
    ExecutionOutcome,
    FeedbackRoute,
    FinalApprovalBundle,
    LearningObservation,
    PlanBlueprint,
    QualityReport,
    ReplanProposal,
    ScheduleBlueprint,
    UnderstandingSnapshot,
)


PlanningMode = Literal["model_backed", "blocked_model_unavailable"]
CognitivePlanningStatus = Literal[
    "needs_goal_clarification",
    "waiting_understanding_confirmation",
    "planning",
    "final_revision",
    "waiting_final_review",
    "waiting_calendar_write_approval",
    "written_to_calendar",
    "learning_from_feedback",
    "MODEL_UNAVAILABLE",
    "ARCHIVED",
    "cancelled",
]


class ConversationTurn(CognitiveContract):
    role: Literal["user", "assistant"]
    content: str


class SafePlanningError(CognitiveContract):
    stage: str
    error_type: str
    message: str
    retryable: bool = True
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class CognitivePlanningMetadata(CognitiveContract):
    engine_version: Literal["planning-engine-2"] = "planning-engine-2"
    planning_mode: PlanningMode
    current_stage: str
    agent_confidence: float | None = Field(default=None, ge=0, le=1)
    applied_user_rules: list[str] = Field(default_factory=list)
    repair_count: int = Field(default=0, ge=0, le=2)


UserAction = Literal[
    "create",
    "answer_question",
    "confirm_understanding",
    "give_feedback",
    "write_calendar",
    "restart",
    "continue_current_stage",
    "skip_current_stage",
    "cancel",
]


class CognitivePlanningState(TypedDict, total=False):
    session_id: str
    thread_id: str
    user_input: str
    conversation_history: list[ConversationTurn]
    request_context: dict[str, Any]
    user_action: UserAction
    status: CognitivePlanningStatus
    business_status: str
    runtime_status: str
    resume_node: str
    planning_mode: PlanningMode
    repair_count: int
    schedule_repair_count: int
    next_node: str
    understanding_snapshot: UnderstandingSnapshot
    understanding_updated: bool
    constraint_set: ConstraintSet
    context_pack: ContextPack
    plan_blueprint: PlanBlueprint
    plan_quality_report: QualityReport
    schedule_blueprint: ScheduleBlueprint
    schedule_quality_report: QualityReport
    calendar_proposal: CalendarProposal
    feedback_route: FeedbackRoute
    final_approval_bundle: FinalApprovalBundle
    execution_outcomes: list[ExecutionOutcome]
    replan_proposal: ReplanProposal
    learning_observations: list[LearningObservation]
    errors: list[SafePlanningError]
    response: Any


__all__ = [
    "CognitivePlanningMetadata",
    "CognitivePlanningState",
    "CognitivePlanningStatus",
    "ConversationTurn",
    "PlanningMode",
    "SafePlanningError",
    "UserAction",
]
