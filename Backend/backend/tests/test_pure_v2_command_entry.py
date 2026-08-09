import json

from app.cognitive_planning.agents import AgentResult
from app.cognitive_planning.contracts import (
    SemanticItem,
    UnderstandingQuestion,
    UnderstandingReadiness,
    UnderstandingSnapshot,
)
from app.cognitive_planning.runtime import CognitiveOSRuntime
from app.db import get_conn


class CommandEntryUnderstandingModel:
    def __init__(self, *, ready: bool):
        self.ready = ready
        self.calls: list[str] = []

    def complete_contract(self, *, stage, contract_type, **_kwargs):
        assert contract_type is UnderstandingSnapshot
        self.calls.append(stage)
        source = SemanticItem(
            id="fact-1",
            key="raw_goal",
            statement="User supplied the goal in the current command turn",
            sourceType="user_confirmed",
            sourceRef="turn:1",
            mutationPolicy="immutable",
        )
        if self.ready:
            artifact = UnderstandingSnapshot(
                goalSummary="Prepare for an AI application development internship in three months",
                facts=[source],
                constraints=[SemanticItem(
                    id="capacity-1",
                    key="weekly_capacity",
                    statement="10 hours per week",
                    sourceType="user_confirmed",
                    sourceRef="turn:1",
                    mutationPolicy="immutable",
                )],
                successSignals=[SemanticItem(
                    id="success-1",
                    key="portfolio_and_applications",
                    statement="A demonstrable Agent project is ready before applications begin in week six",
                    sourceType="user_confirmed",
                    sourceRef="turn:1",
                    mutationPolicy="immutable",
                )],
                readiness=UnderstandingReadiness(readyForConfirmation=True),
                sourceRefs=["turn:1"],
            )
        else:
            artifact = UnderstandingSnapshot(
                goalSummary="去北京",
                facts=[source],
                unknowns=[SemanticItem(
                    id="unknown-purpose",
                    key="purpose",
                    statement="去北京的目的尚未说明",
                    sourceType="user_confirmed",
                    sourceRef="turn:1",
                )],
                nextQuestion=UnderstandingQuestion(
                    question="你去北京的主要目的是什么？",
                    whyThisQuestionMatters="目的会决定计划内容与约束。",
                    expectedDecisionImpact="用于确定后续计划范围。",
                    priority="blocking",
                    answerOptions=["工作", "学习", "旅行", "其他"],
                ),
                readiness=UnderstandingReadiness(readyForConfirmation=False),
                sourceRefs=["turn:1"],
            )
        return AgentResult(
            artifact=artifact,
            model_usage={"provider": "test", "model": "v2", "mode": "llm", "taskType": "planning_understanding"},
        )


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _run_fresh_goal(client, monkeypatch, *, message: str, ready: bool):
    model = CommandEntryUnderstandingModel(ready=ready)
    runtime = CognitiveOSRuntime(model_client=model)
    monkeypatch.setattr("app.services.command_agent.get_planning_orchestrator", lambda: runtime)
    response = client.post("/api/command/chat", json={"mode": "auto", "message": message})
    assert response.status_code == 200
    events = _events(response)
    started = next(event for event in events if event["type"] == "planning_session_started")
    status = next(event for event in reversed(events) if event["type"] == "planning_session_status")
    assert not any(event["type"] == "command_decision" for event in events)
    assert not any(event["type"] in {"runtime_started", "runtime_event", "draft_created"} for event in events)
    assert model.calls == ["understanding"]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT thread_id, user_input FROM planning_sessions WHERE id = %s",
            (started["sessionId"],),
        ).fetchone()
    assert row is not None
    assert row["thread_id"] == events[-1]["threadId"]
    assert row["user_input"] == message
    return status


def test_raw_destination_enters_fresh_v2_session_and_v2_asks_purpose(client, monkeypatch):
    status = _run_fresh_goal(client, monkeypatch, message="我要去北京", ready=False)
    assert status["status"] == "needs_goal_clarification"
    understanding = status["data"]["understandingSnapshot"]
    assert understanding["goalSummary"] == "去北京"
    assert understanding["unknowns"][0]["key"] == "purpose"
    assert understanding["nextQuestion"]["question"] == "你去北京的主要目的是什么？"


def test_complete_internship_goal_enters_fresh_v2_session_on_first_request(client, monkeypatch):
    message = (
        "我想在三个月内准备AI应用开发实习。我会Python、FastAPI和React，"
        "每周可以投入10小时，希望前五周完成一个可以展示的Agent项目，第六周开始投递。"
    )
    status = _run_fresh_goal(client, monkeypatch, message=message, ready=True)
    assert status["status"] == "waiting_understanding_confirmation"
    assert status["data"]["understandingSnapshot"]["readiness"]["readyForConfirmation"] is True
