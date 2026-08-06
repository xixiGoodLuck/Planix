from __future__ import annotations

from .state import PlanningGraphState


def route_after_session_guard(state: PlanningGraphState) -> str:
    action = state.get("action", "create")
    if action in {"create", "clarify"}:
        return "user_advocate"
    if action == "revise_design":
        return "plan_codesigner"
    if action == "approve_design":
        return "execution_planner"
    if action == "approve_execution":
        return "wait_execution_approval"
    if action in {"revise_execution", "submit_feedback"}:
        return "feedback_evolution"
    if action == "prepare_calendar_write":
        return "calendar_write_gate"
    return "user_advocate"


def route_after_user_advocate(state: PlanningGraphState) -> str:
    response = state.get("response")
    if response and response.status == "waiting_design_approval":
        return "memory_insight"
    return "__end__"


def route_after_feedback(state: PlanningGraphState) -> str:
    response = state.get("response")
    patch = response.learning_patch if response else None
    immediate = patch.immediate_patch if patch else None
    if immediate and immediate.target in {"resource", "schedule"}:
        return "resource_intelligence"
    if immediate and immediate.target == "design":
        return "plan_codesigner"
    return "execution_planner"
