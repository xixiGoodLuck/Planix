from __future__ import annotations

from typing import Any

import pytest

from app.cognitive_planning import CognitiveOSRuntime
from app.cognitive_planning.agents import CriticAgent
from app.schemas import CreatePlanningSessionRequest
from app.services.cognitive_planning.agents import CriticLearningAgent
from app.services.cognitive_planning.contracts import EVIDENCE_AUTHORITY_POLICY_VERSION
from app.services.cognitive_planning.orchestration import runtime as runtime_module

from planning_evals.test_cognitive_kernel import StubCognitiveModel


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'critic-policy-retry.db'}")
    return tmp_path


def _start(runtime: CognitiveOSRuntime, thread_id: str):
    return runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId=thread_id,
            userInput=(
                "Build a reviewable Python project in 30 days with three hours "
                "available daily"
            ),
        )
    )


def _review_artifacts(runtime: CognitiveOSRuntime, session_id: str):
    artifacts = runtime.agent_runtime.list_artifacts(session_id)
    executions = [item for item in artifacts if item.artifact_type == "execution_blueprint"]
    critiques = [item for item in artifacts if item.artifact_type == "critique_report"]
    return executions, critiques


def _violation() -> dict[str, Any]:
    return {
        "code": "half_time_contingency_promoted_to_primary_quota",
        "sourceKind": "repair_request",
        "sourceIndex": 0,
        "severity": "repair_request",
        "message": "The contingency was promoted into a primary quota.",
    }


def test_both_critic_agents_always_send_machine_readable_policy() -> None:
    model = StubCognitiveModel()
    goal = model._goal(
        {
            "conversationHistory": [
                {"role": "user", "content": "Build a reviewable Python project"}
            ]
        }
    )
    goal_payload = goal.model_dump(by_alias=True)
    reality = model._reality({"goalModel": goal_payload})
    evidence = model._evidence({"goalModel": goal_payload}).model_copy(
        update={"authority_policy_version": EVIDENCE_AUTHORITY_POLICY_VERSION}
    )
    strategy = model._strategy({"goalModel": goal_payload})
    execution = model._execution({"goalModel": goal_payload})
    policy = {"weeklyCapacity": {"explicitInGoal": False, "minutes": None}}
    violations = [_violation()]
    previous_critique = model._critique()
    repair_history = [{"status": "needs_repair", "score": 81}]

    CriticLearningAgent(model).critique(
        goal,
        evidence,
        strategy,
        execution,
        reality=reality,
        previous_critique=previous_critique,
        repair_history=repair_history,
        critic_policy=policy,
        critic_policy_violations=violations,
    )
    CriticAgent(model).critique(
        goal,
        evidence,
        strategy,
        execution,
        reality=reality,
        previous_critique=previous_critique,
        repair_history=repair_history,
        critic_policy=policy,
        critic_policy_violations=violations,
    )

    critic_payloads = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_critique"
    ]
    assert len(critic_payloads) == 2
    for payload in critic_payloads:
        assert payload["criticPolicy"]["enforcement"] == "deterministic_semantic_policy"
        assert payload["criticPolicy"]["reviewMode"] == "policy_repair"
        assert payload["criticPolicy"]["violationsToCorrect"] == violations
        assert payload["criticPolicy"]["mustReviewSameExecution"] is True
        assert payload["criticPolicyViolations"] == violations
        assert payload["previousCritiqueReport"] == previous_critique.model_dump(
            by_alias=True
        )
        assert payload["repairHistory"] == repair_history


def test_policy_violation_retries_once_without_persisting_first_output(
    isolated_db,
    monkeypatch,
) -> None:
    checks = iter(([_violation()], []))
    monkeypatch.setattr(
        runtime_module,
        "critic_policy_violations",
        lambda *_args, **_kwargs: next(checks),
    )
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "critic-policy-repaired")

    reviewed = runtime.approve_design(waiting_strategy.session_id)

    assert reviewed.status == "waiting_execution_approval"
    assert reviewed.cognitive_metadata.repair_count == 0
    assert model.critique_calls == 2
    executions, critiques = _review_artifacts(runtime, reviewed.session_id)
    assert len(executions) == len(critiques) == 1
    assert critiques[0].content_json["status"] == "passed"
    critic_calls = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_critique"
    ]
    assert len(critic_calls) == 2
    execution_key = (
        "executionBlueprint" if "executionBlueprint" in critic_calls[0] else "executionPlan"
    )
    assert critic_calls[0][execution_key] == critic_calls[1][execution_key]
    assert critic_calls[0]["criticPolicy"]["reviewMode"] == "initial_review"
    assert "criticPolicyViolations" not in critic_calls[0]
    assert critic_calls[1]["criticPolicyViolations"] == [_violation()]
    assert critic_calls[1]["criticPolicy"]["reviewMode"] == "policy_repair"

    decisions = [
        item
        for item in runtime.agent_runtime.list_decisions(reviewed.session_id)
        if item.agent == "Critic Agent"
    ]
    assert len(decisions) == 1
    usage = decisions[0].model_usage
    assert usage is not None
    assert usage.prompt_tokens == 200
    assert usage.completion_tokens == 100
    assert usage.total_tokens == 300
    assert usage.latency_ms == 10
    assert len(usage.attempts) == 2
    assert usage.attempts[0].automatic_retry is not True
    assert usage.attempts[1].automatic_retry is True
    assert usage.attempts[1].retry_reason == "critic_policy_repair"


def test_second_policy_violation_fails_closed_without_execution_repair(
    isolated_db,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "critic_policy_violations",
        lambda *_args, **_kwargs: [_violation()],
    )
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "critic-policy-blocked")

    blocked = runtime.approve_design(waiting_strategy.session_id)

    assert blocked.status == "MODEL_UNAVAILABLE"
    assert blocked.model_failure is not None
    assert blocked.model_failure.resume_node == "critic"
    assert blocked.model_failure.automatic_retry_attempted is True
    assert blocked.cognitive_metadata.repair_count == 0
    assert model.critique_calls == 2
    executions, critiques = _review_artifacts(runtime, blocked.session_id)
    assert len(executions) == len(critiques) == 1
    assert critiques[0].content_json["status"] == "blocked"
    assert (
        critiques[0].content_json["issues"][0]["evidence"]
        == "independent_critic_model_unavailable"
    )
    assert not [
        item
        for item in runtime.agent_runtime.list_decisions(blocked.session_id)
        if item.agent == "Critic Agent"
    ]
    assert not any(
        item.artifact_type == "execution_blueprint" and item.version > 1
        for item in runtime.agent_runtime.list_artifacts(blocked.session_id)
    )
    attempts = blocked.model_failure.attempts
    assert len(attempts) == 2
    assert blocked.model_failure.automatic_retry_attempted is True
    block_messages = [
        item
        for item in runtime.agent_runtime.list_messages(blocked.session_id)
        if item.message_type == "block"
    ]
    assert block_messages
    raw_attempts = block_messages[-1].payload_json["attempts"]
    assert raw_attempts[1]["automaticRetry"] is True
    assert raw_attempts[1]["retryReason"] == "critic_policy_repair"
