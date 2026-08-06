from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import Field

from .base import CognitiveContract
from .critique import PlanCritiqueReport
from .evidence import EvidencePack
from .execution import ExecutionBlueprint
from .goal_model import ConversationTurn, UserGoalModel
from .goal_completion import GoalCompletionResult
from .learning import PlanningLearningUpdate
from .strategy import StrategyPortfolio


PlanningMode = Literal["model_backed", "degraded_read_only", "blocked_model_unavailable"]
CognitivePlanningStatus = Literal[
    "needs_goal_clarification",
    "waiting_design_approval",
    "design_revision",
    "waiting_execution_approval",
    "execution_revision",
    "ready_to_write_calendar",
    "waiting_calendar_write_approval",
    "written_to_calendar",
    "learning_from_feedback",
    "blocked_model_unavailable",
    "MODEL_UNAVAILABLE",
    "cancelled",
]


class SafePlanningError(CognitiveContract):
    stage: str
    error_type: str
    message: str
    retryable: bool = True
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class CognitivePlanningMetadata(CognitiveContract):
    engine_version: Literal["cognitive-v2", "cognitive-os-v1"] = "cognitive-v2"
    planning_mode: PlanningMode
    current_stage: str
    agent_confidence: float | None = Field(default=None, ge=0, le=1)
    applied_user_rules: list[str] = Field(default_factory=list)
    repair_count: int = Field(default=0, ge=0, le=2)


UserAction = Literal[
    "create",
    "answer_question",
    "approve_strategy",
    "revise_strategy",
    "approve_execution",
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
    goal_model: UserGoalModel
    goal_completion: GoalCompletionResult
    evidence_pack: EvidencePack
    legacy_evidence_pack: EvidencePack
    evidence_requires_authority_refresh: bool
    reality_assessment: Any
    strategy_portfolio: StrategyPortfolio
    approved_strategy_id: str
    execution_blueprint: ExecutionBlueprint
    critique_report: PlanCritiqueReport
    learning_update: PlanningLearningUpdate
    user_action: UserAction
    status: CognitivePlanningStatus
    business_status: str
    runtime_status: str
    resume_node: str
    planning_mode: PlanningMode
    repair_count: int
    repair_loop: bool
    repair_instructions: list[dict[str, Any]]
    finalized_critique_artifact_id: str
    next_node: str
    errors: list[SafePlanningError]
    response: Any
