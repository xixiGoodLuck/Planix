from __future__ import annotations

from app.cognitive_planning.agents import AgentResult
from app.cognitive_planning.contracts import (
    EffortEstimate,
    PlanBlueprint,
    PlanMilestone,
    PlanTask,
    QualityIssue,
    QualityReport,
    SemanticItem,
    SemanticReviewResult,
    UnderstandingReadiness,
    UnderstandingSnapshot,
)
from app.cognitive_planning.runtime import CognitiveOSRuntime
from app.db import get_conn
from app.schemas import CreatePlanningSessionRequest


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
        else:  # pragma: no cover - protects the single-runtime contract
            raise AssertionError(f"unexpected contract {contract_type.__name__}")
        task_type = {
            "understanding": "planning_understanding",
            "generate_plan": "planning_plan",
            "semantic_review": "planning_review",
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
