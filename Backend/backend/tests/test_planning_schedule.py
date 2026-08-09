from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.cognitive_planning.planning import (
    CalendarMaterializer,
    ConstraintCompiler,
    FeedbackRouter,
    FinalApprovalService,
    PlanBlueprint,
    PlanMilestone,
    PlanTask,
    ScheduleGenerator,
    ScheduleValidator,
    SemanticItem,
    UnderstandingReadiness,
    UnderstandingSnapshot,
    EffortEstimate,
)


def fixtures():
    signal = SemanticItem(
        id="signal-1",
        key="success",
        statement="完成演示",
        sourceType="user_confirmed",
        sourceRef="turn-1",
    )
    snapshot = UnderstandingSnapshot(
        artifactId="understanding-schedule",
        goalSummary="完成项目",
        constraints=[
            SemanticItem(
                id="constraint-session",
                key="session",
                statement="单次任务不超过 120 分钟",
                sourceType="user_confirmed",
                sourceRef="turn-1",
            )
        ],
        successSignals=[signal],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )
    constraints = ConstraintCompiler().compile(snapshot)
    constraints.core.maximum_session_minutes = 120
    constraints.core.weekday_capacity_minutes = 240
    milestone = PlanMilestone(id="milestone-1", title="交付", purpose="完成项目")

    def task(task_id: str, dependencies: list[str] | None = None) -> PlanTask:
        return PlanTask(
            id=task_id,
            milestoneId=milestone.id,
            title=f"任务 {task_id}",
            purpose="完成核心工作",
            whyNow="保持依赖顺序",
            actionSteps=["执行"],
            dependencies=dependencies or [],
            effortEstimate=EffortEstimate(
                minMinutes=60,
                expectedMinutes=120,
                maxMinutes=150,
                confidence=0.7,
                estimationBasis="相似任务",
            ),
            deliverable="产物",
            completionEvidence=["验收通过"],
            risks=["延期"],
            fallback="移动未开始时段",
            sourceGoalRefs=[signal.id],
        )

    plan = PlanBlueprint(
        artifactId="plan-schedule",
        goalSummary=snapshot.goal_summary,
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
        contextRef="context-schedule",
        milestones=[milestone],
        tasks=[task("task-1"), task("task-2", ["task-1"])],
    )
    return snapshot, constraints, plan


def test_schedule_generation_preserves_plan_identity_effort_and_dependency_order():
    _, constraints, plan = fixtures()
    start = datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8)))
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=start,
        calendar_snapshot_ref="calendar-snapshot-1",
    )
    report = ScheduleValidator().validate(
        schedule,
        plan=plan,
        constraints=constraints,
        current_calendar_snapshot_ref="calendar-snapshot-1",
    )
    assert report.passed is True
    assert {value.task_id for value in schedule.sessions} == {"task-1", "task-2"}
    assert sum(value.duration_minutes for value in schedule.sessions) == 240
    assert schedule.sessions[1].start > schedule.sessions[0].end


def test_schedule_validator_rejects_calendar_conflict_without_deleting_task():
    _, constraints, plan = fixtures()
    start = datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8)))
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=start,
        calendar_snapshot_ref="calendar-snapshot-1",
    )
    busy = [(start + timedelta(minutes=30), start + timedelta(minutes=90))]
    report = ScheduleValidator().validate(
        schedule,
        plan=plan,
        constraints=constraints,
        current_calendar_snapshot_ref="calendar-snapshot-1",
        calendar_busy=busy,
    )
    assert "calendar_conflict" in {value.rule_id for value in report.issues}
    assert {session.task_id for session in schedule.sessions} == {"task-1", "task-2"}


def test_calendar_materialization_is_deterministic_and_idempotent_by_source_key():
    _, constraints, plan = fixtures()
    start = datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8)))
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=start,
        calendar_snapshot_ref="calendar-snapshot-1",
    )
    materializer = CalendarMaterializer()
    first = materializer.materialize(
        plan,
        schedule,
        timezone="Asia/Shanghai",
        current_calendar_snapshot_ref="calendar-snapshot-1",
    )
    second = materializer.materialize(
        plan,
        schedule,
        timezone="Asia/Shanghai",
        current_calendar_snapshot_ref="calendar-snapshot-1",
    )
    assert [value.source_key for value in first.events] == [value.source_key for value in second.events]
    assert len({value.source_key for value in first.events}) == len(first.events)
    assert {value.source_task_id for value in first.events} == {"task-1", "task-2"}


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("目标改成求职项目", "understanding_change"),
        ("把第二个任务拆开", "plan_change"),
        ("周三不要安排", "schedule_change"),
        ("替换这门课程", "resource_change"),
        ("修改日历标题", "presentation_change"),
        ("确认并写入日历", "approve"),
        ("拒绝这个计划", "reject"),
    ],
)
def test_final_feedback_router_targets_correct_stage(text: str, category: str):
    assert FeedbackRouter().route(text).category == category


def test_final_approval_rejects_stale_version_and_consumed_replay():
    service = FinalApprovalService()
    approval = service.create(
        session_id="session-1",
        understanding_version=2,
        constraint_version=1,
        context_version=1,
        plan_version=3,
        quality_report_version=2,
        schedule_version=2,
        schedule_quality_report_version=2,
        calendar_proposal_version=1,
        calendar_snapshot_version=4,
        checkpoint_version=7,
    )
    versions = {
        "understanding": 2,
        "constraint": 1,
        "context": 1,
        "plan": 3,
        "quality_report": 2,
        "schedule": 2,
        "schedule_quality": 2,
        "calendar_proposal": 1,
        "calendar_snapshot": 4,
        "checkpoint": 7,
    }
    service.assert_current(approval, versions)
    with pytest.raises(ValueError, match="plan"):
        service.assert_current(approval, {**versions, "plan": 4})
    with pytest.raises(ValueError, match="consumed"):
        service.assert_current(approval.model_copy(update={"consumed": True}), versions)
