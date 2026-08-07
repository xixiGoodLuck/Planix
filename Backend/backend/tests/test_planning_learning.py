from __future__ import annotations

import pytest

from app.cognitive_planning.planning import (
    EffortEstimate,
    ExecutionFeedbackService,
    LearningObservation,
    MemoryGateway,
    PlanTask,
    PromotionPolicy,
    UserAdaptation,
    UserAdaptationService,
)


def task() -> PlanTask:
    return PlanTask(
        id="task-feedback",
        milestoneId="milestone-1",
        title="完成验证",
        purpose="获得实际反馈",
        whyNow="验证计划",
        actionSteps=["执行"],
        effortEstimate=EffortEstimate(
            minMinutes=30,
            expectedMinutes=60,
            maxMinutes=90,
            confidence=0.6,
            estimationBasis="初始估计",
        ),
        deliverable="结果",
        completionEvidence=["结果记录"],
        risks=["阻塞"],
        fallback="记录阻塞并进入最终审阅",
    )


def observations(count: int):
    return [
        LearningObservation(
            id=f"observation-{index}",
            sessionId="session-1",
            category="duration",
            statement="实际耗时持续高于估计",
            sourceRefs=[f"outcome-{index}"],
        )
        for index in range(count)
    ]


def test_calendar_time_passing_does_not_mark_task_completed():
    outcome = ExecutionFeedbackService().record(task=task(), status="not_started")
    assert outcome.status == "not_started"
    assert outcome.completed_at is None


def test_completed_outcome_requires_real_evidence():
    with pytest.raises(ValueError, match="completion evidence"):
        ExecutionFeedbackService().record(task=task(), status="completed")
    outcome = ExecutionFeedbackService().record(
        task=task(),
        status="completed",
        actual_minutes=75,
        completion_evidence=["commit:abc123"],
    )
    assert outcome.completion_evidence == ["commit:abc123"]


def test_execution_deviation_creates_review_required_replan_not_calendar_mutation():
    service = ExecutionFeedbackService()
    outcome = service.record(
        task=task(),
        status="blocked",
        blocker_reason="依赖服务不可用",
    )
    proposal = service.propose_replan(session_id="session-1", outcomes=[outcome])
    assert proposal is not None
    assert proposal.requires_final_review is True
    assert proposal.affected_task_ids == ["task-feedback"]
    assert proposal.proposed_operations == []


def test_user_adaptation_requires_three_consistent_non_outlier_observations():
    service = UserAdaptationService()
    current = UserAdaptation()
    assert service.update_duration(current, observations(2), ratios=[1.4, 1.5]) == current
    updated = service.update_duration(current, observations(3), ratios=[1.4, 1.5, 8])
    assert updated == current
    updated = service.update_duration(current, observations(3), ratios=[1.4, 1.5, 1.6])
    assert updated.version == 2
    assert updated.duration_multiplier > 1
    assert len(updated.observation_refs) == 3


def test_memory_gateway_only_proposes_candidate_and_does_not_write():
    candidate = MemoryGateway.propose_candidate(
        statement="用户通常需要更长的实现时间",
        category="planning_hypothesis",
        source_refs=["outcome-1", "outcome-2", "outcome-3"],
        confidence=0.7,
        proposed_scope=["software"],
    )
    assert candidate.evidence_count == 3
    assert candidate.proposed_scope == ["software"]


def test_system_learning_keeps_safety_and_approval_changes_behind_human_release():
    policy = PromotionPolicy()
    low_risk = policy.audit(
        runtime_version="cognitive-os-v2",
        change_type="duration_estimate",
        previous_value="1.0",
        proposed_value="1.2",
        observation_refs=["o1", "o2", "o3"],
    )
    assert low_risk.allowed_for_automatic_promotion is True
    safety = policy.audit(
        runtime_version="cognitive-os-v2",
        change_type="approval_rule",
        previous_value="final_review_required",
        proposed_value="auto_write",
        observation_refs=["o1", "o2", "o3", "o4"],
    )
    assert safety.allowed_for_automatic_promotion is False
    assert safety.requires_human_release is True
    assert safety.rollback_value == "final_review_required"
