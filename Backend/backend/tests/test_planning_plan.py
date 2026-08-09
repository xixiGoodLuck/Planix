from __future__ import annotations

import pytest

from app.cognitive_planning.agents import AgentResult, PlanReviewer, PlanningModelUnavailable
from app.cognitive_planning.planning import (
    ConstraintCompiler,
    ContextClaim,
    ContextPack,
    EffortEstimate,
    PlanBlueprint,
    PlanHardValidator,
    PlanMilestone,
    PlanTask,
    QualityIssue,
    QualityReport,
    SemanticReviewResult,
    SemanticItem,
    UnderstandingReadiness,
    UnderstandingSnapshot,
)


def item(key: str, statement: str, policy: str = "user_confirmation_required") -> SemanticItem:
    return SemanticItem(
        id=f"item-{key}",
        key=key,
        statement=statement,
        sourceType="user_confirmed",
        sourceRef=f"turn:{key}",
        mutationPolicy=policy,
    )


def understanding() -> UnderstandingSnapshot:
    return UnderstandingSnapshot(
        artifactId="understanding-plan",
        goalSummary="完成可展示的求职项目",
        constraints=[
            item("capacity", "每周 10 小时", "immutable"),
            item("portfolio", "项目必须有面试展示价值", "immutable"),
        ],
        successSignals=[
            item("success:demo", "完成可运行演示"),
            item("success:interview", "能够在面试中讲解设计决策"),
        ],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )


def context(snapshot: UnderstandingSnapshot, constraint_ref: str, *, verified: bool = True) -> ContextPack:
    return ContextPack(
        artifactId="context-plan",
        understandingRef=snapshot.artifact_id,
        constraintRef=constraint_ref,
        claims=[
            ContextClaim(
                id="claim-doc",
                claim="官方文档",
                sourceType="tool_verified" if verified else "model_assumption",
                sourceRef="https://example.test/docs" if verified else "inference:docs",
                verificationStatus="verified" if verified else "inference",
                credibility=0.9,
            )
        ],
    )


def task(task_id: str, milestone_id: str, dependencies: list[str] | None = None) -> PlanTask:
    return PlanTask(
        id=task_id,
        milestoneId=milestone_id,
        title=f"任务 {task_id}",
        purpose="推进可运行项目",
        whyNow="为后续验证建立基础",
        actionSteps=["实现并运行"],
        dependencies=dependencies or [],
        effortEstimate=EffortEstimate(
            minMinutes=60,
            expectedMinutes=90,
            maxMinutes=120,
            confidence=0.7,
            estimationBasis="基于相似任务",
        ),
        priority="high",
        optionality="required",
        deliverable="可运行提交",
        completionEvidence=["测试通过"],
        resourceRefs=["https://example.test/docs"],
        risks=["环境差异"],
        fallback="缩小非必要展示范围但保留核心功能",
        sourceGoalRefs=["item-success:demo", "item-success:interview"],
        sourceConstraintRefs=["item-portfolio"],
    )


def plan(snapshot: UnderstandingSnapshot, constraint_ref: str) -> PlanBlueprint:
    milestone = PlanMilestone(
        id="milestone-1",
        title="项目交付",
        purpose="完成并验证项目",
        successSignalRefs=["item-success:demo", "item-success:interview"],
    )
    return PlanBlueprint(
        artifactId="plan-1",
        goalSummary=snapshot.goal_summary,
        understandingRef=snapshot.artifact_id,
        constraintRef=constraint_ref,
        contextRef="context-plan",
        milestones=[milestone],
        tasks=[task("task-1", milestone.id), task("task-2", milestone.id, ["task-1"])],
    )


def test_constraint_compiler_keeps_core_and_semantic_layers_separate():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    assert constraints.core.weekly_capacity_minutes == 600
    assert constraints.core.weekday_capacity_minutes is None
    assert constraints.core.required_deliverables == [
        "完成可运行演示",
        "能够在面试中讲解设计决策",
    ]
    assert [value.statement for value in constraints.semantic] == ["项目必须有面试展示价值"]
    assert constraints.semantic[0].mutation_policy == "immutable"


def test_plan_hard_validator_accepts_dag_and_success_coverage():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current = plan(snapshot, constraints.artifact_id)
    report = PlanHardValidator().validate(
        current,
        snapshot=snapshot,
        constraints=constraints,
        context=context(snapshot, constraints.artifact_id),
    )
    assert report.passed is True
    assert report.issues == []


