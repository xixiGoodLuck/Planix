from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.cognitive_planning.agents import AgentResult, PlanningModelUnavailable
from app.cognitive_planning.contracts import (
    EffortEstimate,
    LearningObservation,
    PlanBlueprint,
    PlanMilestone,
    PlanTask,
    QualityIssue,
    QualityReport,
    RepairOperation,
    RepairProposal,
    SafePlanningError,
    SemanticItem,
    SemanticReviewResult,
    UnderstandingReadiness,
    UnderstandingSnapshot,
    UserModelMemoryDraft,
)
from app.cognitive_planning.memory import UserModelMemoryRepository
from app.cognitive_planning.runtime import CognitiveOSRuntime
from app.db import get_conn
from app.schemas import CreatePlanningSessionRequest, PlanningExecutionFeedbackRequest, PlanningSessionTextRequest


def _quality_issue(severity: str) -> QualityIssue:
    return QualityIssue(
        issueId=f"{severity}-issue",
        category="content",
        severity=severity,
        ruleId="semantic_fit",
        targetType="plan",
        targetId="plan-1",
        description=f"A {severity} semantic issue.",
        evidenceRefs=["understanding-1"],
        allowedOperations=["update_task"],
        repairBasis="semantic_review",
    )


def test_quality_pass_is_owned_only_by_hard_rules_and_issue_severity():
    base = {"targetArtifactId": "plan-1", "targetVersion": 1}

    assert QualityReport(**base, hardRulesPassed=True, score=None).passed is True
    assert QualityReport(**base, hardRulesPassed=True, score=20).passed is True
    assert QualityReport(**base, hardRulesPassed=True, score=100, issues=[_quality_issue("major")]).passed is False
    assert QualityReport(**base, hardRulesPassed=True, score=100, issues=[_quality_issue("blocker")]).passed is False
    assert QualityReport(**base, hardRulesPassed=True, score=20, issues=[_quality_issue("minor")]).passed is True
    assert QualityReport(**base, hardRulesPassed=False, score=100).passed is False


class UnavailableModel:
    def complete_contract(self, *, stage, **_kwargs):
        raise PlanningModelUnavailable(
            stage,
            SafePlanningError(
                stage=stage,
                errorType="provider_unavailable",
                message="Provider unavailable",
                retryable=True,
            ),
        )


def test_model_failure_stops_graph_at_native_node_without_fake_artifact(client):
    runtime = CognitiveOSRuntime(model_client=UnavailableModel())
    session = runtime.create_session(CreatePlanningSessionRequest(threadId="model-block", userInput="Build a demo"))
    assert session.status == "MODEL_UNAVAILABLE"
    assert session.runtime_status == "blocked_model"
    assert session.model_failure.resume_node == "understanding"
    assert session.artifacts == []


class DeterministicV2Model:
    calls: list[str]

    def __init__(self):
        self.calls = []

    def complete_contract(self, *, stage, contract_type, **_kwargs):
        self.calls.append(stage)
        if contract_type is UnderstandingSnapshot:
            artifact = UnderstandingSnapshot(
                goalSummary="Build a small portfolio project",
                constraints=[
                    SemanticItem(
                        id="capacity-1",
                        key="weekly_capacity",
                        statement="10 hours per week",
                        sourceType="user_confirmed",
                        sourceRef="turn:1",
                        mutationPolicy="immutable",
                    )
                ],
                successSignals=[
                    SemanticItem(
                        id="success-1",
                        key="portfolio_demo",
                        statement="A working demo is available",
                        sourceType="user_confirmed",
                        sourceRef="turn:1",
                        mutationPolicy="immutable",
                    )
                ],
                readiness=UnderstandingReadiness(readyForConfirmation=True),
                sourceRefs=["turn:1"],
            )
        elif contract_type is PlanBlueprint:
            artifact = PlanBlueprint(
                goalSummary="placeholder",
                understandingRef="placeholder",
                constraintRef="placeholder",
                contextRef="placeholder",
                milestones=[
                    PlanMilestone(
                        id="milestone-1",
                        title="Deliver demo",
                        purpose="Produce the requested outcome",
                        successSignalRefs=["success-1"],
                    )
                ],
                tasks=[
                    PlanTask(
                        id="task-1",
                        milestoneId="milestone-1",
                        title="Build and verify the demo",
                        purpose="Create the portfolio evidence",
                        whyNow="It is the shortest path to the goal",
                        actionSteps=["Implement the slice", "Run the acceptance check"],
                        effortEstimate=EffortEstimate(
                            minMinutes=60,
                            expectedMinutes=90,
                            maxMinutes=120,
                            estimationBasis="One bounded implementation slice",
                        ),
                        deliverable="Working demo",
                        completionEvidence=["Acceptance check passes"],
                        risks=["The implementation slice may take longer than estimated"],
                        fallback="Reduce the slice while preserving the demo",
                        sourceGoalRefs=["success-1"],
                        sourceConstraintRefs=["capacity-1"],
                    )
                ],
            )
        elif contract_type is SemanticReviewResult:
            artifact = SemanticReviewResult(
                targetArtifactId="placeholder",
                targetVersion=1,
                issues=[],
                score=40,
                remainingRisks=["Diagnostic score is intentionally below the retired threshold."],
            )
        elif contract_type is RepairProposal:
            issue = _kwargs["payload"]["currentIssue"]
            plan = _kwargs["payload"]["currentPlan"]
            artifact = RepairProposal(
                artifactId=plan["artifactId"],
                artifactVersion=plan["version"],
                issueId=issue["issueId"],
                operations=[
                    RepairOperation(
                        operation="update_task",
                        targetId="task-1",
                        payload={"title": "Build the concise demo"},
                    )
                ],
            )
        elif contract_type is LearningObservation:
            artifact = LearningObservation(
                sessionId="placeholder",
                category="execution_feedback",
                statement="The completed task took the reported amount of time.",
                sourceRefs=["placeholder"],
            )
        else:  # pragma: no cover - protects the single-runtime contract
            raise AssertionError(f"unexpected contract {contract_type.__name__}")
        task_type = {
            "understanding": "planning_understanding",
            "generate_plan": "planning_plan",
            "semantic_review": "planning_review",
            "repair_plan": "planning_plan",
            "record_learning": "planning_learning",
        }[stage]
        return AgentResult(
            artifact=artifact,
            model_usage={"provider": "test", "model": "v2", "mode": "llm", "taskType": task_type},
        )


