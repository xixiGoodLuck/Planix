from __future__ import annotations

import pytest

from app.services.cognitive_planning.contracts import (
    Constraint,
    CriticRepairRequest,
    CritiqueDimensions,
    CritiqueIssue,
    ExecutionBlueprint,
    ExecutionBlueprintTask,
    ExecutionNarrative,
    ExecutionResource,
    GoalSuccessModel,
    PlanCritiqueReport,
    UserGoalModel,
)
from app.services.cognitive_planning.evaluation import (
    critic_policy_context,
    critic_policy_violations,
)


def _goal(*, weekly_capacity: bool = False) -> UserGoalModel:
    constraints = []
    if weekly_capacity:
        constraints.append(
            Constraint(
                statement="每周最多投入 6 小时",
                source_text="我每周可投入 6 小时",
                category="schedule",
            )
        )
    return UserGoalModel(
        goal_statement="完成一个可验证的学习项目",
        desired_change="能够独立完成项目",
        domain="learning",
        hard_constraints=constraints,
        success_model=GoalSuccessModel(
            definition="项目通过验收",
            measurable_signals=["验收记录"],
        ),
        confidence=0.9,
        can_proceed_to_evidence=True,
    )


def _execution(*, source_ref: str | None = None) -> ExecutionBlueprint:
    return ExecutionBlueprint(
        narrative=ExecutionNarrative(
            execution_logic="先准备再实践",
            dependency_explanation="实践依赖准备",
            weekly_or_stage_rhythm="按阶段推进",
            workload_reasoning="工作量按任务估算",
            risk_handling="资源不可用时使用备用资源",
        ),
        tasks=[
            ExecutionBlueprintTask(
                id="task-1",
                title="完成实践",
                purpose="形成可验证成果",
                why_now="基础信息已经明确",
                action_steps=["选择资源并核验", "完成实践"],
                estimated_minutes=240,
                difficulty="medium",
                schedule_window="Week 1",
                completion_evidence=["成果文件"],
                deliverable="项目成果",
                resources=[
                    ExecutionResource(
                        title="候选学习资源",
                        type="guide",
                        source_ref=source_ref,
                        exact_usage="按选择标准核验后用于实践",
                        expected_contribution="提供实践步骤",
                        fallback_resource="改用另一个满足相同标准的资源",
                    )
                ],
                risks=["资源可能不可用"],
                fallback_action="按选择标准改用备用资源",
            )
        ],
        resource_coverage="strong",
    )


def _dimensions() -> CritiqueDimensions:
    return CritiqueDimensions(
        user_fit=70,
        goal_alignment=70,
        domain_correctness=70,
        feasibility=70,
        safety=70,
        task_specificity=70,
        resource_actionability=70,
        schedule_fit=70,
        adaptability=70,
    )


def _repair_report(
    *,
    description: str,
    evidence: str,
    instruction: str = "修复所述问题",
    expected_change: str = "问题得到修复",
) -> PlanCritiqueReport:
    return PlanCritiqueReport(
        status="needs_repair",
        score=70,
        dimensions=_dimensions(),
        issues=[
            CritiqueIssue(
                severity="major",
                description=description,
                evidence=evidence,
                responsible_agent="execution_designer",
            )
        ],
        repair_requests=[
            CriticRepairRequest(
                target_agent="execution_designer",
                instruction=instruction,
                expected_change=expected_change,
            )
        ],
        calendar_writable=False,
    )


@pytest.mark.parametrize(
    ("description", "evidence"),
    [
        (
            "半时间模拟要求把每周投入固定为 180 分钟。",
            "当前每周工作量超出该上限。",
        ),
        (
            "The half-time simulation fails because weekly workload exceeds a hard cap of 3 hours.",
            "The primary plan uses more than that quota.",
        ),
    ],
)
def test_guard_rejects_half_time_contingency_used_as_unsourced_weekly_quota(
    description: str,
    evidence: str,
) -> None:
    report = _repair_report(description=description, evidence=evidence)

    violations = critic_policy_violations(
        report,
        goal=_goal(),
        execution=_execution(),
    )

    assert [item["code"] for item in violations] == [
        "half_time_contingency_promoted_to_primary_quota"
    ]
    assert violations[0]["sourceKind"] == "issue"


def test_guard_links_a_weekly_quota_repair_to_its_half_time_issue() -> None:
    report = _repair_report(
        description="半时间演练没有通过。",
        evidence="该情景被当成了主计划约束。",
        instruction="将每周工作量限制在 180 分钟。",
        expected_change="所有周都不得超过 180 分钟。",
    )

    violations = critic_policy_violations(
        report,
        goal=_goal(),
        execution=_execution(),
    )

    assert [item["code"] for item in violations] == [
        "half_time_contingency_promoted_to_primary_quota"
    ]
    assert violations[0]["sourceKind"] == "repair_request"


def test_guard_does_not_override_an_explicit_goal_weekly_capacity() -> None:
    report = _repair_report(
        description="半时间模拟要求每周工作量最多为 3 小时。",
        evidence="该值来自用户每周 6 小时容量的一半。",
    )

    assert critic_policy_violations(
        report,
        goal=_goal(weekly_capacity=True),
        execution=_execution(),
    ) == []


def test_guard_checks_source_demands_in_issue_and_repair_request() -> None:
    report = _repair_report(
        description="资源缺少 sourceRef 或具体服务商信息，因此不可操作。",
        evidence="现有资源没有 URL。",
        instruction="为每个资源补充 URL 或服务商名称。",
        expected_change="每个资源都列出可点击链接和具体提供商。",
    )

    violations = critic_policy_violations(
        report,
        goal=_goal(),
        execution=_execution(),
    )

    assert [item["code"] for item in violations] == [
        "unverified_resource_reference_promoted_to_hard_requirement",
        "unverified_resource_reference_promoted_to_hard_requirement",
    ]
    assert [item["sourceKind"] for item in violations] == [
        "issue",
        "repair_request",
    ]


def test_guard_accepts_selection_and_fallback_when_source_ref_is_optional() -> None:
    report = _repair_report(
        description="安全停止条件缺失。sourceRef 可选，不需要提供 URL。",
        evidence="资源可以通过选择标准、核验步骤和备用方案保持可操作。",
        instruction="补充清晰的安全停止条件，不要求资源链接或服务商名称。",
        expected_change="安全条件明确，资源仍按选择标准核验。",
    )

    assert critic_policy_violations(
        report,
        goal=_goal(),
        execution=_execution(),
    ) == []


def test_guard_does_not_infer_a_missing_reference_when_all_resources_have_one() -> None:
    report = _repair_report(
        description="Every resource must include a sourceRef and URL.",
        evidence="The Critic requests named providers.",
    )

    assert critic_policy_violations(
        report,
        goal=_goal(),
        execution=_execution(source_ref="https://example.test/guide"),
    ) == []


def test_guard_context_is_machine_readable_and_does_not_fabricate_capacity() -> None:
    context = critic_policy_context(goal=_goal(), execution=_execution())

    assert context["weeklyCapacity"] == {
        "explicitInGoal": False,
        "minutes": None,
    }
    assert context["halfTimeSimulation"]["isPrimaryWeeklyQuota"] is False
    assert context["resources"]["withoutSourceRef"] == 1
    assert context["resources"]["sourceRefRequiredForActionability"] is False
    assert context["resources"]["missingSourceRefs"] == [
        {
            "taskId": "task-1",
            "resourceIndex": 1,
            "title": "候选学习资源",
        }
    ]
