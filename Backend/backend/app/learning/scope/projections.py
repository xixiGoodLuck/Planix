from __future__ import annotations

from pydantic import Field

from ..contracts import LearningAssumption, LearningContract, LearningScope, LearningUnknown
from .readiness import LearningScopeReadiness, evaluate_readiness


class LearningKnownInformation(LearningContract):
    field: str = Field(min_length=1)
    values: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class LearningScopeReview(LearningContract):
    known_information: list[LearningKnownInformation] = Field(default_factory=list)
    recommended_gaps: list[LearningUnknown] = Field(default_factory=list, max_length=6)
    assumptions: list[LearningAssumption] = Field(default_factory=list)
    ready_for_planning: bool
    high_impact_gap_count: int = Field(ge=0)
    recommendation_round: int = Field(ge=0, le=2)
    auto_continue_reason: str


def project_scope_review(
    scope: LearningScope,
    readiness: LearningScopeReadiness | None = None,
) -> LearningScopeReview:
    readiness = readiness or evaluate_readiness(scope)
    unknown_fields = {
        field
        for item in scope.unknowns
        for field in item.affected_fields
    }
    known: list[LearningKnownInformation] = []
    if "user_goal" not in unknown_fields:
        known.append(
            LearningKnownInformation(
                field="user_goal",
                values=[scope.user_goal],
                sourceRefs=scope.source_refs[:1],
            )
        )
    if "target_result" not in unknown_fields:
        known.append(
            LearningKnownInformation(
                field="target_result",
                values=[scope.target_result],
                sourceRefs=list(scope.source_refs),
            )
        )
    if scope.current_level.source_refs:
        values = [scope.current_level.summary] if scope.current_level.summary else []
        values.extend(scope.current_level.known_skills)
        values.extend(scope.current_level.known_technologies)
        known.append(
            LearningKnownInformation(
                field="current_level",
                values=list(dict.fromkeys(value for value in values if value)),
                sourceRefs=list(scope.current_level.source_refs),
            )
        )
    if scope.content_budget.target_total_minutes is not None:
        known.append(
            LearningKnownInformation(
                field="content_budget",
                values=[str(scope.content_budget.target_total_minutes)],
                sourceRefs=list(scope.source_refs),
            )
        )
    if scope.language_preference.preferred_languages:
        known.append(
            LearningKnownInformation(
                field="language_preference",
                values=list(scope.language_preference.preferred_languages),
                sourceRefs=list(scope.source_refs),
            )
        )
    resource_values = [
        *scope.resource_preference.preferred_platforms,
        *scope.resource_preference.preferred_styles,
    ]
    if resource_values:
        known.append(
            LearningKnownInformation(
                field="resource_preference",
                values=list(dict.fromkeys(resource_values)),
                sourceRefs=list(scope.source_refs),
            )
        )
    if scope.resource_preference.user_supplied_urls:
        known.append(
            LearningKnownInformation(
                field="user_supplied_urls",
                values=list(scope.resource_preference.user_supplied_urls),
                sourceRefs=list(scope.source_refs),
            )
        )
    gaps = (
        []
        if readiness.ready_for_planning or scope.version > 2
        else list(scope.unknowns[:6])
    )
    return LearningScopeReview(
        knownInformation=known,
        recommendedGaps=gaps,
        assumptions=list(scope.assumptions),
        readyForPlanning=readiness.ready_for_planning,
        highImpactGapCount=readiness.high_impact_gap_count,
        recommendationRound=readiness.recommendation_round,
        autoContinueReason=readiness.auto_continue_reason,
    )


__all__ = ["LearningKnownInformation", "LearningScopeReview", "project_scope_review"]