def test_constraint_compiler_does_not_treat_weekdays_as_weekly_capacity():
    snapshot = UnderstandingSnapshot(
        artifactId="understanding-daily-capacity",
        goalSummary="Learn Python",
        constraints=[item("daily", "Study 1 hour on weekdays and 2 hours on weekend days", "immutable")],
        successSignals=[item("success", "Complete a project")],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )

    constraints = ConstraintCompiler().compile(snapshot)

    assert constraints.core.weekday_capacity_minutes == 60
    assert constraints.core.weekend_capacity_minutes == 120
    assert constraints.semantic == []


def test_constraint_compiler_parses_compact_chinese_day_horizon():
    snapshot = UnderstandingSnapshot(
        artifactId="understanding-30-days",
        goalSummary="30天内完成学习目标",
        constraints=[item("horizon", "必须在30天内完成", "immutable"), item("daily", "每天学习1小时", "immutable")],
        successSignals=[item("success", "完成一个可运行项目")],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )

    constraints = ConstraintCompiler().compile(snapshot)

    assert constraints.core.planning_horizon == "30 days"
    assert constraints.core.weekday_capacity_minutes == 60
    assert constraints.core.weekend_capacity_minutes == 60


def test_plan_hard_validator_rejects_dependency_cycle():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current = plan(snapshot, constraints.artifact_id)
    cyclic = current.model_copy(
        update={
            "tasks": [
                current.tasks[0].model_copy(update={"dependencies": ["task-2"]}),
                current.tasks[1],
            ]
        }
    )
    report = PlanHardValidator().validate(
        cyclic,
        snapshot=snapshot,
        constraints=constraints,
        context=context(snapshot, constraints.artifact_id),
    )
    assert report.passed is False
    assert "dependency_dag" in {value.rule_id for value in report.issues}


def test_plan_hard_validator_rejects_missing_success_signal_coverage():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current = plan(snapshot, constraints.artifact_id)
    reduced = current.model_copy(
        update={
            "tasks": [
                value.model_copy(update={"source_goal_refs": ["item-success:demo"]})
                for value in current.tasks
            ]
        }
    )
    report = PlanHardValidator().validate(
        reduced,
        snapshot=snapshot,
        constraints=constraints,
        context=context(snapshot, constraints.artifact_id),
    )
    assert "success_coverage" in {value.rule_id for value in report.issues}


def test_plan_hard_validator_rejects_unverified_external_fact():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current = plan(snapshot, constraints.artifact_id)
    external = current.model_copy(
        update={
            "tasks": [
                current.tasks[0].model_copy(
                    update={"title": "使用 https://example.test/docs 当前版本"}
                ),
                current.tasks[1],
            ]
        }
    )
    report = PlanHardValidator().validate(
        external,
        snapshot=snapshot,
        constraints=constraints,
        context=context(snapshot, constraints.artifact_id, verified=False),
    )
    assert "provenance" in {value.rule_id for value in report.issues}


def test_plan_validator_does_not_treat_version_control_as_an_external_claim():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current = plan(snapshot, constraints.artifact_id)
    current = current.model_copy(
        update={
            "tasks": [
                current.tasks[0].model_copy(update={"purpose": "Set up Git version control"}),
                current.tasks[1],
            ]
        }
    )
    report = PlanHardValidator().validate(
        current,
        snapshot=snapshot,
        constraints=constraints,
        context=context(snapshot, constraints.artifact_id),
    )
    assert "provenance" not in {value.rule_id for value in report.issues}


class ReviewModel:
    def __init__(self, issue: QualityIssue):
        self.issue = issue

    def complete_contract(self, **_kwargs):
        return AgentResult(
            SemanticReviewResult(
                targetArtifactId="placeholder",
                targetVersion=1,
                issues=[self.issue],
            ),
            {"attempts": []},
        )


@pytest.mark.parametrize(
    "issue",
    [
        QualityIssue(
            issueId="invalid-evidence",
            category="content",
            severity="major",
            ruleId="semantic_fit",
            targetType="plan",
            targetId="task-1",
            description="Evidence does not exist",
            evidenceRefs=["missing-ref"],
            allowedOperations=["update_task"],
            repairBasis="semantic_review",
        ),
        QualityIssue(
            issueId="unsupported-operation",
            category="content",
            severity="major",
            ruleId="semantic_fit",
            targetType="plan",
            targetId="task-1",
            description="Operation is not valid for Plan repair",
            evidenceRefs=["task-1"],
            allowedOperations=["update_calendar_presentation"],
            repairBasis="semantic_review",
        ),
    ],
)
def test_semantic_reviewer_rejects_invalid_evidence_or_unsupported_repair(issue):
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current_context = context(snapshot, constraints.artifact_id)
    current_plan = plan(snapshot, constraints.artifact_id)
    hard = QualityReport(targetArtifactId=current_plan.artifact_id, targetVersion=1, hardRulesPassed=True)
    with pytest.raises(PlanningModelUnavailable) as exc:
        PlanReviewer(ReviewModel(issue)).run(snapshot, constraints, current_context, current_plan, hard)
    assert exc.value.error.error_type == "invalid_model_output"


