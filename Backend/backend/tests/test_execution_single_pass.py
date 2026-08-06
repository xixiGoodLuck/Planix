from __future__ import annotations

from typing import Any

import pytest

from app.cognitive_planning.agents.execution_agent import ExecutionAgent
from app.schemas import ModelUsage
from app.services.cognitive_planning.agents import AgentResult
from app.services.cognitive_planning.agents.base import PlanningModelUnavailable
from app.services.cognitive_planning.agents.execution_designer_agent import ExecutionDesignerAgent
from app.services.cognitive_planning.contracts import (
    Constraint,
    EVIDENCE_AUTHORITY_POLICY_VERSION,
    EvidencePack,
    ExecutionBlueprint,
    ExecutionBlueprintTask,
    ExecutionBudgetAllocation,
    ExecutionBudgetSummary,
    ExecutionNarrative,
    ExecutionResource,
    FeasibilityJudgment,
    GoalSuccessModel,
    RealityAssessment,
    SafePlanningError,
    StrategyOption,
    StrategyPhase,
    StrategyRationale,
    UserGoalModel,
)
from app.services.cognitive_planning.evaluation import (
    DeterministicGuardError,
    validate_execution_preflight,
)
from app.services.cognitive_planning.evaluation.deterministic_guards import (
    execution_preflight_context,
)


def _usage(index: int) -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "promptTokens": 10,
        "completionTokens": 5,
        "totalTokens": 15,
        "latencyMs": 7,
        "mode": "llm",
        "taskType": "planning_execution",
        "fallbackUsed": False,
        "localFallbackAllowed": False,
        "attempts": [
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "status": "success",
                "latencyMs": 7,
                "unsafeDetail": f"must-not-survive-{index}",
            }
        ],
    }


def _goal(*, constraints: list[Constraint] | None = None) -> UserGoalModel:
    return UserGoalModel(
        goalStatement="Deliver a reviewable result",
        desiredChange="Complete the approved result within explicit constraints",
        domain="test",
        hardConstraints=constraints or [],
        successModel=GoalSuccessModel(definition="The result passes its acceptance checks."),
        feasibilityJudgment=FeasibilityJudgment(summary="Feasible."),
        confidence=0.9,
        canProceedToEvidence=True,
    )


def _reality(*, can_proceed: bool = True) -> RealityAssessment:
    return RealityAssessment(
        goalRestatement="Deliver the result.",
        feasibilitySummary="Feasible within the explicit limits.",
        timeAssessment="Use the stated capacity.",
        resourceAssessment="Required resources are available.",
        confidence=0.9,
        canProceedToEvidence=can_proceed,
        importantQuestions=[] if can_proceed else [
            {
                "question": "Resolve the feasibility blocker.",
                "whyThisQuestionMatters": "Execution cannot safely proceed.",
                "expectedDecisionImpact": "Changes feasibility.",
            }
        ],
    )


def _evidence() -> EvidencePack:
    return EvidencePack(
        synthesis="The approved route is supported.",
        confidence=0.9,
        canProceedToStrategy=True,
        authorityPolicyVersion=EVIDENCE_AUTHORITY_POLICY_VERSION,
    )


def _strategy() -> StrategyOption:
    return StrategyOption(
        id="approved",
        name="Approved strategy",
        coreIdea="Validate dependencies before delivery.",
        rationale=StrategyRationale(whyItFitsUser="It respects the explicit constraints."),
        phases=[
            StrategyPhase(
                title="Deliver",
                purpose="Produce the accepted result.",
                outcome="A reviewable result exists.",
                whyThisPhaseExists="It fulfills the approved goal.",
            )
        ],
        estimatedEffort="Within capacity",
    )


def _task(
    task_id: str,
    *,
    dependencies: list[str] | None = None,
    minutes: int = 30,
    window: str | None = "Week 1",
    scheduled_date: str | None = None,
    weekly_minutes: dict[str, int] | None = None,
) -> ExecutionBlueprintTask:
    return ExecutionBlueprintTask(
        id=task_id,
        title=f"Produce {task_id} evidence",
        purpose="Produce a concrete part of the approved result.",
        whyNow="Its dependencies are ready.",
        dependencies=dependencies or [],
        actionSteps=["Perform the concrete action and save its output."],
        estimatedMinutes=minutes,
        difficulty="low",
        scheduledDate=scheduled_date,
        scheduleWindow=window,
        completionEvidence=[f"Saved evidence for {task_id}"],
        deliverable=f"Reviewable {task_id} deliverable",
        resources=[
            ExecutionResource(
                title="Verified local tool",
                type="local_tool",
                exactUsage="Run the acceptance check for this task.",
                expectedContribution="Produces checkable evidence.",
            )
        ],
        risks=["The tool may be temporarily unavailable."],
        fallbackAction="Use the documented equivalent check without removing the deliverable.",
        domainExtensions={"weeklyMinutes": weekly_minutes} if weekly_minutes else {},
    )


