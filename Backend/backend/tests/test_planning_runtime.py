from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.cognitive_planning.runtime import CognitiveOSRuntime
from app.schemas import CreatePlanningSessionRequest, PlanningExecutionFeedbackRequest
from app.services.cognitive_planning.agents import AgentResult
from app.services.cognitive_planning.orchestration.runtime import PlanningRuntimeFoundation
from app.services.cognitive_planning.contracts import (
    CritiqueDimensions,
    EvidencePack,
    ExecutionBlueprint,
    ExecutionNarrative,
    GoalSuccessModel,
    PlanCritiqueReport,
    RealityAssessment,
    StrategyPortfolio,
    StrategyUserDecision,
    UserGoalModel,
)
from backend.tests.test_execution_single_pass import (
    _blueprint,
    _evidence,
    _goal,
    _reality,
    _strategy,
)


def _usage(task_type: str) -> dict:
    return {
        "provider": "stub",
        "model": "formal-planning-test",
        "mode": "llm",
        "taskType": task_type,
        "fallbackUsed": False,
        "localFallbackAllowed": False,
        "attempts": [{"provider": "stub", "model": "formal-planning-test", "status": "success"}],
    }


class StubCognitiveModel:
    def __init__(self):
        self.calls: list[str] = []

    def complete_contract(self, *, task_type: str, contract_type, **_kwargs):
        self.calls.append(task_type)
        if contract_type is UserGoalModel:
            value = _goal().model_copy(
                update={
                    "success_model": GoalSuccessModel(
                        definition="The result passes its acceptance checks.",
                        measurableSignals=["A reviewable deliverable passes its acceptance check."],
                    )
                }
            )
        elif contract_type is RealityAssessment:
            value = _reality()
        elif contract_type is EvidencePack:
            value = _evidence()
        elif contract_type is StrategyPortfolio:
            option = _strategy()
            value = StrategyPortfolio(
                recommendedStrategyId=option.id,
                strategies=[option],
                recommendationReason="The route satisfies the stated constraints.",
                userDecision=StrategyUserDecision(
                    question="Use this route?",
                    options=[option.name],
                    defaultRecommendation=option.name,
                ),
            )
        elif contract_type is ExecutionNarrative:
            value = _blueprint().narrative
        elif contract_type is ExecutionBlueprint:
            blueprint = _blueprint()
            value = blueprint.model_copy(
                update={
                    "tasks": [
                        *blueprint.tasks[:-1],
                        blueprint.tasks[-1].model_copy(update={"optionality": "optional"}),
                    ]
                }
            )
        elif contract_type is PlanCritiqueReport:
            value = PlanCritiqueReport(
                status="passed",
                score=95,
                dimensions=CritiqueDimensions(
                    userFit=95,
                    goalAlignment=95,
                    domainCorrectness=95,
                    feasibility=95,
                    safety=95,
                    taskSpecificity=95,
                    resourceActionability=95,
                    scheduleFit=95,
                    adaptability=95,
                ),
                strengths=["The plan is concrete and reviewable."],
                issues=[],
                repairRequests=[],
                simulationSummary="Dependencies and failure paths were checked.",
                remainingRisks=[],
                calendarWritable=True,
                confidence=0.95,
            )
        else:
            raise AssertionError(f"Unexpected planning contract: {contract_type}")
        return AgentResult(value, _usage(task_type))


@pytest.fixture()
def planning_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'planning-runtime.db'}")
    return CognitiveOSRuntime(model_client=StubCognitiveModel())


def test_formal_flow_has_only_understanding_and_final_review_user_gates(planning_runtime):
    created = planning_runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="v2-happy-path",
            userInput="Learn Python for data analysis with nine hours each week",
            context={"calendarSnapshotRef": "calendar:test:1", "calendarSnapshotVersion": 1},
        )
    )

    assert created.status == "waiting_understanding_confirmation"
    assert created.planning_phase == "UNDERSTANDING"
    assert created.understanding_snapshot["readiness"]["readyForConfirmation"] is True

    final_review = planning_runtime.confirm_understanding(created.session_id)

    assert final_review.status == "waiting_final_review"
    assert final_review.planning_phase == "FINAL_REVIEW"
    assert final_review.plan_blueprint
    assert final_review.plan_quality_report["hardRulesPassed"] is True
    verified_refs = {
        claim["sourceRef"]
        for claim in final_review.context_pack["claims"]
        if claim["verificationStatus"] == "verified"
    }
    assert all(
        set(task["resourceRefs"]) <= verified_refs
        for task in final_review.plan_blueprint["tasks"]
    )
    assert final_review.plan_blueprint["tasks"][-1]["optionality"] == "optional"
    assert final_review.schedule_blueprint
    assert final_review.schedule_quality_report["hardRulesPassed"] is True
    assert final_review.calendar_proposal
    assert final_review.approved_strategy_id is not None

    prepared = planning_runtime.approve_final(final_review.session_id)
    assert prepared.status == "waiting_calendar_write_approval"
    assert prepared.final_approval_bundle


