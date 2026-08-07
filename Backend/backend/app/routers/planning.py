from fastapi import APIRouter

from ..schemas import (
    DailyReviewOut,
    DailyReviewRequest,
    CreatePlanningSessionRequest,
    GoalPlanOut,
    GoalPlanRequest,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
    PlanningExecutionFeedbackRequest,
    PlanningExecutionFeedbackResponse,
    PlanOut,
    RefinedTask,
    RefineTaskRequest,
    ReplanApplyRequest,
)
from ..cognitive_planning import get_planning_orchestrator
from ..services.planning import PlanningService

router = APIRouter(prefix="/api/planning", tags=["planning"])

planning = PlanningService()


@router.post("/goal-plan", response_model=GoalPlanOut)
def create_goal_plan(payload: GoalPlanRequest) -> GoalPlanOut:
    return planning.create_goal_plan(payload)


@router.post("/sessions", response_model=PlanningSessionResponse)
def create_planning_session(payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
    return get_planning_orchestrator().create_session(payload)


@router.post("/sessions/{session_id}/answer-understanding", response_model=PlanningSessionResponse)
def answer_planning_understanding(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_planning_orchestrator().answer_understanding(session_id, payload)


@router.post("/sessions/{session_id}/confirm-understanding", response_model=PlanningSessionResponse)
def confirm_planning_understanding(session_id: str) -> PlanningSessionResponse:
    return get_planning_orchestrator().confirm_understanding(session_id)


@router.post("/sessions/{session_id}/revise-understanding", response_model=PlanningSessionResponse)
def revise_planning_understanding(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_planning_orchestrator().revise_understanding(session_id, payload)


@router.post("/sessions/{session_id}/approve-final", response_model=PlanningSessionResponse)
def approve_planning_final(session_id: str, payload: PlanningSessionTextRequest | None = None) -> PlanningSessionResponse:
    return get_planning_orchestrator().approve_final(session_id, accept_missing_resources=bool(payload and payload.accept_missing_resources))


@router.post("/sessions/{session_id}/revise-final", response_model=PlanningSessionResponse)
def revise_planning_final(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_planning_orchestrator().revise_final(session_id, payload)


@router.post(
    "/sessions/{session_id}/execution-feedback",
    response_model=PlanningExecutionFeedbackResponse,
)
def record_planning_execution_feedback(
    session_id: str,
    payload: PlanningExecutionFeedbackRequest,
) -> PlanningExecutionFeedbackResponse:
    return get_planning_orchestrator().record_execution_feedback(session_id, payload)


@router.post("/daily-review", response_model=DailyReviewOut)
def create_daily_review(payload: DailyReviewRequest) -> DailyReviewOut:
    return planning.create_daily_review(payload)


@router.post("/refine-task", response_model=RefinedTask)
def refine_task(payload: RefineTaskRequest) -> RefinedTask:
    return planning.refine_task(payload)


@router.get("/daily-review", response_model=DailyReviewOut)
def get_daily_review(date: str) -> DailyReviewOut:
    return planning.get_daily_review(date)


@router.post("/replan/apply", response_model=list[PlanOut])
def apply_replan(payload: ReplanApplyRequest) -> list[PlanOut]:
    return planning.apply_replan(payload)
