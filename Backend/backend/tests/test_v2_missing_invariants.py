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
from app.services.command_agent import CommandAgentService
from app.services.plans import upsert_calendar_plans
from app.services.secret_store import InMemorySecretStore, get_secret_store


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
        artifact_indexes = conn.execute("PRAGMA index_list(planning_artifacts)").fetchall()
        plan_indexes = conn.execute("PRAGMA index_list(plans)").fetchall()
    assert any(row["unique"] and "version" in row["name"] for row in artifact_indexes)
    assert any(row["unique"] and "source_key" in row["name"] for row in plan_indexes)


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
    secret = "PLANIX_TEST_SECRET_12345"
    monkeypatch.setattr("app.services.ai_settings._validate_provider_config", lambda *_args, **_kwargs: None)
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
    with get_conn() as conn:
        for table in ("ai_settings", "ai_provider_configs", "ai_runs", "planning_artifacts", "agent_decisions", "agent_messages"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            assert secret not in repr([dict(row) for row in rows])


def test_backend_tests_never_use_the_production_secret_store():
    assert isinstance(get_secret_store(), InMemorySecretStore)


def test_deleting_command_thread_cascades_its_planning_runtime_data(client):
    with get_conn() as conn:
        conn.execute("INSERT INTO command_threads(id, title) VALUES ('thread-delete', 'Delete me')")
    session_id = PlanningPersistence().create(thread_id="thread-delete", user_input="目标", context={"timezone": "UTC"})
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO planning_artifacts(id, session_id, owner_agent, artifact_type, version)
               VALUES ('artifact-delete', ?, 'Understanding Agent', 'understanding_snapshot', 1)""",
            (session_id,),
        )
    CommandAgentService().delete_thread("thread-delete")
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM planning_sessions WHERE id = ?", (session_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM planning_artifacts WHERE session_id = ?", (session_id,)).fetchone() is None


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
