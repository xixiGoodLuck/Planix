from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.harness import (
    AgentScheduler,
    HarnessRuntime,
    RecoveryAction,
    SchedulerAction,
    SchedulerDecision,
    repair_json_object,
)
from app.harness.adapters import build_cognitive_agent_registry
from app.harness.persistence import HarnessStateRepository
from app.services.cognitive_planning.agents import CognitiveModelClient, PlanningModelUnavailable
from app.services.cognitive_planning.contracts import SafePlanningError
from app.services.cognitive_planning.orchestration.persistence import CognitivePlanningPersistence
from app.services.llm import LlmResult
from app.services.planning_agent_runtime import PlanningAgentRuntime


class _MinimalContract(BaseModel):
    value: str


class _FakeLlm:
    def __init__(self, content: str):
        self.content = content

    def complete(self, *args, **kwargs):
        return LlmResult(content=self.content, provider="deepseek", model="stub"), None


@pytest.fixture()
def runtime_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'harness-runtime.db'}")


def _session(thread: str) -> str:
    return CognitivePlanningPersistence().create(
        thread_id=thread,
        user_input="Harness test",
    )


def test_deterministic_json_repair_changes_syntax_only_and_schema_stays_fail_closed() -> None:
    repaired = repair_json_object('prefix ```json\n{"value":"kept",}\n``` suffix')
    assert repaired.value == {"value": "kept"}
    assert "extract_balanced_object" in repaired.operations

    repaired = repair_json_object('```json\n{"value":"kept",}\n```')
    assert repaired.value == {"value": "kept"}
    assert repaired.repaired is True
    assert "remove_trailing_commas" in repaired.operations

    result = CognitiveModelClient(llm=_FakeLlm('{"value":"kept",}')).complete_contract(
        stage="goal_modeling",
        task_type="planning_goal_model",
        feature="harness_json_repair",
        system="unchanged agent prompt",
        payload={},
        contract_type=_MinimalContract,
    )
    assert result.artifact.value == "kept"

    with pytest.raises(PlanningModelUnavailable) as exc_info:
        CognitiveModelClient(llm=_FakeLlm('{"other":"untouched",}')).complete_contract(
            stage="goal_modeling",
            task_type="planning_goal_model",
            feature="harness_json_schema_failure",
            system="unchanged agent prompt",
            payload={},
            contract_type=_MinimalContract,
        )
    assert exc_info.value.error.error_type == "invalid_model_output"


def test_scheduler_owns_resume_critic_and_repair_decisions() -> None:
    scheduler = AgentScheduler()
    recovered = scheduler.from_guard(
        {
            "user_action": "continue_current_stage",
            "status": "MODEL_UNAVAILABLE",
            "resume_node": "strategy",
        }
    )
    assert recovered.action.value == "recover"
    assert recovered.agent_id == "strategy"
    assert recovered.next_node == "strategy"

    critic = scheduler.after_execution(
        {"planning_mode": "model_backed", "execution_blueprint": object()}
    )
    assert critic.next_node == "critic"
    assert critic.agent_id == "critic"

    repair = scheduler.after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(status="needs_repair"),
            "repair_count": 0,
        }
    )
    assert repair.action.value == "repair"
    assert repair.next_node == "repair"

    blocked_repair = scheduler.after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(
                status="blocked",
                repair_requests=[SimpleNamespace(target_agent="execution_designer")],
            ),
            "repair_count": 0,
        }
    )
    assert blocked_repair.action.value == "repair"
    assert blocked_repair.next_node == "repair"

    blocked_terminal = scheduler.after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(status="blocked", repair_requests=[]),
            "repair_count": 0,
        }
    )
    assert blocked_terminal.action.value == "wait_user"
    assert blocked_terminal.next_node == "wait_for_execution_approval"

    inconsistent_pass = scheduler.after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(
                status="passed",
                score=95,
                calendar_writable=True,
                issues=[SimpleNamespace(severity="major")],
                repair_requests=[],
            ),
            "repair_count": 0,
        }
    )
    assert inconsistent_pass.action.value == "wait_user"
    assert inconsistent_pass.reason_code == "critic_inconsistent_pass"

    inconsistent_repair = scheduler.after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(
                status="passed",
                score=95,
                calendar_writable=True,
                issues=[],
                repair_requests=[SimpleNamespace(target_agent="execution_designer")],
            ),
            "repair_count": 0,
        }
    )
    assert inconsistent_repair.action.value == "repair"
    assert inconsistent_repair.next_node == "repair"


