from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import CognitiveContract
from .goal_model import UserGoalModel


EVIDENCE_AUTHORITY_POLICY_VERSION = "current_goal_authority_v1"


class MemoryDocument(CognitiveContract):
    id: str
    kind: str
    title: str
    summary: str
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    context_role: Literal["supporting_context", "historical_context"] = "supporting_context"
    source: str | None = None
    source_id: str | None = None
    source_key: str | None = None
    metadata: dict = Field(default_factory=dict)

    def provenance_ids(self) -> set[str]:
        return {
            str(value)
            for value in (self.id, self.source_id, self.source_key)
            if value and str(value).strip()
        }


class CalendarConstraint(CognitiveContract):
    date: str
    statement: str
    source_id: str | None = None


class ResearchPolicy(CognitiveContract):
    allow_web_research: bool = False
    require_user_approval: bool = True
    freshness_required: bool = False
    source_types_allowed: list[str] = Field(default_factory=list)


class EvidenceAuthorityPolicy(CognitiveContract):
    """Typed precedence rules sent to every Evidence model invocation."""

    current_goal_is_authoritative: bool = True
    authoritative_goal_fields: list[str] = Field(
        default_factory=lambda: [
            "goalStatement",
            "desiredChange",
            "knownFacts",
            "hardConstraints",
            "softPreferences",
            "assumptions",
            "successModel",
        ]
    )
    historical_conflict_handling: Literal["superseded_context"] = "superseded_context"
    blocking_unknown_priorities: list[Literal["blocking"]] = Field(default_factory=lambda: ["blocking"])
    non_blocking_unknown_priorities: list[Literal["important", "optional"]] = Field(
        default_factory=lambda: ["important", "optional"]
    )
    non_blocking_unknown_handling: Literal["assumption_or_strategy_branch"] = "assumption_or_strategy_branch"
    new_evidence_blocking_impacts: list[Literal["safety", "feasibility"]] = Field(
        default_factory=lambda: ["safety", "feasibility"]
    )


class EvidenceInput(CognitiveContract):
    goal_model: UserGoalModel
    memory_query_context: str
    user_model_memories: list[dict] = Field(default_factory=list)
    existing_materials: list[MemoryDocument] = Field(default_factory=list)
    calendar_constraints: list[CalendarConstraint] = Field(default_factory=list)
    resource_candidates: list[dict] = Field(default_factory=list)
    planning_hypotheses: list[dict] = Field(default_factory=list)
    research_policy: ResearchPolicy = Field(default_factory=ResearchPolicy)
    authority_policy: EvidenceAuthorityPolicy = Field(default_factory=EvidenceAuthorityPolicy)


class UserEvidence(CognitiveContract):
    source_id: str | None = None
    kind: str
    statement: str
    why_relevant: str
    confidence: float = Field(ge=0, le=1)
    source_context: Literal[
        "current_goal",
        "supporting_context",
        "new_evidence",
        "historical_context",
        "unspecified",
    ] = "unspecified"


class EvidencePlanningRule(CognitiveContract):
    rule: str
    strength: Literal["hard", "soft"]
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    source_ids: list[str] = Field(default_factory=list)
    source_context: Literal[
        "current_goal",
        "supporting_context",
        "new_evidence",
        "historical_context",
        "unspecified",
    ] = "unspecified"


class DomainEvidence(CognitiveContract):
    claim: str
    source_type: str
    source_ref: str | None = None
    relevance: str
    freshness: str | None = None
    credibility: float = Field(ge=0, le=1)
    source_context: Literal[
        "current_goal",
        "supporting_context",
        "new_evidence",
        "historical_context",
        "model_knowledge",
        "unspecified",
    ] = "unspecified"


class EvidenceResourceNeed(CognitiveContract):
    purpose: str
    ideal_resource_type: str
    selection_criteria: list[str] = Field(default_factory=list)


