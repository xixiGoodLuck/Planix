from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.cognitive_planning.planning import (
    ConstraintCompiler,
    ConstraintSet,
    ContextPack,
    CoreConstraints,
    EffortEstimate,
    PlanBlueprint,
    PlanHardValidator,
    PlanMilestone,
    PlanTask,
    QualityReport,
    QualityIssue,
    SchedulePatchGuard,
    ScheduleGenerator,
    ScheduleValidator,
    SemanticItem,
    UnderstandingReadiness,
    UnderstandingSnapshot,
)
from app.db import get_conn
from app.cognitive_planning.persistence import PlanningPersistence, json_object
from app.cognitive_planning.artifact_audit import PlanningArtifactAuditStore
from app.cognitive_planning.runtime import CognitiveOSRuntime
from app.services.command_agent import CommandAgentService
from app.services.plans import upsert_calendar_plans
from app.services.secret_store import InMemorySecretStore, get_secret_store, provider_secret_key


def snapshot_with(*constraints: str) -> UnderstandingSnapshot:
    return UnderstandingSnapshot(
        artifactId="understanding-invariants",
        goalSummary="完成可验证项目",
        constraints=[
            SemanticItem(
                id=f"constraint-{index}",
                key=f"constraint-{index}",
                statement=statement,
                sourceType="user_confirmed",
                sourceRef="turn-1",
                mutationPolicy="immutable",
            )
            for index, statement in enumerate(constraints)
        ],
        successSignals=[
            SemanticItem(
                id="signal-1",
                key="success",
                statement="完成可验收交付物",
                sourceType="user_confirmed",
                sourceRef="turn-1",
            )
        ],
        readiness=UnderstandingReadiness(readyForConfirmation=True, confirmed=True),
    )


@pytest.mark.parametrize(
    ("statement", "weekly", "weekday", "weekend"),
    [
        ("每周 10 小时", 600, None, None),
        ("10 hours per week", 600, None, None),
        ("The user can invest eight hours per week.", 480, None, None),
        ("每天 1 小时", None, 60, 60),
        ("90 minutes daily", None, 90, 90),
        ("工作日每天 1 小时", None, 60, None),
        ("周末每天 2 小时", None, None, 120),
        ("工作日 1 小时、周末 3 小时", None, 60, 180),
        ("一周最多 8 小时", 480, None, None),
    ],
)
def test_constraint_compiler_distinguishes_weekly_and_daily_capacity(statement, weekly, weekday, weekend):
    core = ConstraintCompiler().compile(snapshot_with(statement)).core
    assert core.weekly_capacity_minutes == weekly
    assert core.weekday_capacity_minutes == weekday
    assert core.weekend_capacity_minutes == weekend


def test_constraint_compiler_reads_relative_horizon_and_capacity_from_confirmed_facts():
    snapshot = snapshot_with().model_copy(
        update={
            "constraints": [
                SemanticItem(id="mock-window", key="mock_window", statement="Use the final three days for mock exams.", sourceType="user_confirmed", sourceRef="turn:1")
            ],
            "facts": [
                SemanticItem(id="exam-date", key="exam_date", statement="Database exam is in two weeks.", sourceType="user_confirmed", sourceRef="turn:1"),
                SemanticItem(id="daily-limit", key="daily_study_limit", statement="Can study at most 90 minutes daily.", sourceType="user_confirmed", sourceRef="turn:1"),
            ]
        }
    )
    core = ConstraintCompiler().compile(snapshot, today=date(2026, 8, 9)).core
    assert core.planning_horizon == "14 days"
    assert core.deadline == "2026-08-23"
    assert core.weekday_capacity_minutes == 90
    assert core.weekend_capacity_minutes == 90


