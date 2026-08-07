from __future__ import annotations

from app.cognitive_planning.planning import (
    ConstraintCompiler,
    ContextClaim,
    ContextPack,
    EffortEstimate,
    PlanBlueprint,
    PlanHardValidator,
    PlanMilestone,
    PlanTask,
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
    assert constraints.core.weekday_capacity_minutes == 600
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

    assert constraints.core.weekday_capacity_minutes is None
    assert [value.statement for value in constraints.semantic] == [
        "Study 1 hour on weekdays and 2 hours on weekend days"
    ]


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
