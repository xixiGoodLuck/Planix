from __future__ import annotations

from ...schemas import CreatePlanningSessionRequest, PlanningSessionTextRequest
from ..deep_planning import DeepPlanningService
from .state import PlanningGraphState, response_to_state


class PlanningGraphNodes:
    def __init__(self, service: DeepPlanningService):
        self.service = service

    def session_guard_node(self, state: PlanningGraphState) -> PlanningGraphState:
        # Planix DB remains the durable session guard. This node only mirrors
        # the current request into graph state before a conditional edge runs.
        return state

    def user_advocate_node(self, state: PlanningGraphState) -> PlanningGraphState:
        action = state.get("action", "create")
        if action == "clarify":
            response = self.service.clarify(
                state["session_id"],
                PlanningSessionTextRequest(
                    text=state.get("user_input", ""),
                    acceptMissingResources=bool(state.get("accept_missing_resources", False)),
                ),
            )
        else:
            response = self.service.create_session(
                CreatePlanningSessionRequest(
                    entryPoint="p_mode",
                    threadId=state.get("thread_id", ""),
                    userInput=state.get("user_input", ""),
                    context=state.get("context", {}),
                )
            )
        state.update(response_to_state(response))
        return state

    def memory_insight_node(self, state: PlanningGraphState) -> PlanningGraphState:
        # Existing DeepPlanningService create/clarify already persists this
        # artifact when the User Advocate approves moving to design.
        return state

    def resource_intelligence_node(self, state: PlanningGraphState) -> PlanningGraphState:
        return state

    def plan_codesigner_node(self, state: PlanningGraphState) -> PlanningGraphState:
        if state.get("action") == "revise_design":
            response = self.service.revise_design(
                state["session_id"],
                PlanningSessionTextRequest(
                    text=state.get("user_input", ""),
                    acceptMissingResources=bool(state.get("accept_missing_resources", False)),
                ),
            )
            state.update(response_to_state(response))
        return state

    def execution_planner_node(self, state: PlanningGraphState) -> PlanningGraphState:
        if state.get("action") == "approve_design":
            response = self.service.approve_design(state["session_id"])
            state.update(response_to_state(response))
        return state

    def wait_execution_approval_node(self, state: PlanningGraphState) -> PlanningGraphState:
        response = self.service.approve_execution(
            state["session_id"],
            accept_missing_resources=bool(state.get("accept_missing_resources", False)),
        )
        state.update(response_to_state(response))
        return state

    def feedback_evolution_node(self, state: PlanningGraphState) -> PlanningGraphState:
        request = PlanningSessionTextRequest(
            text=state.get("user_input", ""),
            acceptMissingResources=bool(state.get("accept_missing_resources", False)),
        )
        if state.get("action") == "submit_feedback":
            response = self.service.submit_feedback(state["session_id"], request)
        else:
            response = self.service.revise_execution(state["session_id"], request)
        state.update(response_to_state(response))
        return state

    def calendar_write_gate_node(self, state: PlanningGraphState) -> PlanningGraphState:
        response = self.service.prepare_calendar_write(
            state["session_id"],
            accept_missing_resources=bool(state.get("accept_missing_resources", False)),
        )
        state.update(response_to_state(response))
        return state