@pytest.mark.parametrize(
    ("statement", "expected_minutes", "weekly_limit", "weekday_limit", "weekend_limit"),
    [
        ("每周最多学习 10 小时", 1080, 600, None, None),
        ("每天最多 1 小时", 420, None, 60, 60),
        ("工作日每天 1 小时，周末每天 2 小时", 630, None, 60, 120),
        ("一周最多 8 小时", 864, 480, None, None),
    ],
)
def test_natural_language_capacity_limits_generated_schedule(statement, expected_minutes, weekly_limit, weekday_limit, weekend_limit):
    snapshot = snapshot_with(statement)
    constraints = ConstraintCompiler().compile(snapshot)
    _, _, _, plan = make_plan(expected_minutes)
    plan = plan.model_copy(update={"constraint_ref": constraints.artifact_id})
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8))),
        calendar_snapshot_ref="calendar:7",
    )
    by_week: dict[tuple[int, int], int] = {}
    by_day: dict[str, int] = {}
    for session in schedule.sessions:
        started = datetime.fromisoformat(session.start)
        iso = started.isocalendar()
        by_week[(iso.year, iso.week)] = by_week.get((iso.year, iso.week), 0) + session.duration_minutes
        by_day[started.date().isoformat()] = by_day.get(started.date().isoformat(), 0) + session.duration_minutes
    if weekly_limit is not None:
        assert len(by_week) >= 2
        assert all(minutes <= weekly_limit for minutes in by_week.values())
    for day, minutes in by_day.items():
        limit = weekend_limit if datetime.fromisoformat(day).weekday() >= 5 else weekday_limit
        if limit is not None:
            assert minutes <= limit


def test_weekly_buffer_is_enforced_at_the_one_minute_boundary():
    snapshot = snapshot_with("每周最多学习 10 小时")
    constraints = ConstraintCompiler().compile(snapshot)
    _, _, _, plan = make_plan(540)
    plan = plan.model_copy(update={"constraint_ref": constraints.artifact_id})
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8))),
        calendar_snapshot_ref="calendar:7",
    )
    assert schedule.capacity_summary.available_minutes == 600
    assert schedule.capacity_summary.scheduled_minutes == 540
    assert schedule.capacity_summary.buffer_minutes == 60
    assert schedule.capacity_summary.scheduled_minutes + schedule.capacity_summary.buffer_minutes == 600
    last = schedule.sessions[-1]
    over = schedule.model_copy(update={
        "sessions": [*schedule.sessions[:-1], last.model_copy(update={
            "duration_minutes": last.duration_minutes + 1,
            "end": (datetime.fromisoformat(last.end) + timedelta(minutes=1)).isoformat(),
        })],
        "capacity_summary": schedule.capacity_summary.model_copy(update={"scheduled_minutes": 541}),
    })
    report = ScheduleValidator().validate(over, plan=plan, constraints=constraints, current_calendar_snapshot_ref="calendar:7")
    assert {"weekly_capacity", "minimum_buffer"}.issubset({issue.rule_id for issue in report.issues})


def make_plan(expected_minutes: int = 240) -> tuple[UnderstandingSnapshot, ConstraintSet, ContextPack, PlanBlueprint]:
    snapshot = snapshot_with("每天 4 小时")
    constraints = ConstraintCompiler().compile(snapshot)
    context = ContextPack(
        artifactId="context-invariants",
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
    )
    milestone = PlanMilestone(id="milestone-1", title="交付", purpose="完成", successSignalRefs=["signal-1"])
    task = PlanTask(
        id="task-1",
        milestoneId=milestone.id,
        title="完成项目",
        purpose="交付",
        whyNow="现在执行",
        actionSteps=["完成"],
        effortEstimate=EffortEstimate(minMinutes=expected_minutes, expectedMinutes=expected_minutes, maxMinutes=expected_minutes, estimationBasis="用户容量"),
        deliverable="项目",
        completionEvidence=["验收"],
        risks=["延期"],
        fallback="缩小范围",
        sourceGoalRefs=["signal-1"],
        sourceConstraintRefs=["constraint-0"],
    )
    return snapshot, constraints, context, PlanBlueprint(
        artifactId="plan-invariants",
        goalSummary=snapshot.goal_summary,
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
        contextRef=context.artifact_id,
        milestones=[milestone],
        tasks=[task],
    )


