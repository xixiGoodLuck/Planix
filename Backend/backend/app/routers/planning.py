from fastapi import APIRouter

from ..schemas import (
    CreatePlanningSessionRequest,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
    PlanningExecutionFeedbackRequest,
    PlanningExecutionFeedbackResponse,
)
from ..cognitive_planning import get_planning_orchestrator

router = APIRouter(prefix="/api/planning", tags=["planning"])

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
def approve_planning_final(session_id: str) -> PlanningSessionResponse:
    return get_planning_orchestrator().approve_final(session_id)


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