def test_native_runtime_runs_direct_plan_flow_and_score_is_diagnostic(client):
    model = DeterministicV2Model()
    runtime = CognitiveOSRuntime(model_client=model)

    started = runtime.create_session(
        CreatePlanningSessionRequest(threadId="v2-thread", userInput="Build a portfolio demo")
    )
    assert started.status == "waiting_understanding_confirmation"

    planned = runtime.confirm_understanding(started.session_id)
    assert planned.status == "waiting_final_review"
    assert planned.plan_quality_report["passed"] is True
    assert planned.plan_quality_report["score"] == 40
    assert model.calls == ["understanding", "generate_plan", "semantic_review"]

    artifact_types = {item.artifact_type for item in planned.artifacts}
    assert {
        "understanding_snapshot",
        "constraint_set",
        "context_pack",
        "plan_blueprint",
        "plan_quality_report",
        "schedule_blueprint",
        "schedule_quality_report",
        "calendar_proposal",
    }.issubset(artifact_types)
    assert not artifact_types.intersection(
        {
            "user_goal_model",
            "goal_completion",
            "reality_assessment",
            "evidence_pack",
            "strategy_portfolio",
            "execution_blueprint",
            "critique_report",
        }
    )

    approved = runtime.approve_final(started.session_id)
    assert approved.status == "waiting_calendar_write_approval"
    final_artifact = max(
        (item for item in approved.artifacts if item.artifact_type == "final_approval_bundle"),
        key=lambda item: item.version,
    )
    final_ref = {
        "id": final_artifact.id,
        "sessionId": final_artifact.session_id,
        "kind": final_artifact.artifact_type,
        "version": final_artifact.version,
        "owner": final_artifact.owner_agent,
        "status": final_artifact.status,
    }
    runtime.approve_calendar_write(started.session_id, final_approval_ref=final_ref)
    runtime.assert_calendar_write_allowed(started.session_id, final_approval_ref=final_ref)
    runtime.mark_calendar_written(started.session_id, final_approval_ref=final_ref)
    assert runtime.get_session(started.session_id).status == "written_to_calendar"