def _blueprint(
    *,
    tasks: list[ExecutionBlueprintTask] | None = None,
    budget: ExecutionBudgetSummary | None = None,
) -> ExecutionBlueprint:
    return ExecutionBlueprint(
        narrative=ExecutionNarrative(
            executionLogic="Resolve dependencies, produce evidence, then review.",
            dependencyExplanation="Each dependency must finish first.",
            weeklyOrStageRhythm="Work and verify in each period.",
            workloadReasoning="Every explicit period allocation is within capacity.",
            riskHandling="Use the stated fallback without dropping a hard requirement.",
        ),
        tasks=tasks or [_task("task-1")],
        budgetSummary=budget,
        resourceCoverage="strong",
    )


class _SequencedModel:
    def __init__(self, outcomes: list[Any]):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def complete_contract(self, **kwargs: Any):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return AgentResult(outcome, _usage(len(self.calls)))


def _failure(error_type: str) -> PlanningModelUnavailable:
    return PlanningModelUnavailable(
        "execution_design",
        SafePlanningError(
            stage="execution_design",
            errorType=error_type,
            message="structured execution generation failed",
            retryable=True,
            attempts=[
                {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "status": "error",
                    "errorType": error_type,
                    "secret": "must-not-survive",
                }
            ],
        ),
    )


@pytest.mark.parametrize("agent_type", [ExecutionAgent, ExecutionDesignerAgent])
def test_execution_defaults_to_one_complete_blueprint_call(agent_type) -> None:
    model = _SequencedModel([_blueprint()])

    result = agent_type(model=model).run(_goal(), _evidence(), _strategy(), reality=_reality())

    assert len(model.calls) == 1
    assert model.calls[0]["contract_type"] is ExecutionBlueprint
    assert model.calls[0]["task_type"] == "planning_execution"
    assert result.model_usage["generationMode"] == "single_pass"
    assert result.model_usage["totalTokens"] == 15
    assert "unsafeDetail" not in result.model_usage["attempts"][0]


@pytest.mark.parametrize("agent_type", [ExecutionAgent, ExecutionDesignerAgent])
@pytest.mark.parametrize("error_type", ["model_output_truncated", "invalid_model_output"])
def test_execution_structured_failure_falls_back_once_to_narrative_then_blueprint(
    agent_type,
    error_type: str,
) -> None:
    blueprint = _blueprint()
    model = _SequencedModel([_failure(error_type), blueprint.narrative, blueprint])

    result = agent_type(model=model).run(_goal(), _evidence(), _strategy(), reality=_reality())

    assert [call["contract_type"] for call in model.calls] == [
        ExecutionBlueprint,
        ExecutionNarrative,
        ExecutionBlueprint,
    ]
    assert result.model_usage["generationMode"] == "two_pass_fallback"
    assert result.model_usage["totalTokens"] == 30
    assert len(result.model_usage["attempts"]) == 3
    assert result.model_usage["attempts"][1]["retryReason"] == "execution_two_pass_fallback"
    assert not any(attempt.get("automaticRetry") for attempt in result.model_usage["attempts"])
    assert all("secret" not in attempt for attempt in result.model_usage["attempts"])


def test_execution_timeout_does_not_enter_structured_output_fallback() -> None:
    model = _SequencedModel([_failure("timeout")])

    with pytest.raises(PlanningModelUnavailable) as exc_info:
        ExecutionAgent(model=model).run(_goal(), _evidence(), _strategy(), reality=_reality())

    assert exc_info.value.error.error_type == "timeout"
    assert len(model.calls) == 1