def test_schedule_reserves_buffer_inside_available_capacity():
    _, constraints, _, plan = make_plan(240)
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8))),
        calendar_snapshot_ref="calendar:7",
    )
    first_day = schedule.sessions[0].start[:10]
    scheduled = sum(item.duration_minutes for item in schedule.sessions if item.start[:10] == first_day)
    assert scheduled <= 216
    assert schedule.capacity_summary.scheduled_minutes + schedule.capacity_summary.buffer_minutes <= schedule.capacity_summary.available_minutes


def test_schedule_capacity_boundary_accepts_limit_and_rejects_one_minute_over():
    _, constraints, _, plan = make_plan(216)
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8))),
        calendar_snapshot_ref="calendar:7",
    )
    validator = ScheduleValidator()
    assert validator.validate(schedule, plan=plan, constraints=constraints, current_calendar_snapshot_ref="calendar:7").passed
    session = schedule.sessions[0]
    over = schedule.model_copy(update={
        "sessions": [session.model_copy(update={"duration_minutes": 217, "end": (datetime.fromisoformat(session.start) + timedelta(minutes=217)).isoformat()})],
        "capacity_summary": schedule.capacity_summary.model_copy(update={"scheduled_minutes": 217}),
    })
    report = validator.validate(over, plan=plan, constraints=constraints, current_calendar_snapshot_ref="calendar:7")
    assert "daily_capacity" in {issue.rule_id for issue in report.issues}


def test_semantic_review_required_must_be_completed_before_pass():
    report = QualityReport(
        targetArtifactId="plan-1",
        targetVersion=1,
        hardRulesPassed=True,
        semanticReviewRequired=True,
        semanticReviewCompleted=False,
        issues=[],
        score=100,
    )
    assert report.passed is False


def test_plan_validator_rejects_broken_referential_integrity():
    snapshot, constraints, context, plan = make_plan(60)
    broken = plan.model_copy(
        update={
            "milestones": [
                plan.milestones[0],
                plan.milestones[0].model_copy(),
            ],
            "tasks": [
                plan.tasks[0].model_copy(
                    update={
                        "milestone_id": "missing-milestone",
                        "dependencies": ["task-1"],
                        "source_goal_refs": ["missing-signal"],
                        "source_constraint_refs": ["missing-constraint"],
                    }
                )
            ],
        }
    )
    report = PlanHardValidator().validate(broken, snapshot=snapshot, constraints=constraints, context=context)
    assert {issue.rule_id for issue in report.issues} >= {
        "unique_milestone_id",
        "milestone_exists",
        "dependency_self",
        "goal_ref_exists",
        "constraint_ref_exists",
    }


@pytest.mark.parametrize(
    ("case", "expected_rule"),
    [
        ("duplicate_milestone", "unique_milestone_id"),
        ("missing_milestone", "milestone_exists"),
        ("missing_milestone_success_ref", "milestone_success_ref_exists"),
        ("missing_goal_ref", "goal_ref_exists"),
        ("missing_constraint_ref", "constraint_ref_exists"),
        ("self_dependency", "dependency_self"),
        ("missing_dependency", "dependency_exists"),
        ("duplicate_task_id", "unique_task_id"),
        ("duplicate_task_title", "duplicate_task"),
        ("fake_resource", "provenance"),
    ],
)
def test_plan_validator_rejects_each_referential_integrity_failure(case, expected_rule):
    snapshot, constraints, context, plan = make_plan(60)
    task = plan.tasks[0]
    if case == "duplicate_milestone":
        broken = plan.model_copy(update={"milestones": [*plan.milestones, plan.milestones[0].model_copy()]})
    elif case == "missing_milestone":
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"milestone_id": "missing"})]})
    elif case == "missing_milestone_success_ref":
        broken = plan.model_copy(update={"milestones": [plan.milestones[0].model_copy(update={"success_signal_refs": ["missing"]})]})
    elif case == "missing_goal_ref":
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"source_goal_refs": ["missing"]})]})
    elif case == "missing_constraint_ref":
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"source_constraint_refs": ["missing"]})]})
    elif case == "self_dependency":
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"dependencies": [task.id]})]})
    elif case == "missing_dependency":
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"dependencies": ["missing"]})]})
    elif case == "duplicate_task_id":
        broken = plan.model_copy(update={"tasks": [task, task.model_copy(update={"title": "Different title"})]})
    elif case == "duplicate_task_title":
        broken = plan.model_copy(update={"tasks": [task, task.model_copy(update={"id": "task-2"})]})
    else:
        broken = plan.model_copy(update={"tasks": [task.model_copy(update={"resource_refs": ["fake-id"]})]})
    report = PlanHardValidator().validate(broken, snapshot=snapshot, constraints=constraints, context=context)
    assert expected_rule in {issue.rule_id for issue in report.issues}


