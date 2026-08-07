from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas import CommandChatRequest, PlanningSessionResponse
from app.services.command_agent import CommandAgentService


def _session(status: str) -> PlanningSessionResponse:
    return PlanningSessionResponse.model_validate(
        {
            "sessionId": "formal-session",
            "threadId": "formal-thread",
            "entryPoint": "p_mode",
            "status": status,
            "userInput": "Create a reviewable learning plan",
            "version": 1,
            "createdAt": "2026-08-07T00:00:00Z",
            "updatedAt": "2026-08-07T00:00:00Z",
        }
    )


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        ("waiting_understanding_confirmation", "确认理解", "confirm_understanding"),
        ("waiting_understanding_confirmation", "目标改成求职项目", "revise_understanding"),
        ("waiting_final_review", "把第二个任务拆开", "revise_final"),
        ("waiting_final_review", "确认并写入日历", "approve_final"),
    ],
)
def test_command_routes_only_formal_planning_actions(monkeypatch, status: str, message: str, expected: str) -> None:
    session = _session(status)
    monkeypatch.setattr(
        "app.services.command_agent.get_planning_orchestrator",
        lambda: SimpleNamespace(latest_for_thread=lambda _thread_id: session),
    )

    result = CommandAgentService()._planning_session_followup_action(
        "formal-thread",
        CommandChatRequest(message=message),
    )

    assert result is not None
    assert result[0] == expected


def test_planning_request_uses_the_single_formal_orchestrator(monkeypatch) -> None:
    calls: list[str] = []
    session = _session("waiting_understanding_confirmation")
    runtime = SimpleNamespace(
        create_session=lambda request: calls.append(request.user_input) or session,
    )
    monkeypatch.setattr(
        "app.services.command_agent.get_planning_orchestrator",
        lambda: runtime,
    )

    service = CommandAgentService()
    monkeypatch.setattr(
        service,
        "_stream_planning_session_snapshot",
        lambda *_args, **_kwargs: iter(["formal-session-started"]),
    )
    events = list(
        service._stream_planning_start(
            "formal-thread",
            CommandChatRequest(message="Create a reviewable learning plan"),
        )
    )

    assert calls == ["Create a reviewable learning plan"]
    assert events == ["formal-session-started"]