def test_rejected_repair_consumes_budget_without_blocking_harness(client):
    class RejectedRepairModel(DeterministicV2Model):
        def complete_contract(self, *, stage, contract_type, **kwargs):
            if contract_type is SemanticReviewResult:
                self.calls.append(stage)
                plan_id = kwargs["payload"]["currentPlan"]["artifactId"]
                issue = _quality_issue("major").model_copy(
                    update={"target_id": plan_id, "evidence_refs": [plan_id]}
                )
                return AgentResult(
                    artifact=SemanticReviewResult(
                        targetArtifactId="placeholder",
                        targetVersion=1,
                        issues=[issue],
                    ),
                    model_usage={"provider": "test", "model": "v2", "mode": "llm", "taskType": "planning_review"},
                )
            if contract_type is RepairProposal:
                self.calls.append(stage)
                issue = kwargs["payload"]["currentIssue"]
                plan = kwargs["payload"]["currentPlan"]
                return AgentResult(
                    artifact=RepairProposal(
                        artifactId=plan["artifactId"],
                        artifactVersion=plan["version"],
                        issueId=issue["issueId"],
                        operations=[RepairOperation(operation="update_task", targetId="missing-task", payload={"title": "Invalid"})],
                    ),
                    model_usage={"provider": "test", "model": "v2", "mode": "llm", "taskType": "planning_plan"},
                )
            return super().complete_contract(stage=stage, contract_type=contract_type, **kwargs)

    runtime = CognitiveOSRuntime(model_client=RejectedRepairModel())
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="rejected-repair", userInput="Build a portfolio demo"))
    result = runtime.confirm_understanding(started.session_id)

    assert result.status == "final_revision"
    assert result.runtime_status == "idle"
    assert result.cognitive_metadata.repair_count == 2
    with get_conn() as conn:
        harness = conn.execute(
            "SELECT lifecycle, errors_json FROM harness_states WHERE session_id = ?",
            (started.session_id,),
        ).fetchone()
    assert harness["lifecycle"] == "waiting"
    assert harness["errors_json"] == "[]"


def test_execution_feedback_uses_learning_route_and_keeps_outcome_evidence(client):
    model = DeterministicV2Model()
    runtime = CognitiveOSRuntime(model_client=model)
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="learning-thread", userInput="Build a portfolio demo"))
    runtime.confirm_understanding(started.session_id)

    response = runtime.record_execution_feedback(
        started.session_id,
        PlanningExecutionFeedbackRequest(
            taskId="task-1",
            status="completed",
            actualMinutes=95,
            completionEvidence=["Acceptance check passed"],
        ),
    )

    observation = response.learning_observation
    assert model.calls[-1] == "record_learning"
    assert observation["status"] == "completed"
    assert observation["actualMinutes"] == 95
    assert observation["completionEvidence"] == ["Acceptance check passed"]
    assert observation["executionOutcomeRef"].startswith("artifact:")
    assert observation["domainScope"] == ["build a small portfolio project"]


def test_unrelated_domain_memory_does_not_pollute_context_pack(client):
    repository = UserModelMemoryRepository()
    repository.upsert(
        UserModelMemoryDraft(
            category="preference",
            statement="Prefer red-eye flights for travel",
            domainScope=["travel planning"],
            evidence="Observed in a travel execution outcome",
            confidence=0.8,
        )
    )
    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model(), user_model=repository)
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="memory-domain", userInput="Build a portfolio demo"))
    planned = runtime.confirm_understanding(started.session_id)
    assert all("red-eye" not in claim["claim"] for claim in planned.context_pack["claims"])


def test_memory_evaluator_failure_is_fail_closed(client):
    class FailingEvaluator:
        def evaluate(self, _candidate):
            raise RuntimeError("evaluation unavailable")

    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model())
    runtime.harness.memory_evaluator = FailingEvaluator()
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="memory-fail", userInput="Build a portfolio demo"))
    runtime.confirm_understanding(started.session_id)
    response = runtime.record_execution_feedback(
        started.session_id,
        PlanningExecutionFeedbackRequest(taskId="task-1", status="failed", failureReason="Dependency unavailable"),
    )
    assert response.learning_observation["failureReason"] == "Dependency unavailable"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM user_model_memories").fetchone()["count"] == 0


def _approved_runtime() -> tuple[CognitiveOSRuntime, str, dict]:
    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model())
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="approval-thread", userInput="Build a portfolio demo"))
    runtime.confirm_understanding(started.session_id)
    approved = runtime.approve_final(started.session_id)
    artifact = max(
        (item for item in approved.artifacts if item.artifact_type == "final_approval_bundle"),
        key=lambda item: item.version,
    )
    return runtime, started.session_id, {
        "id": artifact.id,
        "sessionId": artifact.session_id,
        "kind": artifact.artifact_type,
        "version": artifact.version,
        "owner": artifact.owner_agent,
        "status": artifact.status,
    }


def test_unrelated_audit_event_does_not_stale_final_approval(client):
    runtime, session_id, final_ref = _approved_runtime()
    runtime.agent_runtime.record_message(
        session_id,
        from_agent="Plan Generator",
        to_agent="Plan Quality Reviewer",
        message_type="handoff",
        reason="Unrelated audit event",
    )
    runtime.approve_calendar_write(session_id, final_approval_ref=final_ref)


