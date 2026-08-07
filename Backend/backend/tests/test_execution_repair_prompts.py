from __future__ import annotations

from typing import Any

import pytest

from app.cognitive_planning.agents.critic_agent import (
    CRITIC_SYSTEM as OS_CRITIC_SYSTEM,
    CriticAgent,
)
from app.cognitive_planning.agents.execution_agent import (
    BLUEPRINT_SYSTEM as OS_BLUEPRINT_SYSTEM,
    NARRATIVE_SYSTEM as OS_NARRATIVE_SYSTEM,
    ExecutionAgent,
)
from app.services.cognitive_planning.agents import AgentResult
from app.services.cognitive_planning.agents.critic_learning_agent import (
    CRITIC_SYSTEM as LEGACY_CRITIC_SYSTEM,
)
from app.services.cognitive_planning.agents.execution_designer_agent import (
    BLUEPRINT_SYSTEM as LEGACY_BLUEPRINT_SYSTEM,
    NARRATIVE_SYSTEM as LEGACY_NARRATIVE_SYSTEM,
    ExecutionDesignerAgent,
)
from app.services.cognitive_planning.contracts import (
    EVIDENCE_AUTHORITY_POLICY_VERSION,
    CritiqueDimensions,
    EvidencePack,
    ExecutionBlueprint,
    ExecutionBlueprintTask,
    ExecutionNarrative,
    ExecutionResource,
    FeasibilityJudgment,
    GoalSuccessModel,
    PlanCritiqueReport,
    StrategyOption,
    StrategyPhase,
    StrategyRationale,
    UserGoalModel,
)


def _goal() -> UserGoalModel:
    return UserGoalModel(
        goalStatement="Complete a reviewable project",
        desiredChange="Deliver a working project safely",
        domain="software_project",
        successModel=GoalSuccessModel(definition="The project passes its acceptance checks."),
        feasibilityJudgment=FeasibilityJudgment(summary="Feasible."),
        confidence=0.9,
        canProceedToEvidence=True,
    )


def _evidence() -> EvidencePack:
    return EvidencePack(
        synthesis="The project can proceed within the stated constraints.",
        confidence=0.8,
        canProceedToStrategy=True,
        authorityPolicyVersion=EVIDENCE_AUTHORITY_POLICY_VERSION,
    )


def _strategy() -> StrategyOption:
    return StrategyOption(
        id="approved",
        name="Approved route",
        coreIdea="Build and validate one end-to-end slice.",
        rationale=StrategyRationale(whyItFitsUser="It produces reviewable evidence early."),
        phases=[
            StrategyPhase(
                title="End-to-end slice",
                purpose="Test the riskiest path first.",
                outcome="A reviewable slice exists.",
                whyThisPhaseExists="It reduces delivery risk.",
            )
        ],
        estimatedEffort="Moderate",
    )


def _blueprint() -> ExecutionBlueprint:
    narrative = ExecutionNarrative(
        executionLogic="Validate the riskiest path first.",
        dependencyExplanation="The first task has no dependency.",
        weeklyOrStageRhythm="One focused task in week 1.",
        workloadReasoning="Week 1 uses 60 minutes.",
        riskHandling="Use the documented fallback if the primary tool fails.",
    )
    return ExecutionBlueprint(
        narrative=narrative,
        tasks=[
            ExecutionBlueprintTask(
                id="task-1",
                title="Validate the end-to-end slice",
                purpose="Prove the approved route works.",
                whyNow="It tests the highest-risk dependency first.",
                actionSteps=["Run the acceptance check and record the result."],
                estimatedMinutes=60,
                difficulty="low",
                scheduleWindow="Week 1",
                completionEvidence=["Saved acceptance-check output"],
                deliverable="A reviewable validation record",
                resources=[
                    ExecutionResource(
                        title="Project test command",
                        type="local_tool",
                        exactUsage="Run it against the end-to-end slice.",
                        expectedContribution="Produces the acceptance evidence.",
                    )
                ],
                risks=["The primary tool may be temporarily unavailable."],
                fallbackAction="Use the documented equivalent local check.",
            )
        ],
        resourceCoverage="strong",
    )


class _CaptureModel:
    def __init__(self, blueprint: ExecutionBlueprint):
        self.blueprint = blueprint
        self.calls: list[dict[str, Any]] = []

    def complete_contract(
        self,
        *,
        stage: str,
        system: str,
        payload: dict[str, Any],
        contract_type,
        **_: Any,
    ):
        self.calls.append({"stage": stage, "system": system, "payload": payload})
        usage = {
            "attempts": [],
            "fallbackUsed": False,
            "localFallbackAllowed": False,
        }
        if contract_type is ExecutionNarrative:
            return AgentResult(self.blueprint.narrative, usage)
        if contract_type is ExecutionBlueprint:
            return AgentResult(self.blueprint, usage)
        if contract_type is PlanCritiqueReport:
            return AgentResult(
                PlanCritiqueReport(
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
                    calendarWritable=True,
                ),
                usage,
            )
        raise AssertionError(f"Unexpected contract: {contract_type}")