@pytest.mark.parametrize("reason_code", ["reality_judgment", "evidence_judgment"])
def test_non_critic_user_wait_never_records_a_critic_failure(runtime_db, reason_code: str) -> None:
    session_id = _session(f"harness-{reason_code}")
    harness = HarnessRuntime()

    selected = harness.record_scheduler_decision(
        {
            "session_id": session_id,
            "planning_mode": "model_backed",
            "goal_completion": SimpleNamespace(complete=True, blocking_unknowns=[]),
        },
        SchedulerDecision(
            action=SchedulerAction.WAIT_USER,
            next_node="wait_for_goal_answer",
            reason_code=reason_code,
        ),
    )

    assert selected == "wait_for_goal_answer"
    policy = HarnessStateRepository().recover(session_id).last_policy_decision
    assert policy is not None
    assert policy.subject == "user_question"
    assert policy.action == "wait_user"
    assert policy.required_gates == ()
    assert policy.failed_gates == ()


def test_critic_user_wait_keeps_the_critic_failure_gate(runtime_db) -> None:
    session_id = _session("harness-critic-wait")
    harness = HarnessRuntime()

    selected = harness.record_scheduler_decision(
        {
            "session_id": session_id,
            "planning_mode": "model_backed",
            "goal_completion": SimpleNamespace(complete=True, blocking_unknowns=[]),
        },
        SchedulerDecision(
            action=SchedulerAction.WAIT_USER,
            next_node="wait_for_execution_approval",
            reason_code="critic_blocked",
        ),
    )

    assert selected == "wait_for_execution_approval"
    policy = HarnessStateRepository().recover(session_id).last_policy_decision
    assert policy is not None
    assert policy.subject == "critic_review"
    assert policy.action == "wait_user"
    assert policy.required_gates == ("critic",)
    assert policy.failed_gates == ("critic",)


def test_every_harness_agent_declares_artifact_contract_permissions_and_failures() -> None:
    registry = build_cognitive_agent_registry()
    contracts = {item.agent_id: item for item in registry.list()}
    assert set(contracts) == {
        "goal_intelligence",
        "goal_completion",
        "reality",
        "evidence",
        "strategy",
        "execution",
        "critic",
        "feedback_learning",
        "memory_evaluator",
    }
    for contract in contracts.values():
        assert contract.responsibility.strip()
        assert contract.output_artifact
        assert "write_artifact" in contract.permissions
        assert contract.failure_conditions
    assert "propose_memory" in contracts["feedback_learning"].permissions
    assert "evaluate_memory" not in contracts["feedback_learning"].permissions
    assert contracts["memory_evaluator"].input_artifacts == (
        "planning_learning_update",
    )
    assert contracts["memory_evaluator"].permissions == (
        "read_artifact",
        "write_artifact",
        "evaluate_memory",
    )


def test_harness_wrapper_persists_invocation_artifact_heads_and_completion(runtime_db) -> None:
    session_id = _session("harness-success")
    artifacts = PlanningAgentRuntime()
    artifacts.record_artifact(
        session_id,
        owner_agent="Goal Intelligence Agent",
        artifact_type="user_goal_model",
        content={"goalStatement": "typed goal"},
    )
    harness = HarnessRuntime(
        registry=build_cognitive_agent_registry(),
        artifact_runtime=artifacts,
    )
    called = 0

    def goal_completion_node(state):
        nonlocal called
        called += 1
        artifacts.record_artifact(
            session_id,
            owner_agent="Goal Completion Judge",
            artifact_type="goal_completion",
            content={
                "complete": True,
                "blockingUnknowns": [],
                "optionalUnknowns": [],
                "nextStage": "strategy",
            },
            status="approved",
        )
        state["goal_completion"] = SimpleNamespace(complete=True)
        return state

    wrapped = harness.wrap_agent_node("goal_completion", goal_completion_node)
    result = wrapped(
        {
            "session_id": session_id,
            "goal_model": {"goalStatement": "typed goal"},
            "planning_mode": "model_backed",
        }
    )

    assert called == 1
    assert result["goal_completion"].complete is True
    restored = HarnessStateRepository().recover(session_id)
    assert restored.pending_agent is None
    assert "goal_completion" in restored.completed_agents
    assert restored.artifact_versions["user_goal_model"] == 1
    assert restored.artifact_versions["goal_completion"] == 1
    assert restored.checkpoint.artifact_refs["goal_completion"].status == "approved"
    invocation_events = [
        event
        for event in HarnessStateRepository().events(session_id)
        if event.event_type == "agent_invocation"
    ]
    assert [event.decision for event in invocation_events] == ["running", "succeeded"]