def test_non_requirement_does_not_create_impossible_task_coverage_rule():
    snapshot = snapshot_with("The project must be delivered", "Online deployment is not required")
    constraints = ConstraintCompiler().compile(snapshot)
    context = ContextPack(
        artifactId="context-permission",
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
    )
    milestone = PlanMilestone(id="milestone-1", title="Deliver", purpose="Complete", successSignalRefs=["signal-1"])
    task = PlanTask(
        id="task-1",
        milestoneId=milestone.id,
        title="Build the project",
        purpose="Deliver the project",
        whyNow="It is required",
        actionSteps=["Build", "Verify"],
        effortEstimate=EffortEstimate(minMinutes=60, expectedMinutes=90, maxMinutes=120, estimationBasis="bounded task"),
        deliverable="Working project",
        completionEvidence=["Acceptance check passes"],
        risks=["Implementation delay"],
        fallback="Reduce optional scope",
        sourceGoalRefs=["signal-1"],
        sourceConstraintRefs=["constraint-0"],
    )
    plan = PlanBlueprint(
        artifactId="plan-permission",
        goalSummary=snapshot.goal_summary,
        understandingRef=snapshot.artifact_id,
        constraintRef=constraints.artifact_id,
        contextRef=context.artifact_id,
        milestones=[milestone],
        tasks=[task],
    )

    report = PlanHardValidator().validate(plan, snapshot=snapshot, constraints=constraints, context=context)
    assert "immutable_constraint:constraint-1" not in {issue.issue_id for issue in report.issues}


def test_database_enforces_artifact_version_and_nonempty_source_key_uniqueness(client):
    with get_conn() as conn:
        indexes = {
            row["indexname"]: row["indexdef"]
            for row in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename IN ('planning_artifacts', 'plans')"
            )
        }
    assert "uq_planning_artifact_version" in indexes
    assert "ux_plans_source_key" in indexes


def test_planning_session_uses_backend_calendar_revision_instead_of_forged_context(client):
    persistence = PlanningPersistence()
    session_id = persistence.create(
        thread_id="thread-calendar-snapshot",
        user_input="创建计划",
        context={"calendarSnapshotRef": "forged", "calendarSnapshotVersion": 999, "timezone": "Asia/Shanghai"},
    )
    context = json_object(persistence.get_row(session_id)["request_context_json"])
    assert context["calendarSnapshotRef"] == "calendar:0"
    assert context["calendarSnapshotVersion"] == 0
    assert context["calendarBusy"] == []


def test_calendar_revision_storage_exists_and_increments_with_plan_mutation(client):
    with get_conn() as conn:
        before = conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()["revision"]
    created = client.post(
        "/api/plans",
        json={"date": "2026-08-10", "time": "09:00", "content": "Busy", "estimatedMinutes": 60},
    )
    assert created.status_code == 200
    with get_conn() as conn:
        after = conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()["revision"]
    assert after == before + 1