@pytest.mark.parametrize("agent_type", [ExecutionAgent, ExecutionDesignerAgent])
def test_execution_critic_repair_uses_exactly_one_narrative_then_blueprint_pass(
    agent_type,
) -> None:
    previous = _blueprint(tasks=[_task("baseline")])
    replacement = _blueprint(
        tasks=[
            _task("baseline"),
            _task("verified", dependencies=["baseline"]),
        ]
    )
    repair_instructions = [
        {
            "issue": "Add an explicit verification task without regressing the baseline.",
            "severity": "major",
        }
    ]
    model = _SequencedModel([replacement.narrative, replacement])

    result = agent_type(model=model).run(
        _goal(),
        _evidence(),
        _strategy(),
        reality=_reality(),
        previous_execution=previous,
        repair_instructions=repair_instructions,
    )

    assert [call["contract_type"] for call in model.calls] == [
        ExecutionNarrative,
        ExecutionBlueprint,
    ]
    assert model.calls[0]["feature"].endswith("_narrative_repair")
    assert model.calls[1]["feature"].endswith("_blueprint_repair")
    assert not any(call["feature"].endswith("_single_pass") for call in model.calls)
    assert model.calls[0]["payload"]["previousExecutionBlueprint"] == previous.model_dump(
        by_alias=True
    )
    assert model.calls[0]["payload"]["repairInstructions"] == repair_instructions
    assert model.calls[1]["payload"]["executionNarrative"] == replacement.narrative.model_dump(
        by_alias=True
    )
    assert result.artifact == replacement
    assert result.model_usage["generationMode"] == "two_pass_repair"
    assert result.model_usage["totalTokens"] == 30
    assert len(result.model_usage["attempts"]) == 2
    assert all(
        attempt["retryReason"] == "execution_critic_repair"
        for attempt in result.model_usage["attempts"]
    )


@pytest.mark.parametrize("agent_type", [ExecutionAgent, ExecutionDesignerAgent])
def test_execution_preflight_repairs_once_before_returning_candidate(agent_type) -> None:
    cyclic = _blueprint(
        tasks=[
            _task("task-1", dependencies=["task-2"]),
            _task("task-2", dependencies=["task-1"]),
        ]
    )
    repaired = _blueprint(tasks=[_task("task-1"), _task("task-2", dependencies=["task-1"])])
    model = _SequencedModel([cyclic, repaired])

    result = agent_type(model=model).run(_goal(), _evidence(), _strategy(), reality=_reality())

    assert len(model.calls) == 2
    assert model.calls[1]["payload"]["preflightIssues"]
    assert model.calls[1]["payload"]["invalidExecutionBlueprint"]["tasks"]
    assert result.artifact == repaired
    assert result.model_usage["generationMode"] == "single_pass"
    assert result.model_usage["totalTokens"] == 30
    assert result.model_usage["attempts"][-1]["retryReason"] == "execution_preflight"
    assert result.model_usage["attempts"][-1].get("automaticRetry") is not True


@pytest.mark.parametrize("agent_type", [ExecutionAgent, ExecutionDesignerAgent])
def test_execution_preflight_failure_blocks_after_one_repair(agent_type) -> None:
    cyclic = _blueprint(
        tasks=[
            _task("task-1", dependencies=["task-2"]),
            _task("task-2", dependencies=["task-1"]),
        ]
    )
    model = _SequencedModel([cyclic, cyclic])

    with pytest.raises(PlanningModelUnavailable) as exc_info:
        agent_type(model=model).run(_goal(), _evidence(), _strategy(), reality=_reality())

    assert len(model.calls) == 2
    assert exc_info.value.stage == "execution_design"
    assert exc_info.value.error.error_type == "invalid_model_output"
    assert "deterministic preflight" in exc_info.value.error.message


def test_preflight_normalizes_weekly_period_aliases_before_capacity_check() -> None:
    goal = _goal(
        constraints=[
            Constraint(
                statement="Weekly capacity is one hour.",
                sourceText="每周投入1小时",
                category="schedule",
            )
        ]
    )
    execution = _blueprint(
        tasks=[
            _task("task-1", minutes=40, weekly_minutes={"week 1": 40}),
            _task("task-2", minutes=40, weekly_minutes={"第1周": 40}),
        ]
    )

    with pytest.raises(DeterministicGuardError, match="exceeds explicit weekly capacity"):
        validate_execution_preflight(execution, goal=goal, reality=_reality())


