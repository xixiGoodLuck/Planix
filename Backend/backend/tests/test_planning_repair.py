from __future__ import annotations

import pytest

from app.cognitive_planning.planning import (
    ArtifactInvalidator,
    ConstraintCompiler,
    ContextPack,
    EffortEstimate,
    PatchGuard,
    PlanBlueprint,
    PlanHardValidator,
    PlanMilestone,
    PlanTask,
    QualityIssue,
    QualityReport,
    RepairBudget,
    RepairOperation,
    RepairProposal,
    SemanticItem,
    UnderstandingReadiness,
    UnderstandingSnapshot,
)


def semantic(key: str, statement: str) -> SemanticItem:
    return SemanticItem(
        id=f"item-{key}",
        key=key,
        statement=statement,
        sourceType="user_confirmed",
        sourceRef=f"turn:{key}",
    )


def fixtures():
    snapshot = UnderstandingSnapshot(
        artifactId="understanding-repair",
        goalSummary="完成项目",
        successSignals=[semantic("success", "完成演示")],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )
    constraints = ConstraintCompiler().compile(snapshot)
    context = ContextPack(
        artifactId="context-repair",
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
    )
    task = PlanTask(
        id="task-1",
        milestoneId="milestone-1",
        title="实现",
        purpose="交付",
        whyNow="先建立核心",
        actionSteps=["编码"],
        effortEstimate=EffortEstimate(
            minMinutes=30,
            expectedMinutes=60,
            maxMinutes=90,
            confidence=0.7,
            estimationBasis="历史任务",
        ),
        deliverable="提交",
        completionEvidence=["测试通过"],
        risks=["环境"],
        fallback="保留核心功能",
    )
    plan = PlanBlueprint(
        artifactId="plan-repair",
        goalSummary=snapshot.goal_summary,
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
        contextRef=context.artifact_id,
        milestones=[PlanMilestone(id="milestone-1", title="交付", purpose="完成")],
        tasks=[task],
    )
    issue = QualityIssue(
        issueId="success_coverage:item-success",
        category="content",
        severity="major",
        ruleId="success_coverage",
        targetType="success_signal",
        targetId="item-success",
        description="成功标准缺少任务覆盖",
        allowedOperations=["add_success_coverage"],
        repairBasis="deterministic_validator",
    )
    return snapshot, constraints, context, plan, issue


def test_domain_repair_operation_uses_stable_task_id_and_regression_validation():
    snapshot, constraints, context, plan, issue = fixtures()
    proposal = RepairProposal(
        artifactId=plan.artifact_id,
        artifactVersion=plan.version,
        issueId=issue.issue_id,
        operations=[
            RepairOperation(
                operation="add_success_coverage",
                targetId="task-1",
                payload={"sourceGoalRefs": ["item-success"]},
            )
        ],
    )
    revised, result = PatchGuard().apply_plan(
        plan,
        proposal,
        issue,
        validator=PlanHardValidator(),
        snapshot=snapshot,
        constraints=constraints,
        context=context,
    )
    assert result.accepted is True
    assert revised.version == 2
    assert revised.tasks[0].source_goal_refs == ["item-success"]
    assert "schedule" in result.invalidated_artifacts


def test_patch_guard_rejects_operation_outside_issue_scope():
    snapshot, constraints, context, plan, issue = fixtures()
    proposal = RepairProposal(
        artifactId=plan.artifact_id,
        artifactVersion=plan.version,
        issueId=issue.issue_id,
        operations=[
            RepairOperation(
                operation="update_task",
                targetId="task-1",
                payload={"title": "越权修改"},
            )
        ],
    )
    with pytest.raises(ValueError, match="not allowed"):
        PatchGuard().apply_plan(
            plan,
            proposal,
            issue,
            validator=PlanHardValidator(),
            snapshot=snapshot,
            constraints=constraints,
            context=context,
        )


def test_patch_guard_rejects_stale_artifact_version():
    snapshot, constraints, context, plan, issue = fixtures()
    proposal = RepairProposal(
        artifactId=plan.artifact_id,
        artifactVersion=99,
        issueId=issue.issue_id,
        operations=[
            RepairOperation(
                operation="add_success_coverage",
                targetId="task-1",
                payload={"sourceGoalRefs": ["item-success"]},
            )
        ],
    )
    with pytest.raises(ValueError, match="stale"):
        PatchGuard().apply_plan(
            plan,
            proposal,
            issue,
            validator=PlanHardValidator(),
            snapshot=snapshot,
            constraints=constraints,
            context=context,
        )


def test_artifact_invalidation_is_code_owned():
    assert ArtifactInvalidator.downstream_of("understanding") == [
        "constraint",
        "context",
        "plan",
        "plan_quality",
        "schedule",
        "schedule_quality",
        "calendar",
    ]
    assert ArtifactInvalidator.downstream_of("effort") == [
        "schedule",
        "schedule_quality",
        "calendar",
    ]
    assert ArtifactInvalidator.downstream_of("presentation") == ["calendar"]


def test_repair_budget_stops_after_two_rounds_without_faking_pass():
    report = QualityReport(
        targetArtifactId="plan",
        targetVersion=3,
        hardRulesPassed=False,
        issues=[],
        repairRound=2,
    )
    with pytest.raises(ValueError, match="budget exhausted"):
        RepairBudget().assert_available(report)
    assert report.passed is False