def test_semantic_reviewer_accepts_current_success_signal_as_target_and_evidence():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current_context = context(snapshot, constraints.artifact_id)
    current_plan = plan(snapshot, constraints.artifact_id)
    issue = QualityIssue(
        issueId="missing-success-path",
        category="content",
        severity="major",
        ruleId="semantic_fit",
        targetType="success_signal",
        targetId="item-success:demo",
        description="The success path needs a clearer task",
        evidenceRefs=["item-success:demo"],
        allowedOperations=["add_task"],
        repairBasis="semantic_review",
    )
    hard = QualityReport(targetArtifactId=current_plan.artifact_id, targetVersion=1, hardRulesPassed=True)
    result = PlanReviewer(ReviewModel(issue)).run(snapshot, constraints, current_context, current_plan, hard)
    assert result.artifact.issues == [issue]


def test_semantic_reviewer_intersects_operations_with_patchguard_support():
    snapshot = understanding()
    constraints = ConstraintCompiler().compile(snapshot)
    current_context = context(snapshot, constraints.artifact_id)
    current_plan = plan(snapshot, constraints.artifact_id)
    issue = QualityIssue(
        issueId="mixed-operations",
        category="content",
        severity="major",
        ruleId="semantic_fit",
        targetType="task",
        targetId="task-1",
        description="Task needs an actionable correction",
        evidenceRefs=["task-1"],
        allowedOperations=["update_task", "update_calendar_presentation"],
        repairBasis="semantic_review",
    )
    hard = QualityReport(targetArtifactId=current_plan.artifact_id, targetVersion=1, hardRulesPassed=True)
    result = PlanReviewer(ReviewModel(issue)).run(snapshot, constraints, current_context, current_plan, hard)
    assert result.artifact.issues[0].allowed_operations == ["update_task"]


def test_semantic_reviewer_cannot_promote_nonblocking_unknown_or_task_array_order():
    snapshot = understanding().model_copy(
        update={"unknowns": [item("project-topic", "Project topic is not selected yet")]}
    )
    nonblocking = QualityIssue(
        issueId="nonblocking-unknown",
        category="content",
        severity="major",
        ruleId="goal_alignment",
        targetType="task",
        targetId="task-1",
        description="The unknown project topic requires user confirmation.",
        evidenceRefs=["item-project-topic", "task-1"],
        allowedOperations=["update_task"],
        repairBasis="semantic_review",
    )
    array_order = nonblocking.model_copy(
        update={
            "issue_id": "array-order",
            "description": "task-2 is listed after task-1 in the task array, creating a forward dependency.",
            "evidence_refs": ["task-1", "task-2"],
        }
    )
    schedule_capacity = nonblocking.model_copy(
        update={
            "issue_id": "schedule-capacity",
            "description": "The single task exceeds the daily time limit and must split across sessions.",
            "evidence_refs": ["task-1"],
        }
    )
    prerequisite = nonblocking.model_copy(
        update={
            "issue_id": "prerequisite",
            "description": "The snapshot does not confirm testing knowledge, so the learning task should be optional.",
            "evidence_refs": ["task-1"],
        }
    )
    schedule_timing = nonblocking.model_copy(
        update={
            "issue_id": "schedule-timing",
            "description": "Task-2 may be scheduled after week six and not complete before applications start.",
            "evidence_refs": ["task-2"],
        }
    )
    contradictory_missing_task = nonblocking.model_copy(
        update={
            "issue_id": "contradictory-missing-task",
            "description": "The plan does not include any explicit task for a resume, although task-12 creates it.",
            "evidence_refs": ["task-12"],
        }
    )
    legitimate = nonblocking.model_copy(
        update={
            "issue_id": "legitimate",
            "description": "The required task has no executable acceptance step.",
            "evidence_refs": ["task-1"],
        }
    )

    assert PlanReviewer._outside_semantic_authority(nonblocking, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(array_order, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(schedule_capacity, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(prerequisite, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(schedule_timing, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(contradictory_missing_task, snapshot) is True
    assert PlanReviewer._outside_semantic_authority(legitimate, snapshot) is False
