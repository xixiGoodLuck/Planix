import json
from types import SimpleNamespace

from app.cognitive_planning.control_intent import detect_planning_control_intent
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


def test_natural_understanding_confirmation_routes_to_formal_confirmation(monkeypatch):
    session = _session("waiting_understanding_confirmation")
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: SimpleNamespace(latest_for_thread=lambda _id: session))

    text = "可以，就按这个理解继续。"
    assert detect_planning_control_intent(text) == "approve_current_stage"
    action = CommandAgentService()._followup_action("formal-thread", CommandChatRequest(message=text))

    assert action and action[0] == "confirm_understanding"


def test_natural_chinese_cancel_sentence_is_a_control_intent():
    assert detect_planning_control_intent("先取消这个计划。") == "cancel_planning"


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


def test_natural_final_confirmation_routes_to_calendar_preview(monkeypatch):
    session = _session("waiting_final_review")
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: SimpleNamespace(latest_for_thread=lambda _id: session))

    text = "这个计划可以，就按这个来。"
    assert detect_planning_control_intent(text) == "approve_current_stage"
    action = CommandAgentService()._followup_action("formal-thread", CommandChatRequest(message=text))

    assert action and action[0] == "approve_final"


def test_command_first_input_uses_single_formal_orchestrator(monkeypatch):
    calls = []
    session = _session("waiting_understanding_confirmation")
    runtime = SimpleNamespace(
        prepare_session=lambda request: calls.append(("prepare", request.user_input)) or session.session_id,
        run_prepared_session=lambda session_id: calls.append(("run", session_id)) or session,
        harness=SimpleNamespace(repository=SimpleNamespace(load=lambda _session_id: None)),
    )
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: runtime)
    service = CommandAgentService()
    monkeypatch.setattr(service, "_planning_event", lambda *_args, **_kwargs: json.dumps({"type": "planning_session_started"}) + "\n")
    monkeypatch.setattr(service, "_stream_snapshot", lambda *_args, **_kwargs: iter([json.dumps({"type": "planning_session_status"}) + "\n"]))

    events = [json.loads(line) for line in service._stream_start("formal-thread", CommandChatRequest(message="Create a reviewable learning plan"))]

    assert [event["type"] for event in events] == ["planning_session_started", "planning_progress", "planning_session_status"]
    assert calls == [("prepare", "Create a reviewable learning plan"), ("run", "formal-session")]