def test_harness_contract_missing_artifact_fails_closed_without_invoking_agent(runtime_db) -> None:
    session_id = _session("harness-missing-input")
    harness = HarnessRuntime(registry=build_cognitive_agent_registry())
    called = False

    def forbidden_node(state):
        nonlocal called
        called = True
        return state

    result = harness.wrap_agent_node("goal_completion", forbidden_node)(
        {
            "session_id": session_id,
            "goal_model": {"unpersisted": True},
            "planning_mode": "model_backed",
        }
    )

    assert called is False
    assert result["status"] == "MODEL_UNAVAILABLE"
    assert result["runtime_status"] == "retry_required"
    restored = HarnessStateRepository().recover(session_id)
    assert restored.lifecycle == "blocked"
    assert restored.pending_agent == "goal_completion"
    assert restored.errors[-1].error_type == "missing_input_artifact"


def test_harness_failure_records_recovery_and_model_route_attempts(runtime_db) -> None:
    session_id = _session("harness-recovery")
    harness = HarnessRuntime(registry=build_cognitive_agent_registry())

    def failed_goal(state):
        error = SafePlanningError(
            stage="goal_intelligence",
            errorType="auth_error",
            message="provider rejected credentials",
            retryable=False,
            attempts=[
                {
                    "provider": "deepseek",
                    "model": "primary",
                    "status": "error",
                    "errorType": "auth_error",
                    "latencyMs": 7,
                },
                {
                    "provider": "kimi",
                    "model": "fallback",
                    "status": "skipped",
                    "errorType": "missing_api_key",
                    "latencyMs": 0,
                },
            ],
        )
        state["planning_mode"] = "blocked_model_unavailable"
        state["errors"] = [error]
        return state

    harness.wrap_agent_node("goal_intelligence", failed_goal)(
        {"session_id": session_id, "planning_mode": "model_backed"}
    )
    restored = HarnessStateRepository().recover(session_id)
    assert restored.lifecycle == "blocked"
    assert restored.pending_agent == "goal_intelligence"
    assert restored.recovery_actions[-1].action == "model_switch"
    events = HarnessStateRepository().events(session_id)
    assert len([event for event in events if event.event_type == "model_routing"]) == 2
    assert any(
        event.event_type == "recovery_action" and event.decision == "model_switch"
        for event in events
    )

    selected = harness.record_scheduler_decision(
        {"session_id": session_id, "planning_mode": "model_backed"},
        SchedulerDecision(
            action=SchedulerAction.INVOKE_AGENT,
            next_node="strategy",
            reason_code="manual_bypass_attempt",
            agent_id="strategy",
        ),
    )
    assert selected == "__end__"
    blocked = HarnessStateRepository().recover(session_id)
    assert blocked.last_policy_decision is not None
    assert blocked.last_policy_decision.action == "block_runtime"
    assert blocked.last_decision is not None
    assert blocked.last_decision.directive == "block_runtime"


def test_version_bound_approval_is_restored_and_invalidated_by_repair(runtime_db) -> None:
    session_id = _session("harness-approval")
    artifacts = PlanningAgentRuntime()
    artifacts.record_artifact(
        session_id,
        owner_agent="Strategy Agent",
        artifact_type="strategy_portfolio",
        content={"recommendedStrategyId": "s1"},
    )
    harness = HarnessRuntime(artifact_runtime=artifacts)
    harness.record_approval(session_id, "strategy")
    approved = HarnessStateRepository().recover(session_id)
    assert approved.approvals[-1].status == "approved"
    assert approved.approvals[-1].artifact.version == 1

    artifacts.record_artifact(
        session_id,
        owner_agent="Strategy Agent",
        artifact_type="strategy_portfolio",
        content={"recommendedStrategyId": "s2"},
    )
    harness.bootstrap({"session_id": session_id})
    repaired = HarnessStateRepository().recover(session_id)
    assert repaired.approvals[-1].status == "invalidated"
    assert repaired.checkpoint.artifact_refs["strategy_portfolio"].version == 2


def test_recovery_manager_keeps_exact_failed_stage() -> None:
    harness = HarnessRuntime()
    decision = harness.decide_model_failure(
        {"goal_completion": SimpleNamespace(complete=True)},
        SafePlanningError(
            stage="strategy_design",
            errorType="timeout",
            message="timed out",
            retryable=True,
        ),
    )
    assert decision.action == RecoveryAction.RETRY_STAGE
    assert decision.resume_node == "strategy"
    assert decision.business_status == "strategy_pending"