@pytest.mark.parametrize(
    ("artifact_type", "owner", "response_field"),
    [
        ("plan_blueprint", "Plan Generator", "plan_blueprint"),
        ("plan_quality_report", "Plan Quality Reviewer", "plan_quality_report"),
        ("schedule_blueprint", "Schedule Agent", "schedule_blueprint"),
        ("schedule_quality_report", "Schedule Quality Reviewer", "schedule_quality_report"),
    ],
)
def test_protected_artifact_change_stales_final_approval(client, artifact_type, owner, response_field):
    runtime, session_id, final_ref = _approved_runtime()
    current = getattr(runtime.get_session(session_id), response_field)
    runtime.agent_runtime.record_artifact(
        session_id,
        owner_agent=owner,
        artifact_type=artifact_type,
        content=current,
        status="approved",
    )
    with pytest.raises(HTTPException) as exc:
        runtime.approve_calendar_write(session_id, final_approval_ref=final_ref)
    assert exc.value.status_code == 409


def test_calendar_revision_change_stales_final_approval(client):
    runtime, session_id, final_ref = _approved_runtime()
    response = client.post(
        "/api/plans",
        json={"date": "2026-08-11", "time": "09:00", "content": "External calendar change", "estimatedMinutes": 30},
    )
    assert response.status_code == 200
    with pytest.raises(HTTPException) as exc:
        runtime.approve_calendar_write(session_id, final_approval_ref=final_ref)
    assert exc.value.status_code == 409


def test_consumed_final_approval_cannot_be_replayed(client):
    runtime, session_id, final_ref = _approved_runtime()
    runtime.approve_calendar_write(session_id, final_approval_ref=final_ref)
    runtime.mark_calendar_written(session_id, final_approval_ref=final_ref)
    with pytest.raises((HTTPException, ValueError)):
        runtime.approve_calendar_write(session_id, final_approval_ref=final_ref)


def test_fresh_schema_keeps_session_lifecycle_separate_from_artifacts(client):
    with get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(planning_sessions)")}
        shadow_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'planning_shadow_runs'"
        ).fetchone()
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "id",
        "thread_id",
        "status",
        "business_status",
        "runtime_status",
        "conversation_history_json",
        "request_context_json",
        "cognitive_metadata_json",
    }.issubset(columns)
    assert not columns.intersection(
        {
            "goal_model_json",
            "goal_completion_json",
            "reality_assessment_json",
            "evidence_pack_json",
            "strategy_portfolio_json",
            "execution_blueprint_json",
            "critique_report_json",
        }
    )
    assert shadow_table is None
    assert not tables.intersection({"agent_runs", "agent_events", "planning_goals", "daily_reviews"})


def test_final_review_plan_feedback_creates_a_real_version_bound_revision(client):
    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model())
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="revision-thread", userInput="Build a portfolio demo"))
    planned = runtime.confirm_understanding(started.session_id)
    before = planned.plan_blueprint

    revised = runtime.revise_final(
        started.session_id,
        PlanningSessionTextRequest(text="把任务内容改为精简演示，但其他内容不要改"),
    )

    assert revised.plan_blueprint["artifactId"] != before["artifactId"]
    assert revised.plan_blueprint["version"] == before["version"] + 1
    assert revised.plan_blueprint["tasks"][0]["title"] == "Build the concise demo"
    assert revised.status == "waiting_final_review"


def test_final_review_schedule_feedback_changes_only_schedule_and_downstream(client):
    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model())
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="schedule-revision-thread", userInput="Build a portfolio demo"))
    planned = runtime.confirm_understanding(started.session_id)

    revised = runtime.revise_final(
        started.session_id,
        PlanningSessionTextRequest(text="周一不要安排任务，周末多安排一些，计划内容不要变"),
    )

    assert revised.plan_blueprint == planned.plan_blueprint
    assert revised.schedule_blueprint["artifactId"] != planned.schedule_blueprint["artifactId"]
    assert revised.schedule_blueprint["version"] == planned.schedule_blueprint["version"] + 1
    assert all(datetime.fromisoformat(item["start"]).weekday() != 0 for item in revised.schedule_blueprint["sessions"])
    assert revised.calendar_proposal["artifactId"] != planned.calendar_proposal["artifactId"]


def test_final_review_presentation_feedback_does_not_change_plan_or_schedule(client):
    runtime = CognitiveOSRuntime(model_client=DeterministicV2Model())
    started = runtime.create_session(CreatePlanningSessionRequest(threadId="presentation-revision-thread", userInput="Build a portfolio demo"))
    planned = runtime.confirm_understanding(started.session_id)

    revised = runtime.revise_final(
        started.session_id,
        PlanningSessionTextRequest(text="把日历标题改成更简短，但不要改计划和排期"),
    )

    assert revised.plan_blueprint == planned.plan_blueprint
    assert revised.schedule_blueprint == planned.schedule_blueprint
    assert revised.calendar_proposal["artifactId"] != planned.calendar_proposal["artifactId"]
    assert revised.calendar_proposal["events"][0]["title"] != planned.calendar_proposal["events"][0]["title"]
