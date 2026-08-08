from types import SimpleNamespace

from app.schemas import CommandChatRequest, PlanningSessionResponse
from app.services.command_agent import CommandAgentService


def _session(status: str) -> PlanningSessionResponse:
    return PlanningSessionResponse.model_validate({
        "sessionId": "formal-session", "threadId": "formal-thread", "entryPoint": "p_mode",
        "status": status, "userInput": "Create a reviewable learning plan", "version": 1,
        "createdAt": "2026-08-07T00:00:00Z", "updatedAt": "2026-08-07T00:00:00Z",
    })


def test_command_followup_routes_only_to_formal_understanding(monkeypatch):
    session = _session("waiting_understanding_confirmation")
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: SimpleNamespace(latest_for_thread=lambda _id: session))
    action = CommandAgentService()._followup_action("formal-thread", CommandChatRequest(message="确认当前理解"))
    assert action and action[0] == "confirm_understanding"


def test_command_final_approval_text_routes_to_calendar_preview(monkeypatch):
    session = _session("waiting_final_review")
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: SimpleNamespace(latest_for_thread=lambda _id: session))
    action = CommandAgentService()._followup_action("formal-thread", CommandChatRequest(message="批准最终计划并写入日历"))
    assert action and action[0] == "approve_final"


def test_command_final_confirmation_is_separate_from_calendar_permission(monkeypatch):
    session = _session("waiting_final_review")
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: SimpleNamespace(latest_for_thread=lambda _id: session))
    action = CommandAgentService()._followup_action("formal-thread", CommandChatRequest(message="确认最终计划"))
    assert action and action[0] == "approve_final"


def test_command_first_input_uses_single_formal_orchestrator(monkeypatch):
    calls = []
    session = _session("waiting_understanding_confirmation")
    runtime = SimpleNamespace(create_session=lambda request: calls.append(request.user_input) or session)
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: runtime)
    service = CommandAgentService()
    monkeypatch.setattr(service, "_stream_snapshot", lambda *_args, **_kwargs: iter(["formal-session-started"]))
    assert list(service._stream_start("formal-thread", CommandChatRequest(message="Create a reviewable learning plan"))) == ["formal-session-started"]
    assert calls == ["Create a reviewable learning plan"]
