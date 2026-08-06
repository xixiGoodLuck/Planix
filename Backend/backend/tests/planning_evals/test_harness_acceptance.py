from __future__ import annotations

from collections import Counter

import pytest
from fastapi import HTTPException

from app.cognitive_planning import CognitiveOSRuntime
from app.cognitive_planning.memory import UserModelMemoryRepository
from app.harness.persistence import HarnessStateRepository
from app.schemas import CreatePlanningSessionRequest, PlanningSessionTextRequest

from .test_cognitive_kernel import StubCognitiveModel, UnavailableCognitiveModel
from .test_phase73_hotfix import SemanticGoProgressionModel, _full_go_request


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'harness-acceptance.db'}")
    return tmp_path


def _heads(session) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for item in session.artifacts:
        current = result.get(item.artifact_type)
        if current is None or item.version > current[1]:
            result[item.artifact_type] = (item.id, item.version)
    return result


def _succeeded_agents(session_id: str) -> Counter[str]:
    return Counter(
        item.agent_id
        for item in HarnessStateRepository().events(session_id)
        if item.event_type == "agent_invocation" and item.decision == "succeeded"
    )


def test_acceptance_strategy_failure_recovers_only_strategy_in_fresh_runtime(isolated_db) -> None:
    model = SemanticGoProgressionModel(fail_strategy=True)
    blocked = CognitiveOSRuntime(model_client=model).create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="harness-accept-strategy-recovery",
            userInput=_full_go_request(),
        )
    )
    stable = {
        kind: _heads(blocked)[kind]
        for kind in (
            "user_goal_model",
            "goal_completion",
            "reality_assessment",
            "evidence_pack",
        )
    }
    calls_before = Counter(model.attempts_by_task)
    persisted = HarnessStateRepository().recover(blocked.session_id)
    assert blocked.status == "MODEL_UNAVAILABLE"
    assert persisted.pending_agent == "strategy"
    assert persisted.waiting_state == "model_recovery"
    assert "strategy_portfolio" not in persisted.artifact_versions

    model.fail_strategy = False
    recovered = CognitiveOSRuntime(model_client=model).continue_current_stage(
        blocked.session_id
    )
    assert recovered.session_id == blocked.session_id
    assert recovered.status == "waiting_design_approval"
    assert _heads(recovered)["strategy_portfolio"][1] == 1
    for kind, ref in stable.items():
        assert _heads(recovered)[kind] == ref
    for task in ("planning_goal_model", "planning_reality", "planning_evidence"):
        assert model.attempts_by_task[task] == calls_before[task]
    assert model.attempts_by_task["planning_strategy"] == 2
    assert not any(
        item.event_type == "harness_decision" and item.decision == "wait_user"
        for item in HarnessStateRepository().events(blocked.session_id)
    )


def test_acceptance_critic_rejects_and_repairs_execution_before_approval(isolated_db) -> None:
    model = StubCognitiveModel(first_critique_needs_repair=True)
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="harness-accept-critic-repair",
            userInput="30天准备 Python AI 应用实习，每天3小时，要有项目产出",
        )
    )
    upstream = {
        kind: _heads(waiting_strategy)[kind]
        for kind in ("user_goal_model", "goal_completion", "reality_assessment", "evidence_pack", "strategy_portfolio")
    }
    repaired = runtime.approve_design(waiting_strategy.session_id)
    heads = _heads(repaired)
    assert repaired.status == "waiting_execution_approval"
    assert heads["execution_blueprint"][1] == 2
    assert heads["critique_report"][1] == 2
    for kind, ref in upstream.items():
        assert heads[kind] == ref
    succeeded = _succeeded_agents(repaired.session_id)
    assert succeeded["execution"] == 2
    assert succeeded["critic"] == 2
    state = HarnessStateRepository().recover(repaired.session_id)
    assert not any(
        item.gate == "execution" and item.status == "approved"
        for item in state.approvals
    )
    assert runtime.harness.critic_policy(
        repaired.session_id,
        critique_report=repaired.critique_report,
    ).allowed


