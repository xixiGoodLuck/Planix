from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.cognitive_planning import CognitiveOSRuntime
from app.cognitive_planning.agents.critic_agent import CRITIC_SYSTEM
from app.harness.contracts import ArtifactRef
from app.harness.controllers import CriticController
from app.harness.quality import MIN_CRITIC_PASS_SCORE
from app.harness.scheduler import AgentScheduler
from app.schemas import CreatePlanningSessionRequest
from app.services.cognitive_planning.contracts import PlanCritiqueReport
from app.services.cognitive_planning.evaluation import calendar_write_allowed
from app.services.cognitive_planning.agents.critic_learning_agent import (
    CRITIC_SYSTEM as SERVICE_CRITIC_SYSTEM,
)
from app.services.cognitive_planning.orchestration.edges import route_after_critic

from planning_evals.test_cognitive_kernel import StubCognitiveModel


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'critic-quality.db'}")
    return tmp_path


class _CriticScoreModel(StubCognitiveModel):
    def __init__(self, scores: list[int]) -> None:
        super().__init__()
        self.scores = scores

    def _critique(self) -> PlanCritiqueReport:
        report = super()._critique()
        index = min(self.critique_calls - 1, len(self.scores) - 1)
        return report.model_copy(update={"score": self.scores[index]})


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


def test_low_score_passed_critique_generates_repair_then_high_score_passes(
    isolated_db,
) -> None:
    model = _CriticScoreModel([89, 94])
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "critic-quality-repair")

    reviewed = runtime.approve_design(waiting_strategy.session_id)

    assert reviewed.status == "waiting_execution_approval"
    assert reviewed.business_status == "execution_pending"
    assert reviewed.runtime_status == "idle"
    assert reviewed.cognitive_metadata.repair_count == 1
    assert reviewed.critique_report["status"] == "passed"
    assert reviewed.critique_report["score"] == 94
    executions, critiques = _review_artifacts(runtime, reviewed.session_id)
    assert len(executions) == len(critiques) == 2
    first_review = critiques[0].content_json
    assert first_review["status"] == "needs_repair"
    assert first_review["score"] == 89
    assert first_review["calendarWritable"] is False
    assert first_review["repairRequests"]
    assert "at least 90" in first_review["repairRequests"][0]["instruction"]
    execution_calls = [
        payload
        for task_type, payload in model.calls
        if task_type == "planning_execution"
    ]
    assert execution_calls[1]["previousExecutionBlueprint"] == executions[0].content_json
    assert execution_calls[1]["repairInstructions"] == first_review["repairRequests"]
    for execution, critique in zip(executions, critiques, strict=True):
        assert critique.content_json["evaluatedExecutionArtifactId"] == execution.id


def test_low_score_passed_critique_exhausts_two_repairs_and_fails_closed(
    isolated_db,
) -> None:
    model = _CriticScoreModel([89])
    runtime = CognitiveOSRuntime(model_client=model)
    waiting_strategy = _start(runtime, "critic-quality-exhausted")

    blocked = runtime.approve_design(waiting_strategy.session_id)

    assert blocked.status == "execution_revision"
    assert blocked.business_status == "execution_pending"
    assert blocked.runtime_status == "idle"
    assert blocked.cognitive_metadata.repair_count == 2
    assert blocked.critique_report["status"] == "blocked"
    assert blocked.critique_report["score"] == 89
    assert blocked.critique_report["calendarWritable"] is False
    assert blocked.critique_report["repairRequests"] == []
    executions, critiques = _review_artifacts(runtime, blocked.session_id)
    assert len(executions) == len(critiques) == 3
    assert model.critique_calls == 3
    assert runtime.harness.critic_policy(
        blocked.session_id,
        critique_report=blocked.critique_report,
    ).allowed is False
    with pytest.raises(HTTPException):
        runtime.prepare_calendar_write(blocked.session_id)


def test_scheduler_controller_and_calendar_guard_share_ninety_point_gate() -> None:
    low_report = {
        "status": "passed",
        "score": MIN_CRITIC_PASS_SCORE - 1,
        "calendarWritable": True,
        "issues": [],
        "repairRequests": [],
    }
    execution = ArtifactRef(
        id="execution-v1",
        sessionId="quality-session",
        kind="execution_blueprint",
        version=1,
        owner="Execution Agent",
    )
    critique = ArtifactRef(
        id="critique-v1",
        sessionId="quality-session",
        kind="critique_report",
        version=1,
        owner="Critic Agent",
    )
    gate = CriticController().assess(
        report=low_report,
        critique_artifact=critique,
        execution_artifact=execution,
        evaluated_execution_artifact=execution,
    )
    assert gate.passed is False
    assert "below the required 90" in gate.reason

    scheduled = AgentScheduler().after_critic(
        {
            "planning_mode": "model_backed",
            "critique_report": SimpleNamespace(
                status="passed",
                score=89,
                calendar_writable=True,
                issues=[],
                repair_requests=[],
            ),
            "repair_count": 0,
        }
    )
    assert scheduled.next_node == "repair"
    assert scheduled.reason_code == "critic_score_below_threshold"
    assert route_after_critic(
        {
            "critique_report": SimpleNamespace(
                status="passed",
                score=89,
                repair_requests=[],
            ),
            "repair_count": 0,
        }
    ) == "repair_router"

    model_report = StubCognitiveModel()._critique().model_copy(update={"score": 89})
    assert not calendar_write_allowed(
        planning_mode="model_backed",
        critique=model_report,
        strategy_approved=True,
        execution_approved=True,
    )


def test_both_critic_prompts_define_the_same_ninety_point_pass_rule() -> None:
    for prompt in (CRITIC_SYSTEM, SERVICE_CRITIC_SYSTEM):
        normalized = " ".join(prompt.split())
        assert "Never return status=passed unless the overall score is at least 90" in normalized
        assert "below 90, return needs_repair" in normalized
