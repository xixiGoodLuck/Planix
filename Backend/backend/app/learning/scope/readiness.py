from __future__ import annotations

from pydantic import Field

from ..contracts import LearningContract, LearningScope


class LearningScopeReadiness(LearningContract):
    ready_for_planning: bool
    high_impact_gap_count: int = Field(ge=0)
    recommendation_round: int = Field(ge=0, le=2)
    auto_continue_reason: str


def evaluate_readiness(scope: LearningScope) -> LearningScopeReadiness:
    high_impact = [item for item in scope.unknowns if item.impact == "high"]
    goal_missing = any("user_goal" in item.affected_fields for item in scope.unknowns)
    recommendation_round = min(scope.version, 2)
    recommendation_limit_reached = scope.version > 2
    ready = not goal_missing and (not high_impact or recommendation_limit_reached)
    if goal_missing:
        reason = "learning_goal_unresolved"
    elif not high_impact:
        reason = "scope_has_no_high_impact_gaps"
    elif recommendation_limit_reached:
        reason = "recommendation_round_limit_reached"
    else:
        reason = "high_impact_gaps_remain"
    return LearningScopeReadiness(
        readyForPlanning=ready,
        highImpactGapCount=len(high_impact),
        recommendationRound=recommendation_round,
        autoContinueReason=reason,
    )


__all__ = ["LearningScopeReadiness", "evaluate_readiness"]