class EvidenceResourceCandidate(CognitiveContract):
    title: str
    type: str
    source_ref: str | None = None
    how_it_helps: str
    user_fit: str
    limitations: list[str] = Field(default_factory=list)
    credibility: float = Field(ge=0, le=1)
    source_context: Literal[
        "current_goal",
        "supporting_context",
        "new_evidence",
        "historical_context",
        "model_knowledge",
        "unspecified",
    ] = "unspecified"


class CalendarReality(CognitiveContract):
    available_windows: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    load_warnings: list[str] = Field(default_factory=list)


class EvidenceGap(CognitiveContract):
    description: str
    consequence: str
    proposed_resolution: Literal[
        "ask_user",
        "web_research",
        "make_explicit_assumption",
        "reduce_scope",
    ]
    blocking_basis: Literal["goal_unknown", "new_evidence"] | None = None
    impact: Literal[
        "strategy",
        "safety",
        "feasibility",
        "schedule",
        "resources",
        "success_criteria",
    ] | None = None
    related_goal_unknown_key: str | None = None
    source_context: Literal[
        "current_goal",
        "new_evidence",
        "historical_context",
        "unspecified",
    ] = "unspecified"
    supporting_source_ids: list[str] = Field(default_factory=list)


class EvidencePack(CognitiveContract):
    user_evidence: list[UserEvidence] = Field(default_factory=list)
    planning_rules: list[EvidencePlanningRule] = Field(default_factory=list)
    domain_evidence: list[DomainEvidence] = Field(default_factory=list)
    resource_needs: list[EvidenceResourceNeed] = Field(default_factory=list)
    resource_candidates: list[EvidenceResourceCandidate] = Field(default_factory=list)
    calendar_reality: CalendarReality = Field(default_factory=CalendarReality)
    gaps: list[EvidenceGap] = Field(default_factory=list)
    superseded_context: list[str] = Field(default_factory=list)
    synthesis: str
    confidence: float = Field(ge=0, le=1)
    can_proceed_to_strategy: bool
    authority_policy_version: str | None = None

    @property
    def is_authority_normalized(self) -> bool:
        return self.authority_policy_version == EVIDENCE_AUTHORITY_POLICY_VERSION

    def decision_view(self) -> "EvidencePack":
        """Return the evidence artifact without audit-only superseded context."""

        if not self.is_authority_normalized:
            raise ValueError("legacy Evidence must be authority-normalized before downstream use")
        return self.model_copy(update={"superseded_context": []})

    def model_input_view(self) -> dict:
        """Serialize only evidence that is allowed to influence downstream decisions."""

        return self.decision_view().model_dump(by_alias=True, exclude={"superseded_context"})


