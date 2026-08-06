from __future__ import annotations

import json
from typing import Any

import pytest

from app.cognitive_planning import CognitiveOSRuntime
from app.db import get_conn
from app.schemas import CreatePlanningSessionRequest
from app.services.cognitive_planning.agents import PlanningModelUnavailable
from app.services.cognitive_planning.contracts import (
    ExecutionBlueprint,
    PlanCritiqueReport,
    SafePlanningError,
)

from planning_evals.test_cognitive_kernel import StubCognitiveModel


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'execution-review.db'}")
    return tmp_path


class _RecoverableCriticModel(StubCognitiveModel):
    def __init__(self) -> None:
        super().__init__()
        self.fail_critic = True

    def complete_contract(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        contract_type,
        stage: str = "",
        **kwargs: Any,
    ):
        if contract_type is PlanCritiqueReport and self.fail_critic:
            self.calls.append((task_type, payload))
            raise PlanningModelUnavailable(
                stage or "independent_critique",
                SafePlanningError(
                    stage=stage or "independent_critique",
                    errorType="timeout",
                    message="Independent Critic timed out.",
                    retryable=True,
                    attempts=[
                        {
                            "provider": "deepseek",
                            "model": "stub-cognitive",
                            "status": "error",
                            "errorType": "timeout",
                            "latencyMs": 10,
                        }
                    ],
                ),
            )
        return super().complete_contract(
            task_type=task_type,
            payload=payload,
            contract_type=contract_type,
            stage=stage,
            **kwargs,
        )


class _RecoverableExecutionRepairModel(StubCognitiveModel):
    def __init__(self) -> None:
        super().__init__(first_critique_needs_repair=True)
        self.execution_calls = 0
        self.fail_repair_execution = True

    def complete_contract(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        contract_type,
        stage: str = "",
        **kwargs: Any,
    ):
        if contract_type is ExecutionBlueprint:
            self.execution_calls += 1
            if self.execution_calls == 2 and self.fail_repair_execution:
                self.calls.append((task_type, payload))
                raise PlanningModelUnavailable(
                    stage or "execution_design",
                    SafePlanningError(
                        stage=stage or "execution_design",
                        errorType="timeout",
                        message="Execution repair timed out.",
                        retryable=True,
                        attempts=[],
                    ),
                )
        return super().complete_contract(
            task_type=task_type,
            payload=payload,
            contract_type=contract_type,
            stage=stage,
            **kwargs,
        )


def _start(runtime: CognitiveOSRuntime, thread_id: str):
    return runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId=thread_id,
            userInput="Build a reviewable Python project in 30 days with three hours available daily",
        )
    )


def _review_artifacts(runtime: CognitiveOSRuntime, session_id: str):
    artifacts = runtime.agent_runtime.list_artifacts(session_id)
    executions = [item for item in artifacts if item.artifact_type == "execution_blueprint"]
    critiques = [item for item in artifacts if item.artifact_type == "critique_report"]
    return executions, critiques


def test_critic_failure_keeps_one_bound_critique_and_recovery_finalizes_same_slot(
    isolated_db,
) -> None:
    model = _RecoverableCriticModel()
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "critic-failure-pair")

    blocked = runtime.approve_design(waiting_strategy.session_id)

    executions, critiques = _review_artifacts(runtime, blocked.session_id)
    assert blocked.status == "MODEL_UNAVAILABLE"
    assert len(executions) == len(critiques) == 1
    assert critiques[0].content_json["evaluatedExecutionArtifactId"] == executions[0].id
    assert critiques[0].content_json["evaluatedExecutionArtifactVersion"] == executions[0].version
    assert critiques[0].content_json["status"] == "blocked"
    assert critiques[0].content_json["issues"][0]["evidence"] == "independent_critic_model_unavailable"
    assert not [
        item
        for item in runtime.agent_runtime.list_decisions(blocked.session_id)
        if item.agent == "Critic Agent"
    ]

    model.fail_critic = False
    recovered = CognitiveOSRuntime(model_client=model).continue_current_stage(
        blocked.session_id
    )

    executions_after, critiques_after = _review_artifacts(runtime, blocked.session_id)
    assert recovered.status == "waiting_execution_approval"
    assert recovered.critique_report["status"] == "passed"
    assert [item.id for item in executions_after] == [item.id for item in executions]
    assert [item.id for item in critiques_after] == [item.id for item in critiques]
    assert critiques_after[0].content_json["evaluatedExecutionArtifactId"] == executions[0].id
    critic_decisions = [
        item
        for item in runtime.agent_runtime.list_decisions(blocked.session_id)
        if item.agent == "Critic Agent"
    ]
    assert len(critic_decisions) == 1
    assert critic_decisions[0].decision == "approve"
    assert critic_decisions[0].input_artifact_ids.count(executions[0].id) == 1
    assert critic_decisions[0].output_artifact_ids == [critiques[0].id]


