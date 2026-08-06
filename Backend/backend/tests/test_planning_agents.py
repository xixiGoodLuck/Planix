import pytest

from app.db import get_conn
from app.schemas import CreatePlanningSessionRequest, PlanningSessionTextRequest
from app.services.deep_planning import DeepPlanningService
from app.services.langgraph_planning.runtime import LangGraphPlanningRuntime, get_deep_planning_orchestrator


def test_user_advocate_artifact_blocks_unclear_goal(client):
    service = DeepPlanningService()

    session = service.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", userInput="我要学 Go"))

    assert session.status == "needs_goal_clarification"
    contract = next(item for item in session.artifacts if item.artifact_type == "user_need_contract")
    assert contract.owner_agent == "User Advocate Agent"
    assert contract.status == "blocked"
    decision = next(item for item in session.decisions if item.agent == "User Advocate Agent")
    assert decision.decision == "request_user_input"
    assert session.user_need_contract is not None
    assert session.user_need_contract.can_move_to_design is False


def test_agent_runtime_records_handoffs_for_clear_goal(client):
    service = DeepPlanningService()

    session = service.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            userInput="Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
        )
    )

    artifact_types = {item.artifact_type for item in session.artifacts}
    assert {
        "user_need_contract",
        "memory_insight_brief",
        "resource_brief",
        "plan_design_proposal",
    } <= artifact_types
    assert any(item.agent == "Memory Insight Agent" and item.decision == "produce_artifact" for item in session.decisions)
    assert any(item.from_agent == "Resource Intelligence Agent" and item.to_agent == "Plan Co-Designer Agent" for item in session.messages)


def test_artifact_owner_enforced(client):
    service = DeepPlanningService()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO planning_sessions(id, thread_id, entry_point, status, user_input)
            VALUES ('session-owner-test', '', 'p_mode', 'needs_goal_clarification', 'test')
            """
        )

    with pytest.raises(ValueError):
        service.record_agent_artifact(
            "session-owner-test",
            owner_agent="Memory Insight Agent",
            artifact_type="user_need_contract",
            content={},
        )


def test_feedback_agent_requests_resource_revision(client):
    service = DeepPlanningService()
    session = service.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            userInput="Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
        )
    )
    session = service.approve_design(session.session_id)

    updated = service.revise_execution(session.session_id, PlanningSessionTextRequest(text="资源太难，看不懂"))

    assert any(item.artifact_type == "learning_patch" and item.owner_agent == "Feedback Evolution Agent" for item in updated.artifacts)
    assert any(
        item.from_agent == "Feedback Evolution Agent"
        and item.to_agent == "Resource Intelligence Agent"
        and item.message_type == "revision_request"
        for item in updated.messages
    )
    assert any(item.agent == "Execution Planner Agent" and item.decision == "revise_artifact" for item in updated.decisions)


def test_langgraph_planning_flag_selects_runtime_facade(client, monkeypatch):
    assert isinstance(get_deep_planning_orchestrator(), DeepPlanningService)

    monkeypatch.setenv("PLANIX_USE_LANGGRAPH_PLANNING", "1")

    assert isinstance(get_deep_planning_orchestrator(), LangGraphPlanningRuntime)


def test_langgraph_runtime_invokes_compiled_graph_when_available(client, monkeypatch):
    calls = {"count": 0}
    runtime = LangGraphPlanningRuntime(service=DeepPlanningService())

    class FakeGraph:
        def invoke(self, state):
            calls["count"] += 1
            response = runtime.service.create_session(
                CreatePlanningSessionRequest(
                    entryPoint="p_mode",
                    threadId=state["thread_id"],
                    userInput=state["user_input"],
                    context=state["context"],
                )
            )
            return {"response": response}

    monkeypatch.setattr("app.services.langgraph_planning.runtime.build_graph", lambda service: FakeGraph())

    session = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="thread-graph",
            userInput="Plan 30 days to learn Python for an AI internship, daily 30 minutes, project driven",
        )
    )

    assert calls["count"] == 1
    assert session.thread_id == "thread-graph"
    assert session.status == "waiting_design_approval"


def test_langgraph_runtime_falls_back_safely_when_graph_fails(client, monkeypatch):
    runtime = LangGraphPlanningRuntime(service=DeepPlanningService())
    monkeypatch.setattr(
        "app.services.langgraph_planning.runtime.build_graph",
        lambda service: (_ for _ in ()).throw(RuntimeError("boom with internal stack")),
    )

    session = runtime.create_session(CreatePlanningSessionRequest(entryPoint="p_mode", userInput="我要学 Go"))

    assert session.status == "needs_goal_clarification"
    assert session.user_need_contract is not None
    assert session.user_need_contract.clarification_questions
    assert any(
        item.message_type == "context_request"
        and "legacy planning service fallback" in item.reason
        and "boom" not in item.reason
        for item in session.messages
    )