def test_preflight_rejects_provably_reversed_relative_dependency_schedule() -> None:
    blueprint = _blueprint(
        tasks=[
            _task("training", window="Weeks 7-9"),
            _task("week-3-test", dependencies=["training"], window="Week 3"),
        ]
    )

    with pytest.raises(DeterministicGuardError, match="occurs before dependency") as exc_info:
        validate_execution_preflight(
            blueprint,
            goal=_goal(),
            reality=_reality(),
        )

    assert any("week-3-test" in issue and "training" in issue for issue in exc_info.value.issues)


def test_preflight_rejects_provably_reversed_exact_date_dependency_schedule() -> None:
    blueprint = _blueprint(
        tasks=[
            _task("prerequisite", window=None, scheduled_date="2026-08-10"),
            _task(
                "dependent",
                dependencies=["prerequisite"],
                window=None,
                scheduled_date="2026-08-01",
            ),
        ]
    )

    with pytest.raises(DeterministicGuardError, match="occurs before dependency"):
        validate_execution_preflight(
            blueprint,
            goal=_goal(),
            reality=_reality(),
        )


def test_preflight_does_not_guess_order_inside_overlapping_relative_ranges() -> None:
    blueprint = _blueprint(
        tasks=[
            _task("prerequisite", window="Weeks 1-3"),
            _task("dependent", dependencies=["prerequisite"], window="Weeks 3-4"),
        ]
    )

    validate_execution_preflight(
        blueprint,
        goal=_goal(),
        reality=_reality(),
    )


@pytest.mark.parametrize(
    ("prerequisite_window", "dependent_window"),
    [
        ("出行前8周", "出行前6周"),
        ("8 weeks before departure", "6 weeks before departure"),
    ],
)
def test_preflight_accepts_forward_order_on_countdown_week_axis(
    prerequisite_window: str,
    dependent_window: str,
) -> None:
    blueprint = _blueprint(
        tasks=[
            _task("prerequisite", window=prerequisite_window),
            _task("dependent", dependencies=["prerequisite"], window=dependent_window),
        ]
    )

    validate_execution_preflight(blueprint, goal=_goal(), reality=_reality())
    assert execution_preflight_context(blueprint, goal=_goal())[
        "dependencyScheduleViolations"
    ] == []


@pytest.mark.parametrize(
    ("prerequisite_window", "dependent_window"),
    [
        ("出行前6周", "出行前8周"),
        ("6 weeks before departure", "8 weeks before departure"),
    ],
)
def test_preflight_rejects_reversed_order_on_countdown_week_axis(
    prerequisite_window: str,
    dependent_window: str,
) -> None:
    blueprint = _blueprint(
        tasks=[
            _task("prerequisite", window=prerequisite_window),
            _task("dependent", dependencies=["prerequisite"], window=dependent_window),
        ]
    )

    with pytest.raises(DeterministicGuardError, match="occurs before dependency"):
        validate_execution_preflight(blueprint, goal=_goal(), reality=_reality())

    violations = execution_preflight_context(blueprint, goal=_goal())[
        "dependencyScheduleViolations"
    ]
    assert violations[0]["scheduleType"] == "countdown_week"


@pytest.mark.parametrize(
    ("prerequisite_window", "dependent_window"),
    [
        ("出行前6周", "住宿前8周"),
        ("6 weeks before departure", "8 weeks before arrival"),
        ("出行前6周", "Week 8"),
    ],
)
def test_preflight_does_not_compare_incompatible_week_axes(
    prerequisite_window: str,
    dependent_window: str,
) -> None:
    blueprint = _blueprint(
        tasks=[
            _task("prerequisite", window=prerequisite_window),
            _task("dependent", dependencies=["prerequisite"], window=dependent_window),
        ]
    )

    validate_execution_preflight(blueprint, goal=_goal(), reality=_reality())
    assert execution_preflight_context(blueprint, goal=_goal())[
        "dependencyScheduleViolations"
    ] == []