def test_acceptance_reopen_restores_wait_checkpoint_without_model_calls(isolated_db) -> None:
    model = StubCognitiveModel()
    created = CognitiveOSRuntime(model_client=model).create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="harness-accept-reopen",
            userInput="30天准备 Python AI 应用实习，每天3小时，要有项目产出",
        )
    )
    before_calls = list(model.calls)
    before_state = HarnessStateRepository().recover(created.session_id)
    assert before_state.waiting_state == "strategy_approval"

    reopened_runtime = CognitiveOSRuntime(model_client=model)
    reopened = reopened_runtime.continue_current_stage(created.session_id)
    after_state = HarnessStateRepository().recover(created.session_id)
    assert reopened.session_id == created.session_id
    assert reopened.status == "waiting_design_approval"
    assert model.calls == before_calls
    assert after_state.checkpoint.artifact_refs == before_state.checkpoint.artifact_refs
    assert after_state.pending_agent is None
    assert after_state.waiting_state == "strategy_approval"


def test_acceptance_unavailable_model_persists_error_without_fake_plan(isolated_db) -> None:
    runtime = CognitiveOSRuntime(model_client=UnavailableCognitiveModel())
    blocked = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="harness-accept-unavailable",
            userInput="我要学游泳",
        )
    )
    state = HarnessStateRepository().recover(blocked.session_id)
    assert state.lifecycle == "blocked"
    assert state.pending_agent == "goal_intelligence"
    assert state.errors and state.recovery_actions
    assert state.recovery_actions[-1].action == "model_switch"
    assert blocked.strategy_portfolio is None
    assert blocked.execution_blueprint is None
    assert blocked.critique_report is None
    assert not {
        "strategy_portfolio",
        "execution_blueprint",
        "critique_report",
    } & set(state.artifact_versions)
    with pytest.raises(HTTPException):
        runtime.prepare_calendar_write(blocked.session_id)


def test_acceptance_task_too_hard_repairs_only_execution_layer(isolated_db) -> None:
    model = StubCognitiveModel()
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="harness-accept-task-too-hard",
            userInput="30天准备 Python AI 应用实习，每天3小时，要有代码和项目产出",
        )
    )
    waiting_execution = runtime.approve_design(waiting_strategy.session_id)
    ready = runtime.approve_execution(waiting_execution.session_id)
    before = _heads(ready)
    stable_kinds = (
        "user_goal_model",
        "goal_completion",
        "reality_assessment",
        "evidence_pack",
        "strategy_portfolio",
    )

    revised = runtime.revise_execution(
        ready.session_id,
        PlanningSessionTextRequest(text="任务太难"),
    )
    after = _heads(revised)
    assert revised.status == "waiting_execution_approval"
    for kind in stable_kinds:
        assert after[kind] == before[kind]
    assert after["execution_blueprint"][1] == before["execution_blueprint"][1] + 1
    assert after["critique_report"][1] == before["critique_report"][1] + 1
    assert after["planning_learning_update"][1] == 1
    assert after["memory_evaluation"][1] == 1

    succeeded = _succeeded_agents(revised.session_id)
    assert succeeded["goal_intelligence"] == 1
    assert succeeded["evidence"] == 1
    assert succeeded["strategy"] == 1
    assert succeeded["execution"] == 2
    assert succeeded["critic"] == 2
    assert succeeded["feedback_learning"] == 1
    assert succeeded["memory_evaluator"] == 1
    state = HarnessStateRepository().recover(revised.session_id)
    assert any(item.gate == "strategy" and item.status == "approved" for item in state.approvals)
    assert any(item.gate == "execution" and item.status == "invalidated" for item in state.approvals)
    assert not any(
        item.gate == "execution"
        and item.status == "approved"
        and item.artifact.version == after["execution_blueprint"][1]
        for item in state.approvals
    )
    memories = UserModelMemoryRepository().relevant("python_career")
    assert memories  # Saved only because independent Memory Evaluation allowed it.