def apply_evidence_authority_policy(
    goal_model: UserGoalModel,
    evidence: EvidencePack,
    policy: EvidenceAuthorityPolicy,
    *,
    historical_source_ids: set[str] | None = None,
    valid_evidence_source_ids: set[str] | None = None,
) -> EvidencePack:
    """Prevent Evidence from reopening a completed Goal without an auditable blocker."""

    historical_source_ids = {str(item) for item in (historical_source_ids or set()) if str(item)}
    valid_evidence_source_ids = {
        str(item) for item in (valid_evidence_source_ids or set()) if str(item)
    }
    valid_evidence_source_ids.update(
        f"current_goal:known_fact:{item.key}" for item in goal_model.known_facts
    )
    valid_evidence_source_ids.update(
        f"current_goal:hard_constraint:{index}"
        for index, _item in enumerate(goal_model.hard_constraints)
    )
    valid_evidence_source_ids.update(
        f"current_goal:soft_preference:{index}"
        for index, _item in enumerate(goal_model.soft_preferences)
    )
    valid_evidence_source_ids.update(
        f"current_goal:assumption:{index}"
        for index, _item in enumerate(goal_model.assumptions)
    )
    unknowns = {item.key: item for item in goal_model.decision_relevant_unknowns}
    blocking_priorities = set(policy.blocking_unknown_priorities)
    has_goal_blocker = any(item.priority in blocking_priorities for item in unknowns.values())
    has_new_evidence_blocker = False
    has_unclassified_blocker = False
    normalized_gaps: list[EvidenceGap] = []
    superseded_context = list(evidence.superseded_context)

    for gap in evidence.gaps:
        supporting_source_ids = {
            str(item) for item in gap.supporting_source_ids if str(item)
        }
        if (
            gap.source_context == "historical_context"
            or (supporting_source_ids and supporting_source_ids & historical_source_ids)
            or (supporting_source_ids and not supporting_source_ids <= valid_evidence_source_ids)
            or (
                (gap.blocking_basis == "new_evidence" or gap.source_context == "new_evidence")
                and not supporting_source_ids
            )
        ):
            if gap.description not in superseded_context:
                superseded_context.append(gap.description)
            continue

        related_unknown = unknowns.get(gap.related_goal_unknown_key or "")
        if gap.blocking_basis == "goal_unknown" and related_unknown is None:
            if gap.description not in superseded_context:
                superseded_context.append(gap.description)
            continue
        authorized_goal_blocker = bool(
            gap.blocking_basis == "goal_unknown"
            and related_unknown is not None
            and related_unknown.priority in blocking_priorities
        )
        authorized_new_evidence_blocker = bool(
            gap.blocking_basis == "new_evidence"
            and gap.source_context == "new_evidence"
            and gap.impact in policy.new_evidence_blocking_impacts
            and supporting_source_ids
            and not (supporting_source_ids & historical_source_ids)
            and supporting_source_ids <= valid_evidence_source_ids
        )
        has_new_evidence_blocker = has_new_evidence_blocker or authorized_new_evidence_blocker

        proven_non_blocking_goal_unknown = bool(
            gap.blocking_basis == "goal_unknown"
            and related_unknown is not None
            and related_unknown.priority in policy.non_blocking_unknown_priorities
        )
        if (
            not evidence.can_proceed_to_strategy
            and not authorized_goal_blocker
            and not authorized_new_evidence_blocker
            and not proven_non_blocking_goal_unknown
        ):
            has_unclassified_blocker = True

        if gap.proposed_resolution == "ask_user" and not (
            authorized_goal_blocker or authorized_new_evidence_blocker
        ):
            gap = gap.model_copy(
                update={
                    "proposed_resolution": "make_explicit_assumption",
                    "blocking_basis": None,
                }
            )
        normalized_gaps.append(gap)

    if not evidence.can_proceed_to_strategy and not evidence.gaps:
        has_unclassified_blocker = True

    canonical_user_evidence = [
        UserEvidence(
            sourceId=f"current_goal:known_fact:{item.key}",
            kind="current_goal_fact",
            statement=item.statement,
            whyRelevant="Authoritative fact in the current Goal",
            confidence=item.confidence,
            sourceContext="current_goal",
        )
        for item in goal_model.known_facts
    ]
    canonical_user_evidence.extend(
        UserEvidence(
            sourceId=f"current_goal:hard_constraint:{index}",
            kind="current_goal_constraint",
            statement=item.statement,
            whyRelevant="Authoritative hard constraint in the current Goal",
            confidence=1,
            sourceContext="current_goal",
        )
        for index, item in enumerate(goal_model.hard_constraints)
    )
    canonical_user_evidence.extend(
        UserEvidence(
            sourceId=f"current_goal:soft_preference:{index}",
            kind="current_goal_preference",
            statement=item.statement,
            whyRelevant="Authoritative preference in the current Goal",
            confidence=item.confidence,
            sourceContext="current_goal",
        )
        for index, item in enumerate(goal_model.soft_preferences)
    )
    canonical_user_evidence.extend(
        UserEvidence(
            sourceId=f"current_goal:assumption:{index}",
            kind="current_goal_assumption",
            statement=item.statement,
            whyRelevant="Explicit assumption in the current Goal",
            confidence=item.confidence,
            sourceContext="current_goal",
        )
        for index, item in enumerate(goal_model.assumptions)
    )

    sanitized_user_evidence: list[UserEvidence] = []
    for item in evidence.user_evidence:
        source_id = str(item.source_id or "")
        if item.source_context == "historical_context" or source_id in historical_source_ids:
            if item.statement not in superseded_context:
                superseded_context.append(item.statement)
            continue
        if item.source_context == "current_goal":
            continue
        if item.source_context not in {"supporting_context", "new_evidence"}:
            continue
        if not source_id or source_id not in valid_evidence_source_ids:
            continue
        sanitized_user_evidence.append(item)

    canonical_rules = [
        EvidencePlanningRule(
            rule=item.statement,
            strength="hard",
            evidence=["current Goal hard constraint"],
            confidence=1,
            sourceIds=[f"current_goal:hard_constraint:{index}"],
            sourceContext="current_goal",
        )
        for index, item in enumerate(goal_model.hard_constraints)
    ]
    sanitized_rules: list[EvidencePlanningRule] = []
    for item in evidence.planning_rules:
        source_ids = {str(source_id) for source_id in item.source_ids if str(source_id)}
        if item.source_context == "historical_context" or source_ids & historical_source_ids:
            if item.rule not in superseded_context:
                superseded_context.append(item.rule)
            continue
        if item.source_context == "current_goal":
            pass
        elif item.source_context not in {"supporting_context", "new_evidence"}:
            continue
        if not source_ids or not source_ids <= valid_evidence_source_ids:
            continue
        sanitized_rules.append(item)

    sanitized_domain_evidence: list[DomainEvidence] = []
    for item in evidence.domain_evidence:
        source_ref = str(item.source_ref or "")
        if item.source_context == "historical_context" or source_ref in historical_source_ids:
            if item.claim not in superseded_context:
                superseded_context.append(item.claim)
            continue
        if item.source_context == "model_knowledge":
            if source_ref:
                continue
            sanitized_domain_evidence.append(item)
            continue
        if item.source_context not in {"current_goal", "supporting_context", "new_evidence"}:
            continue
        if not source_ref or source_ref not in valid_evidence_source_ids:
            continue
        sanitized_domain_evidence.append(item)

    sanitized_resource_candidates: list[EvidenceResourceCandidate] = []
    for item in evidence.resource_candidates:
        source_ref = str(item.source_ref or "")
        if item.source_context == "historical_context" or source_ref in historical_source_ids:
            if item.title not in superseded_context:
                superseded_context.append(item.title)
            continue
        if item.source_context not in {"current_goal", "supporting_context", "new_evidence"}:
            continue
        if not source_ref or source_ref not in valid_evidence_source_ids:
            continue
        sanitized_resource_candidates.append(item)

    sanitized = bool(
        historical_source_ids
        or superseded_context
        or len(sanitized_user_evidence) != len(evidence.user_evidence)
        or len(sanitized_rules) != len(evidence.planning_rules)
        or len(sanitized_domain_evidence) != len(evidence.domain_evidence)
        or len(sanitized_resource_candidates) != len(evidence.resource_candidates)
    )
    synthesis = evidence.synthesis
    if sanitized:
        synthesis = (
            f"Evidence was normalized against the authoritative current Goal: {goal_model.goal_statement}. "
            f"{len(superseded_context)} superseded historical item(s) were excluded; "
            f"{len(sanitized_user_evidence) + len(canonical_user_evidence)} user evidence item(s) and "
            f"{len(sanitized_rules) + len(canonical_rules)} planning rule(s) remain for strategy design."
        )

    return evidence.model_copy(
        update={
            "user_evidence": [*canonical_user_evidence, *sanitized_user_evidence],
            "planning_rules": [*canonical_rules, *sanitized_rules],
            "domain_evidence": sanitized_domain_evidence,
            "resource_candidates": sanitized_resource_candidates,
            "gaps": normalized_gaps,
            "superseded_context": superseded_context,
            "synthesis": synthesis,
            "can_proceed_to_strategy": not (
                has_goal_blocker or has_new_evidence_blocker or has_unclassified_blocker
            ),
            "authority_policy_version": EVIDENCE_AUTHORITY_POLICY_VERSION,
        }
    )