def test_api_key_is_not_stored_in_plaintext(client, monkeypatch):
    secret = "PLANIX_TEST_SECRET_12345_DO_NOT_PERSIST"
    response = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "apiKey": secret,
            "temperature": 0.3,
            "timeoutSeconds": 40,
        },
    )
    assert response.status_code == 200
    tables = (
        "ai_settings",
        "ai_provider_configs",
        "ai_runs",
        "user_preferences",
        "planning_sessions",
        "planning_artifacts",
        "agent_decisions",
        "agent_messages",
        "harness_states",
        "harness_events",
    )
    with get_conn() as conn:
        for table in tables:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            assert secret not in repr([dict(row) for row in rows])
    assert get_secret_store().get(provider_secret_key("deepseek")) == secret
    public = client.get("/api/ai/settings")
    assert public.status_code == 200
    assert public.json()["hasApiKey"] is True


def test_backend_tests_never_use_the_production_secret_store():
    assert isinstance(get_secret_store(), InMemorySecretStore)


def test_deleting_command_thread_cascades_its_planning_runtime_data(client):
    with get_conn() as conn:
        conn.execute("INSERT INTO command_threads(id, title) VALUES ('thread-delete', 'Delete me')")
        conn.execute("INSERT INTO command_messages(id, thread_id, role) VALUES ('message-delete', 'thread-delete', 'user')")
        conn.execute("INSERT INTO command_drafts(id, thread_id) VALUES ('draft-delete', 'thread-delete')")
        conn.execute(
            """INSERT INTO command_actions(id, thread_id, draft_id, target, operation, risk, status)
               VALUES ('action-delete', 'thread-delete', 'draft-delete', 'calendar', 'create_or_update_plans', 'write', 'waiting_approval')"""
        )
        conn.execute(
            """INSERT INTO command_approvals(id, thread_id, action_id, permission, decision)
               VALUES ('approval-delete', 'thread-delete', 'action-delete', 'low', 'approve')"""
        )
    session_id = PlanningPersistence().create(thread_id="thread-delete", user_input="目标", context={"timezone": "UTC"})
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO planning_artifacts(id, session_id, owner_agent, artifact_type, version)
               VALUES ('artifact-delete', %s, 'Understanding Agent', 'understanding_snapshot', 1)""",
            (session_id,),
        )
        conn.execute("INSERT INTO harness_states(session_id) VALUES (%s)", (session_id,))
        conn.execute(
            """INSERT INTO harness_events(id, session_id, sequence, checkpoint_version, event_type)
               VALUES ('event-delete', %s, 1, 1, 'created')""",
            (session_id,),
        )
    CommandAgentService().delete_thread("thread-delete")
    with get_conn() as conn:
        checks = {
            "command_threads": ("id", "thread-delete"),
            "command_messages": ("thread_id", "thread-delete"),
            "command_actions": ("thread_id", "thread-delete"),
            "command_approvals": ("thread_id", "thread-delete"),
            "command_drafts": ("thread_id", "thread-delete"),
            "planning_sessions": ("id", session_id),
            "planning_artifacts": ("session_id", session_id),
            "harness_states": ("session_id", session_id),
            "harness_events": ("session_id", session_id),
        }
        for table, (column, value) in checks.items():
            assert conn.execute(f"SELECT 1 FROM {table} WHERE {column} = %s", (value,)).fetchone() is None


def test_artifact_versions_are_unique_under_concurrent_writers(client):
    session_id = PlanningPersistence().create(thread_id="artifact-race", user_input="Goal", context={"timezone": "UTC"})
    store = PlanningArtifactAuditStore()

    def write(index: int):
        return store.record_artifact(
            session_id,
            owner_agent="Understanding Agent",
            artifact_type="understanding_patch",
            content={"index": index},
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        artifacts = list(pool.map(write, range(12)))
    assert sorted(item.version for item in artifacts) == list(range(1, 13))


def test_planning_session_update_supports_compare_and_set(client):
    persistence = PlanningPersistence()
    session_id = persistence.create(thread_id="session-cas", user_input="Goal", context={"timezone": "UTC"})
    version = int(persistence.get_row(session_id)["version"])
    persistence.update(session_id, runtime_status="idle", expected_version=version)
    with pytest.raises(ValueError, match="version changed"):
        persistence.update(session_id, runtime_status="running", expected_version=version)


def test_calendar_batch_rolls_back_all_rows_on_error(client):
    with get_conn() as conn:
        revision = int(conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()["revision"])
    with pytest.raises(ValueError, match="requires title"):
        upsert_calendar_plans(
            [
                {"title": "Valid first row", "sourceKey": "batch:first", "date": "2026-08-10", "time": "09:00"},
                {"title": "", "sourceKey": "batch:invalid", "date": "2026-08-10", "time": "10:00"},
            ],
            expected_revision=revision,
        )
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM plans WHERE source_key = 'batch:first'").fetchone() is None
        assert int(conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()["revision"]) == revision


def test_calendar_action_claim_is_atomic(client):
    with get_conn() as conn:
        conn.execute("INSERT INTO command_threads(id, title) VALUES ('claim-thread', 'Claim')")
        conn.execute("INSERT INTO command_drafts(id, thread_id) VALUES ('claim-draft', 'claim-thread')")
        conn.execute(
            """INSERT INTO command_actions(id, thread_id, draft_id, target, operation, risk, status)
               VALUES ('claim-action', 'claim-thread', 'claim-draft', 'calendar', 'create_or_update_plans', 'write', 'waiting_approval')"""
        )
    service = CommandAgentService()

    def claim() -> bool:
        try:
            service._claim_calendar_action("claim-action")
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]


def test_schedule_repair_is_issue_scoped_and_preserves_effort():
    _, constraints, _, plan = make_plan(120)
    schedule = ScheduleGenerator().generate(
        plan,
        constraints,
        start=datetime(2026, 8, 10, 9, tzinfo=timezone(timedelta(hours=8))),
        calendar_snapshot_ref="calendar:7",
    )
    original = schedule.sessions[0]
    issue = QualityIssue(
        issueId="schedule:calendar_conflict:session",
        category="schedule",
        severity="major",
        ruleId="calendar_conflict",
        targetType="schedule",
        targetId=original.id,
        description="Session overlaps busy time",
        evidenceRefs=["calendar:7"],
        allowedOperations=["move_schedule_session"],
        repairBasis="deterministic_schedule_validator",
    )
    repaired = SchedulePatchGuard().apply(
        schedule,
        issue,
        constraints=constraints,
        calendar_busy=[(datetime.fromisoformat(original.start), datetime.fromisoformat(original.end))],
    )
    assert repaired.sessions[0].id == original.id
    assert repaired.sessions[0].start != original.start
    assert sum(item.duration_minutes for item in repaired.sessions) == 120
    assert ScheduleValidator().validate(
        repaired,
        plan=plan,
        constraints=constraints,
        current_calendar_snapshot_ref="calendar:7",
        calendar_busy=[(datetime.fromisoformat(original.start), datetime.fromisoformat(original.end))],
    ).passed


def test_schedule_repair_budget_survives_runtime_reconstruction_and_is_capped(client):
    persistence = PlanningPersistence()
    session_id = persistence.create(thread_id="schedule-repair-budget", user_input="Goal", context={"timezone": "UTC"})
    persistence.update(session_id, schedule_repair_count=1)
    reconstructed = CognitiveOSRuntime()._state_from_row(PlanningPersistence().get_row(session_id), action="continue_current_stage")
    assert reconstructed["schedule_repair_count"] == 1
    persistence.update(session_id, schedule_repair_count=3)
    reconstructed = CognitiveOSRuntime()._state_from_row(PlanningPersistence().get_row(session_id), action="continue_current_stage")
    assert reconstructed["schedule_repair_count"] == 2