def test_execution_preflight_context_exposes_capacity_slack_and_dependency_order() -> None:
    goal = _goal(
        constraints=[
            Constraint(
                statement="Weekly capacity is five hours.",
                sourceText="5 hours per week",
                category="schedule",
            )
        ]
    )
    blueprint = _blueprint(
        tasks=[
            _task(
                "prerequisite",
                minutes=300,
                window="Week 8",
                weekly_minutes={"Week 8": 300},
            ),
            _task(
                "overlap",
                minutes=30,
                window="Week 8",
                weekly_minutes={"Week 8": 30},
            ),
            _task("early-test", dependencies=["prerequisite"], window="Week 2"),
        ]
    )

    context = execution_preflight_context(blueprint, goal=goal)

    assert context == {
        "maxTopLevelTasks": 10,
        "weeklyCapacityMinutes": 300,
        "periodTotals": {"relative-week:2": 30, "relative-week:8": 330},
        "periodSlack": {"relative-week:2": 270, "relative-week:8": -30},
        "taskAllocations": {
            "prerequisite": {"relative-week:8": 300},
            "overlap": {"relative-week:8": 30},
            "early-test": {"relative-week:2": 30},
        },
        "allocationIssues": [],
        "dependencyScheduleViolations": [
            {
                "taskId": "early-test",
                "dependencyId": "prerequisite",
                "taskSchedule": "Week 2",
                "dependencySchedule": "Week 8",
                "scheduleType": "relative_week",
                "reason": (
                    "early-test schedule Week 2 occurs before dependency prerequisite "
                    "schedule Week 8"
                ),
            }
        ],
    }


@pytest.mark.parametrize(
    ("weekly_minutes", "expected"),
    [
        ({"Phase 1": 90}, "unrecognized weekly period"),
        ({"Week 1": "NaN"}, "non-numeric allocation"),
        ({"Week 1": "Infinity"}, "non-numeric allocation"),
    ],
)
def test_preflight_rejects_non_comparable_or_non_finite_weekly_allocations(
    weekly_minutes: dict[str, Any],
    expected: str,
) -> None:
    goal = _goal(
        constraints=[
            Constraint(
                statement="Weekly capacity is one hour.",
                sourceText="每周投入1小时",
                category="schedule",
            )
        ]
    )
    task = _task("task-1", minutes=90)
    task.domain_extensions = {"weeklyMinutes": weekly_minutes}

    with pytest.raises(DeterministicGuardError, match=expected):
        validate_execution_preflight(
            _blueprint(tasks=[task]),
            goal=goal,
            reality=_reality(),
        )


def test_preflight_preserves_an_explicit_cny_hard_cap() -> None:
    goal = _goal(
        constraints=[
            Constraint(
                statement="The total budget is capped at CNY 20,000.",
                sourceText="总预算2万元",
                category="budget",
            )
        ]
    )

    with pytest.raises(DeterministicGuardError, match="requires budgetSummary"):
        validate_execution_preflight(_blueprint(), goal=goal, reality=_reality())

    compliant = _blueprint(
        budget=ExecutionBudgetSummary(
            spendingLimitCny=20_000,
            allocations=[ExecutionBudgetAllocation(category="all costs", amountCny=18_000)],
        )
    )
    validate_execution_preflight(compliant, goal=goal, reality=_reality())


def test_preflight_requires_a_calendar_date_or_relative_schedule_window() -> None:
    task = _task("task-1")
    task.schedule_window = None

    with pytest.raises(DeterministicGuardError, match="scheduledDate or scheduleWindow"):
        validate_execution_preflight(
            _blueprint(tasks=[task]),
            goal=_goal(),
            reality=_reality(),
        )


def test_preflight_requires_complete_narrative_and_risk_text() -> None:
    blueprint = _blueprint()
    blueprint.narrative.risk_handling = ""
    blueprint.tasks[0].risks = [""]

    with pytest.raises(DeterministicGuardError) as exc_info:
        validate_execution_preflight(
            blueprint,
            goal=_goal(),
            reality=_reality(),
        )

    assert "riskHandling" in exc_info.value.issues[0]
    assert any("risk description" in issue for issue in exc_info.value.issues)


@pytest.mark.parametrize("generation_mode", ["two_pass_fallback", "two_pass_repair"])
def test_model_usage_round_trips_execution_generation_mode_and_retry_reason(
    generation_mode: str,
) -> None:
    usage = ModelUsage.model_validate(
        {
            **_usage(1),
            "generationMode": generation_mode,
            "attempts": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "status": "success",
                    "automaticRetry": True,
                    "retryReason": "execution_two_pass_fallback",
                }
            ],
        }
    )

    payload = usage.model_dump(by_alias=True)
    assert payload["generationMode"] == generation_mode
    assert payload["attempts"][0]["retryReason"] == "execution_two_pass_fallback"