def test_failed_critic_repair_generation_persists_no_candidate_and_rehydrates_instructions(
    isolated_db,
) -> None:
    model = _RecoverableExecutionRepairModel()
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "execution-repair-resume")

    blocked = runtime.approve_design(waiting_strategy.session_id)

    executions, critiques = _review_artifacts(runtime, blocked.session_id)
    assert blocked.status == "MODEL_UNAVAILABLE"
    assert blocked.model_failure is not None
    assert blocked.model_failure.resume_node == "execution"
    # The failed preflight/generation candidate is never formalized.
    assert len(executions) == len(critiques) == 1
    first_execution = executions[0].content_json
    repair_call = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_execution"
    ][-1]
    assert repair_call["previousExecutionBlueprint"] == first_execution
    assert repair_call["repairInstructions"]
    assert repair_call["previousCritiqueReport"]["status"] == "needs_repair"
    assert repair_call["repairHistory"][-1]["repairRequests"]

    model.fail_repair_execution = False
    recovered = CognitiveOSRuntime(model_client=model).continue_current_stage(
        blocked.session_id
    )

    executions_after, critiques_after = _review_artifacts(runtime, blocked.session_id)
    assert recovered.status == "waiting_execution_approval"
    assert len(executions_after) == len(critiques_after) == 2
    resumed_call = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_execution"
    ][-1]
    assert resumed_call["previousExecutionBlueprint"] == first_execution
    assert resumed_call["repairInstructions"] == repair_call["repairInstructions"]
    assert resumed_call["previousCritiqueReport"] == repair_call["previousCritiqueReport"]
    assert resumed_call["repairHistory"] == repair_call["repairHistory"]
    critique_calls = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_critique"
    ]
    assert len(critique_calls) == 2
    assert critique_calls[-1]["previousCritiqueReport"]["status"] == "needs_repair"
    assert critique_calls[-1]["repairHistory"][-1]["repairRequests"]
    for execution, critique in zip(executions_after, critiques_after, strict=True):
        assert critique.content_json["evaluatedExecutionArtifactId"] == execution.id
        assert critique.content_json["evaluatedExecutionArtifactVersion"] == execution.version


def test_legacy_decision_lineage_upgrades_existing_critique_without_duplicate(
    isolated_db,
) -> None:
    runtime = CognitiveOSRuntime(model_client=StubCognitiveModel())
    waiting_strategy = _start(runtime, "legacy-review-lineage")
    reviewed = runtime.approve_design(waiting_strategy.session_id)
    executions, critiques = _review_artifacts(runtime, reviewed.session_id)
    assert len(executions) == len(critiques) == 1

    legacy_content = dict(critiques[0].content_json)
    legacy_content.pop("evaluatedExecutionArtifactId")
    legacy_content.pop("evaluatedExecutionArtifactVersion")
    with get_conn() as conn:
        conn.execute(
            "UPDATE planning_artifacts SET content_json = ? WHERE id = ?",
            (json.dumps(legacy_content), critiques[0].id),
        )

    upgraded = runtime.agent_runtime.ensure_execution_review_slot(
        reviewed.session_id,
        execution_artifact_id=executions[0].id,
        critique_owner=runtime.critic_agent.name,
        pending_critique=runtime._unreviewed_critique(),
    )

    executions_after, critiques_after = _review_artifacts(runtime, reviewed.session_id)
    assert upgraded.id == critiques[0].id
    assert len(executions_after) == len(critiques_after) == 1
    assert upgraded.content_json["evaluatedExecutionArtifactId"] == executions[0].id