@pytest.mark.parametrize(
    ("agent_type", "narrative_prompt", "blueprint_prompt"),
    [
        (ExecutionAgent, OS_NARRATIVE_SYSTEM, OS_BLUEPRINT_SYSTEM),
        (ExecutionDesignerAgent, LEGACY_NARRATIVE_SYSTEM, LEGACY_BLUEPRINT_SYSTEM),
    ],
)
def test_execution_repair_uses_two_pass_generation_with_cumulative_context(
    agent_type,
    narrative_prompt: str,
    blueprint_prompt: str,
) -> None:
    previous = _blueprint()
    repair_instructions = [
        {
            "instruction": "Repair only the named fallback risk.",
            "expectedChange": "The fallback preserves the required deliverable.",
        }
    ]
    previous_critique = PlanCritiqueReport.model_validate(
        {
            "status": "needs_repair",
            "score": 78,
            "dimensions": {
                "userFit": 90,
                "goalAlignment": 90,
                "domainCorrectness": 80,
                "feasibility": 75,
                "safety": 90,
                "taskSpecificity": 80,
                "resourceActionability": 80,
                "scheduleFit": 70,
                "adaptability": 80,
            },
            "issues": [
                {
                    "severity": "major",
                    "description": "The fallback can drop the required deliverable.",
                    "evidence": "task-1 fallbackAction",
                    "responsibleAgent": "execution",
                }
            ],
            "repairRequests": [
                {
                    "targetAgent": "execution",
                    "instruction": "Preserve the required deliverable in the fallback.",
                    "expectedChange": "The fallback produces equivalent evidence.",
                }
            ],
            "simulationSummary": "The primary route works but the fallback regresses scope.",
            "calendarWritable": False,
            "confidence": 0.9,
        }
    )
    repair_history = [
        {
            "executionVersion": 1,
            "critiqueScore": 72,
            "repairInstructions": ["Keep the acceptance evidence."],
        }
    ]
    model = _CaptureModel(previous)
    agent = agent_type(model=model)

    agent.run(
        _goal(),
        _evidence(),
        _strategy(),
        repair_instructions=repair_instructions,
        previous_execution=previous,
        previous_critique=previous_critique,
        repair_history=repair_history,
    )

    assert len(model.calls) == 2
    expected_previous = previous.model_dump(by_alias=True)
    for call in model.calls:
        assert call["payload"]["previousExecutionBlueprint"] == expected_previous
        assert call["payload"]["repairInstructions"] == repair_instructions
        assert call["payload"]["previousCritiqueReport"] == previous_critique.model_dump(
            by_alias=True
        )
        assert call["payload"]["repairHistory"] == repair_history
    assert model.calls[0]["stage"] == "execution_narrative"
    assert model.calls[1]["stage"] == "execution_design"
    assert model.calls[0]["system"] == narrative_prompt
    assert model.calls[1]["system"] == blueprint_prompt
    assert model.calls[1]["payload"]["executionNarrative"] == previous.narrative.model_dump(
        by_alias=True
    )
    normalized_narrative = " ".join(narrative_prompt.split())
    normalized_blueprint = " ".join(blueprint_prompt.split())
    assert "cumulative checklist" in normalized_narrative
    assert "revise that exact blueprint" in normalized_blueprint


@pytest.mark.parametrize("critic_prompt", [OS_CRITIC_SYSTEM, LEGACY_CRITIC_SYSTEM])
def test_critic_prompt_uses_evidence_authority_and_forbids_numeric_oscillation(
    critic_prompt: str,
) -> None:
    normalized_prompt = " ".join(critic_prompt.split())
    assert "explicit Goal facts and hard constraints first" in normalized_prompt
    assert "current verifiable Evidence with source references" in normalized_prompt
    assert "own uncited memory" in normalized_prompt
    assert "official current source" in normalized_prompt
    assert "reverse a repaired Execution" in normalized_prompt
    assert "lower-authority Strategy estimate" in normalized_prompt
    assert "quote the exact current field" in normalized_prompt
    assert "treat that finding as resolved" in normalized_prompt


def test_critic_separates_current_context_from_superseded_history() -> None:
    execution = _blueprint()
    model = _CaptureModel(execution)
    prior = PlanCritiqueReport.model_validate(
        {
            "status": "needs_repair",
            "score": 75,
            "dimensions": {key: 75 for key in (
                "userFit",
                "goalAlignment",
                "domainCorrectness",
                "feasibility",
                "safety",
                "taskSpecificity",
                "resourceActionability",
                "scheduleFit",
                "adaptability",
            )},
            "issues": [{
                "severity": "major",
                "description": "The previous version lacked evidence.",
                "evidence": "superseded task-1",
                "responsibleAgent": "execution_designer",
            }],
            "repairRequests": [{
                "targetAgent": "execution_designer",
                "instruction": "Add evidence.",
                "expectedChange": "Evidence exists.",
            }],
            "calendarWritable": False,
        }
    )

    class StrategyStub:
        status = "approved"
        recommended_strategy_id = "approved"
        approved_strategy_id = "approved"

        @staticmethod
        def model_dump(**_kwargs):
            return {"status": "approved", "approvedStrategyId": "approved"}

    CriticAgent(model=model).critique(
        _goal(),
        _evidence(),
        StrategyStub(),
        execution,
        previous_critique=prior,
        repair_history=[{"critiqueArtifactVersion": 1}],
        critic_policy={
            "currentReviewContext": {
                "currentExecutionArtifact": {"id": "execution-current", "version": 2},
                "supersededArtifactsIncluded": False,
            }
        },
    )

    payload = model.calls[-1]["payload"]
    assert payload["currentReviewContext"]["currentExecutionArtifact"]["version"] == 2
    assert "previousCritiqueReport" not in payload
    assert "repairHistory" not in payload
    assert payload["supersededReviewHistory"]["authority"] == "audit_only"
