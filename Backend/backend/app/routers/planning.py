from fastapi import APIRouter

from ..schemas import (
    DailyReviewOut,
    DailyReviewRequest,
    CreatePlanningSessionRequest,
    GoalPlanOut,
    GoalPlanRequest,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
    PlanOut,
    RefinedTask,
    RefineTaskRequest,
    ReplanApplyRequest,
)
from ..services.langgraph_planning import get_deep_planning_orchestrator
from ..services.planning import PlanningService

router = APIRouter(prefix="/api/planning", tags=["planning"])

planning = PlanningService()


@router.post("/goal-plan", response_model=GoalPlanOut)
def create_goal_plan(payload: GoalPlanRequest) -> GoalPlanOut:
    return planning.create_goal_plan(payload)


@router.post("/sessions", response_model=PlanningSessionResponse)
def create_planning_session(payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().create_session(payload)


@router.post("/sessions/{session_id}/clarify", response_model=PlanningSessionResponse)
def clarify_planning_session(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().clarify(session_id, payload)


@router.post("/sessions/{session_id}/approve-design", response_model=PlanningSessionResponse)
def approve_planning_design(session_id: str) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().approve_design(session_id)


@router.post("/sessions/{session_id}/revise-design", response_model=PlanningSessionResponse)
def revise_planning_design(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().revise_design(session_id, payload)


@router.post("/sessions/{session_id}/approve-execution", response_model=PlanningSessionResponse)
def approve_planning_execution(session_id: str, payload: PlanningSessionTextRequest | None = None) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().approve_execution(session_id, accept_missing_resources=bool(payload and payload.accept_missing_resources))


@router.post("/sessions/{session_id}/revise-execution", response_model=PlanningSessionResponse)
def revise_planning_execution(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().revise_execution(session_id, payload)


@router.post("/sessions/{session_id}/feedback", response_model=PlanningSessionResponse)
def submit_planning_feedback(session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().submit_feedback(session_id, payload)


@router.post("/sessions/{session_id}/prepare-calendar-write", response_model=PlanningSessionResponse)
def prepare_planning_calendar_write(session_id: str, payload: PlanningSessionTextRequest | None = None) -> PlanningSessionResponse:
    return get_deep_planning_orchestrator().prepare_calendar_write(session_id, accept_missing_resources=bool(payload and payload.accept_missing_resources))


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
