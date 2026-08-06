from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from ...schemas import CreatePlanningSessionRequest, PlanningSessionResponse, PlanningSessionTextRequest
from ..deep_planning import DeepPlanningService
from .graph import build_graph
from .state import PlanningGraphAction, PlanningGraphState


TRUE_VALUES = {"1", "true", "yes", "on"}


def use_langgraph_planning() -> bool:
    return os.getenv("PLANIX_USE_LANGGRAPH_PLANNING", "").strip().lower() in TRUE_VALUES


def get_deep_planning_orchestrator() -> DeepPlanningService | "LangGraphPlanningRuntime":
    from ...cognitive_planning import CognitiveOSRuntime, use_cognitive_os
    from ..cognitive_planning import CognitivePlanningRuntime, use_cognitive_planning

    if use_cognitive_os():
        return CognitiveOSRuntime()
    if use_cognitive_planning():
        return CognitivePlanningRuntime()
    if use_langgraph_planning():
        return LangGraphPlanningRuntime()
    return DeepPlanningService()


class LangGraphPlanningRuntime:
    def __init__(self, service: DeepPlanningService | None = None):
        self.service = service or DeepPlanningService()

    def run(
        self,
        *,
        action: PlanningGraphAction,
        thread_id: str = "",
        user_input: str = "",
        session_id: str = "",
        context: dict[str, Any] | None = None,
        accept_missing_resources: bool = False,
    ) -> PlanningSessionResponse:
        state: PlanningGraphState = {
            "action": action,
            "thread_id": thread_id,
            "user_input": user_input,
            "session_id": session_id,
            "context": context or {},
            "accept_missing_resources": accept_missing_resources,
        }
        try:
            graph = build_graph(self.service)
            result = graph.invoke(state)
            response = result.get("response") if isinstance(result, dict) else None
            if not isinstance(response, PlanningSessionResponse):
                raise RuntimeError("LangGraph planning completed without a PlanningSessionResponse")
            return response
        except HTTPException:
            raise
        except Exception as exc:
            response = self._fallback(
                action=action,
                thread_id=thread_id,
                user_input=user_input,
                session_id=session_id,
                context=context or {},
                accept_missing_resources=accept_missing_resources,
            )
            self._record_safe_fallback_message(response, exc)
            return self.service.get_session(response.session_id)

    def _fallback(
        self,
        *,
        action: PlanningGraphAction,
        thread_id: str,
        user_input: str,
        session_id: str,
        context: dict[str, Any],
        accept_missing_resources: bool,
    ) -> PlanningSessionResponse:
        request = PlanningSessionTextRequest(text=user_input, acceptMissingResources=accept_missing_resources)
        if action == "create":
            return self.service.create_session(
                CreatePlanningSessionRequest(
                    entryPoint="p_mode",
                    threadId=thread_id,
                    userInput=user_input,
                    context=context,
                )
            )
        if action == "clarify":
            return self.service.clarify(session_id, request)
        if action == "revise_design":
            return self.service.revise_design(session_id, request)
        if action == "approve_design":
            return self.service.approve_design(session_id)
        if action == "approve_execution":
            return self.service.approve_execution(session_id, accept_missing_resources=accept_missing_resources)
        if action == "submit_feedback":
            return self.service.submit_feedback(session_id, request)
        if action == "revise_execution":
            return self.service.revise_execution(session_id, request)
        if action == "prepare_calendar_write":
            return self.service.prepare_calendar_write(session_id, accept_missing_resources=accept_missing_resources)
        raise RuntimeError(f"Unsupported planning graph action: {action}")

    def _record_safe_fallback_message(self, response: PlanningSessionResponse, exc: Exception) -> None:
        reason = "LangGraph planning runtime unavailable; used the legacy planning service fallback."
        if exc.__class__.__name__ not in {"LangGraphUnavailable", "ModuleNotFoundError"}:
            reason = "LangGraph planning runtime failed safely; used the legacy planning service fallback."
        try:
            self.service.agent_runtime.record_message(
                response.session_id,
                from_agent="User Advocate Agent",
                to_agent="Plan Co-Designer Agent",
                message_type="context_request",
                reason=reason,
                payload={"fallback": "legacy_deep_planning_service", "errorType": exc.__class__.__name__},
                resolved=True,
            )
        except Exception:
            # Fallback observability should never break the user-facing session.
            return

    def create_session(self, payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
        return self.run(
            action="create",
            thread_id=payload.thread_id or "",
            user_input=payload.user_input,
            context=payload.context,
        )

    def clarify(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        return self.run(
            action="clarify",
            session_id=session_id,
            user_input=payload.text,
            accept_missing_resources=payload.accept_missing_resources,
        )

    def revise_design(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        return self.run(
            action="revise_design",
            session_id=session_id,
            user_input=payload.text,
            accept_missing_resources=payload.accept_missing_resources,
        )

    def approve_design(self, session_id: str) -> PlanningSessionResponse:
        return self.run(action="approve_design", session_id=session_id)

    def approve_execution(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        return self.run(
            action="approve_execution",
            session_id=session_id,
            accept_missing_resources=accept_missing_resources,
        )

    def revise_execution(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        return self.run(
            action="revise_execution",
            session_id=session_id,
            user_input=payload.text,
            accept_missing_resources=payload.accept_missing_resources,
        )

    def submit_feedback(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        return self.run(
            action="submit_feedback",
            session_id=session_id,
            user_input=payload.text,
            accept_missing_resources=payload.accept_missing_resources,
        )

    def prepare_calendar_write(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        return self.run(
            action="prepare_calendar_write",
            session_id=session_id,
            accept_missing_resources=accept_missing_resources,
        )

    def latest_for_thread(self, thread_id: str) -> PlanningSessionResponse | None:
        return self.service.latest_for_thread(thread_id)

    def get_session(self, session_id: str) -> PlanningSessionResponse:
        return self.service.get_session(session_id)

    def mark_calendar_written(self, session_id: str) -> None:
        self.service.mark_calendar_written(session_id)

    def execution_to_structured_plan(self, session: PlanningSessionResponse) -> dict[str, Any]:
        return self.service.execution_to_structured_plan(session)