@pytest.mark.parametrize(
    ("repair_target", "graph_node"),
    (
        ("goal_modeling", "understanding"),
        ("context_evidence", "synthesize_context"),
        ("strategy_architect", "design_approach"),
        ("execution_designer", "generate_plan"),
        ("independent_critic", "semantic_review"),
    ),
)
def test_repair_targets_map_to_current_graph_nodes(
    planning_runtime,
    monkeypatch,
    repair_target,
    graph_node,
):
    monkeypatch.setattr(
        PlanningRuntimeFoundation,
        "repair_plan_node",
        lambda _self, state: {**state, "next_node": repair_target},
    )

    updated = planning_runtime.repair_plan_node({"session_id": "repair-routing-test"})

    assert updated["next_node"] == graph_node


def test_learning_patch_is_consumed_before_critic_repair(planning_runtime, monkeypatch):
    monkeypatch.setattr(planning_runtime.persistence, "update", lambda *_args, **_kwargs: None)
    learning_update = SimpleNamespace(
        current_plan_patch=SimpleNamespace(
            target_artifact="execution_blueprint",
            instruction="Apply the user's final-review feedback.",
        )
    )
    critique = SimpleNamespace(
        repair_requests=[
            SimpleNamespace(
                target_agent="execution_designer",
                model_dump=lambda **_kwargs: {"instruction": "Apply the Critic repair."},
            )
        ]
    )

    first = planning_runtime.repair_plan_node(
        {
            "session_id": "repair-learning-test",
            "learning_update": learning_update,
            "critique_report": critique,
            "repair_count": 0,
        }
    )
    second = planning_runtime.repair_plan_node(first)

    assert first["learning_update"] is None
    assert second["repair_count"] == 1
    assert second["next_node"] == "generate_plan"


def test_formal_flow_rejects_a_stale_final_approval(planning_runtime):
    created = planning_runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="v2-stale-final-approval",
            userInput="Learn Python for data analysis with nine hours each week",
            context={"calendarSnapshotRef": "calendar:test:1", "calendarSnapshotVersion": 1},
        )
    )
    final_review = planning_runtime.confirm_understanding(created.session_id)
    prepared = planning_runtime.approve_final(final_review.session_id)
    execution = max(
        (item for item in prepared.artifacts if item.artifact_type == "execution_blueprint"),
        key=lambda item: item.version,
    )
    execution_ref = {
        "id": execution.id,
        "sessionId": execution.session_id,
        "kind": execution.artifact_type,
        "version": execution.version,
        "owner": execution.owner_agent,
        "status": execution.status,
    }
    planning_runtime.agent_runtime.record_artifact(
        prepared.session_id,
        owner_agent="Plan Generator",
        artifact_type="plan_blueprint",
        content=prepared.plan_blueprint,
    )

    with pytest.raises(HTTPException) as exc_info:
        planning_runtime.approve_calendar_write(
            prepared.session_id,
            execution_artifact_ref=execution_ref,
        )

    assert exc_info.value.status_code == 409
    assert "stale" in str(exc_info.value.detail)


def test_execution_feedback_records_outcome_and_requires_review_for_deviation(planning_runtime):
    created = planning_runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="v2-execution-feedback",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    final_review = planning_runtime.confirm_understanding(created.session_id)
    task_id = final_review.plan_blueprint["tasks"][0]["id"]

    result = planning_runtime.record_execution_feedback(
        final_review.session_id,
        PlanningExecutionFeedbackRequest(
            taskId=task_id,
            status="failed",
            actualMinutes=90,
            failureReason="The required environment was unavailable.",
        ),
    )

    assert result.outcome["taskId"] == task_id
    assert result.outcome["status"] == "failed"
    assert result.replan_proposal["requiresFinalReview"] is True
    assert result.learning_observation["sourceRefs"]
    artifact_types = {
        item.artifact_type
        for item in planning_runtime.agent_runtime.list_artifacts(final_review.session_id)
    }
    assert {"execution_outcome", "replan_proposal", "learning_observation"} <= artifact_types


def test_formal_flow_restores_feedback_contract_inputs_from_artifact_heads(planning_runtime):
    created = planning_runtime.create_session(
        CreatePlanningSessionRequest(
            entryPoint="p_mode",
            threadId="v2-feedback-state",
            userInput="Learn Python for data analysis with nine hours each week",
        )
    )
    final_review = planning_runtime.confirm_understanding(created.session_id)
    row = planning_runtime.persistence.get_row(final_review.session_id)

    restored = planning_runtime._state_from_row(row, action="give_feedback")

    assert restored["evidence_pack"]
    assert restored["strategy_portfolio"]
    assert restored["execution_blueprint"]
    assert restored["critique_report"]
    assert restored["approved_strategy_id"] == final_review.approved_strategy_id
    assert restored["strategy_portfolio"].status == "approved"
