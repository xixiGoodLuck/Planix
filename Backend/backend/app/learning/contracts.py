from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _artifact_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LearningContract(BaseModel):
    """Strict, camel-case compatible base for the isolated Learning domain."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        validate_assignment=True,
        extra="forbid",
    )


LearningArtifactType = Literal[
    "learning_scope",
    "capability_graph",
    "knowledge_graph",
    "evidence_graph",
    "content_selection",
    "learning_content_plan",
    "learning_quality_report",
]
Importance = Literal["required", "important", "optional"]


class LearningArtifactRef(LearningContract):
    artifact_type: LearningArtifactType
    artifact_id: str = Field(min_length=1)
    version: int = Field(ge=1)


class LearningArtifact(LearningContract):
    artifact_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    schema_version: Literal[1] = 1
    created_at: str = Field(default_factory=_utc_now)


class CurrentLevel(LearningContract):
    summary: str = ""
    known_skills: list[str] = Field(default_factory=list)
    known_technologies: list[str] = Field(default_factory=list)
    uncertain_areas: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class ContentBudget(LearningContract):
    target_total_minutes: int | None = Field(default=None, ge=1)
    maximum_total_minutes: int | None = Field(default=None, ge=1)
    maximum_video_count: int | None = Field(default=None, ge=1)
    maximum_segment_minutes: int | None = Field(default=None, ge=1)


class LanguagePreference(LearningContract):
    preferred_languages: list[str] = Field(default_factory=list)
    acceptable_languages: list[str] = Field(default_factory=list)
    subtitles_acceptable: bool = True


class ResourcePreference(LearningContract):
    preferred_platforms: list[str] = Field(default_factory=list)
    excluded_platforms: list[str] = Field(default_factory=list)
    preferred_styles: list[
        Literal["conceptual", "hands_on", "project_based", "lecture", "short_form"]
    ] = Field(default_factory=list)
    free_only: bool | None = None
    user_supplied_urls: list[str] = Field(default_factory=list)


class LearningAssumption(LearningContract):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    impact: Literal["low", "medium", "high"] = "medium"


class LearningUnknown(LearningContract):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    blocking: bool = False
    affected_fields: list[str] = Field(default_factory=list)


class LearningScope(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("learning-scope"))
    user_goal: str = Field(min_length=1)
    target_result: str = Field(min_length=1)
    current_level: CurrentLevel = Field(default_factory=CurrentLevel)
    content_budget: ContentBudget = Field(default_factory=ContentBudget)
    language_preference: LanguagePreference = Field(default_factory=LanguagePreference)
    resource_preference: ResourcePreference = Field(default_factory=ResourcePreference)
    assumptions: list[LearningAssumption] = Field(default_factory=list)
    unknowns: list[LearningUnknown] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    confirmed: bool = False


class LearningOutcome(LearningContract):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    importance: Importance = "required"
    source_goal_refs: list[str] = Field(min_length=1)


class CapabilityNode(LearningContract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    outcome_refs: list[str] = Field(min_length=1)
    importance: Importance = "required"


class CapabilityEdge(LearningContract):
    source_capability_id: str = Field(min_length=1)
    target_capability_id: str = Field(min_length=1)
    relation: Literal["prerequisite", "supports"]


class CapabilityGraph(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("capability-graph"))
    scope_ref: LearningArtifactRef
    outcomes: list[LearningOutcome] = Field(min_length=1)
    capabilities: list[CapabilityNode] = Field(min_length=1)
    edges: list[CapabilityEdge] = Field(default_factory=list)


class KnowledgeNode(LearningContract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    capability_refs: list[str] = Field(min_length=1)
    outcome_refs: list[str] = Field(min_length=1)
    importance: Importance
    mastery_indicators: list[str] = Field(min_length=1)


class KnowledgeEdge(LearningContract):
    source_knowledge_id: str = Field(min_length=1)
    target_knowledge_id: str = Field(min_length=1)
    relation: Literal["prerequisite", "supports", "part_of", "optional_extension"]
    reason: str = Field(min_length=1)


class KnowledgeGraph(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("knowledge-graph"))
    scope_ref: LearningArtifactRef
    capability_graph_ref: LearningArtifactRef
    nodes: list[KnowledgeNode] = Field(min_length=1)
    edges: list[KnowledgeEdge] = Field(default_factory=list)


VideoProvider = Literal["fixture", "user_url", "youtube", "bilibili", "other"]


class VideoResource(LearningContract):
    id: str = Field(min_length=1)
    provider: VideoProvider
    external_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    language: str = ""
    technology_versions: dict[str, str] = Field(default_factory=dict)
    duration_seconds: int = Field(ge=1)
    published_at: str | None = None
    observed_at: str = Field(default_factory=_utc_now)
    content_fingerprint: str = Field(min_length=1)
    availability: Literal["available", "unavailable", "restricted", "unknown"] = "available"


class ContentSegment(LearningContract):
    """Canonical evidence segment; raw provider transcripts may carry source timestamps."""

    id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    start_seconds: int = Field(ge=0)
    end_seconds: int = Field(gt=0)
    content_summary: str = Field(min_length=1)
    topics: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)
    context_segment_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_range(self) -> "ContentSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end must be after segment start")
        return self


class EvidenceSourceRange(LearningContract):
    """Text/caption offsets, never video timestamps."""

    locator_type: Literal["transcript_chars", "caption_cues", "chapter_text", "manual_note"]
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered_range(self) -> "EvidenceSourceRange":
        if self.end_offset <= self.start_offset:
            raise ValueError("evidence source range end must be after start")
        return self


class SegmentEvidence(LearningContract):
    id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    kind: Literal[
        "transcript_span", "caption_span", "chapter_marker", "provider_metadata", "manual_verified"
    ]
    supported_claim: str = Field(min_length=1)
    source_range: EvidenceSourceRange
    source_excerpt: str | None = None
    verification_status: Literal["verified", "unverified", "rejected"]
    observed_at: str = Field(default_factory=_utc_now)


class CoverageEdge(LearningContract):
    id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    coverage_type: Literal[
        "introduction",
        "explanation",
        "demonstration",
        "implementation",
        "comparison",
        "practice",
        "review",
    ]
    coverage_strength: Literal["full", "partial", "supplementary"]
    confidence: float = Field(ge=0, le=1)
    summary: str = ""
    reason: str = Field(min_length=1)


class EvidenceGraph(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("evidence-graph"))
    knowledge_graph_ref: LearningArtifactRef
    resources: list[VideoResource] = Field(default_factory=list)
    segments: list[ContentSegment] = Field(default_factory=list)
    evidence: list[SegmentEvidence] = Field(default_factory=list)
    coverage_edges: list[CoverageEdge] = Field(default_factory=list)


class AlternativeRejection(LearningContract):
    segment_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    reason: Literal[
        "duplicate_content",
        "longer_duration",
        "weaker_evidence",
        "more_source_switches",
        "version_incompatible",
    ]


class SelectionFacts(LearningContract):
    knowledge_covered: list[str] = Field(default_factory=list)
    evidence_level: Literal["transcript", "caption", "chapter", "manual", "metadata"]
    saved_minutes: int = Field(default=0, ge=0)
    version_compatible: bool
    alternative_rejected: list[AlternativeRejection] = Field(default_factory=list)
    selection_rule_refs: list[str] = Field(default_factory=list)


class SelectedSegment(LearningContract):
    id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    knowledge_refs: list[str] = Field(min_length=1)
    coverage_edge_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    viewing_order: int = Field(ge=0)
    selection_reason: str = Field(min_length=1)
    selection_facts: SelectionFacts | None = None


class CoverageGap(LearningContract):
    knowledge_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    impact: Literal["blocker", "major", "minor"]
    searched_resource_refs: list[str] = Field(default_factory=list)


class ContentSelection(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("content-selection"))
    scope_ref: LearningArtifactRef
    knowledge_graph_ref: LearningArtifactRef
    evidence_graph_ref: LearningArtifactRef
    selected_segments: list[SelectedSegment] = Field(default_factory=list)
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    total_duration_seconds: int = Field(default=0, ge=0)


class RecommendedContent(LearningContract):
    selection_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    video_title: str = Field(min_length=1)
    segment_summary: str = Field(min_length=1)
    duration_seconds: int = Field(ge=1)
    recommendation_reason: str = Field(min_length=1)
    selection_facts: SelectionFacts | None = None


class LearningContentItem(LearningContract):
    knowledge_id: str = Field(min_length=1)
    knowledge_name: str = Field(min_length=1)
    knowledge_explanation: str = Field(min_length=1)
    why_required: str = Field(min_length=1)
    recommended_content: list[RecommendedContent] = Field(default_factory=list)
    uncovered_reason: str | None = None


class LearningContentPlan(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("learning-content-plan"))
    scope_ref: LearningArtifactRef
    knowledge_graph_ref: LearningArtifactRef
    evidence_graph_ref: LearningArtifactRef
    content_selection_ref: LearningArtifactRef
    items: list[LearningContentItem] = Field(min_length=1)
    total_duration_seconds: int = Field(default=0, ge=0)
    evidence_gaps: list[CoverageGap] = Field(default_factory=list)


LearningQualityRule = Literal[
    "knowledge_coverage",
    "evidence_validity",
    "version_compatibility",
    "content_redundancy",
    "unsupported_timestamp",
]


class LearningQualityIssue(LearningContract):
    issue_id: str = Field(min_length=1)
    rule: LearningQualityRule
    severity: Literal["blocker", "major", "minor"]
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)


class LearningQualityCheck(LearningContract):
    rule: LearningQualityRule
    passed: bool
    evidence: list[str] = Field(default_factory=list)


class LearningQualityReport(LearningArtifact):
    artifact_id: str = Field(default_factory=lambda: _artifact_id("learning-quality"))
    target_ref: LearningArtifactRef
    scope_ref: LearningArtifactRef
    capability_graph_ref: LearningArtifactRef
    knowledge_graph_ref: LearningArtifactRef
    evidence_graph_ref: LearningArtifactRef
    content_selection_ref: LearningArtifactRef
    hard_rules_passed: bool
    quality_checks: list[LearningQualityCheck] = Field(default_factory=list)
    issues: list[LearningQualityIssue] = Field(default_factory=list)
    remaining_gaps: list[CoverageGap] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=100)

    @computed_field
    @property
    def passed(self) -> bool:
        return bool(
            self.hard_rules_passed
            and all(check.passed for check in self.quality_checks)
            and not any(issue.severity in {"blocker", "major"} for issue in self.issues)
        )


__all__ = [
    "AlternativeRejection",
    "CapabilityEdge",
    "CapabilityGraph",
    "CapabilityNode",
    "ContentBudget",
    "ContentSegment",
    "ContentSelection",
    "CoverageEdge",
    "CoverageGap",
    "CurrentLevel",
    "EvidenceGraph",
    "EvidenceSourceRange",
    "KnowledgeEdge",
    "KnowledgeGraph",
    "KnowledgeNode",
    "LanguagePreference",
    "LearningArtifactRef",
    "LearningAssumption",
    "LearningContentItem",
    "LearningContentPlan",
    "LearningOutcome",
    "LearningQualityCheck",
    "LearningQualityIssue",
    "LearningQualityReport",
    "LearningQualityRule",
    "LearningScope",
    "LearningUnknown",
    "RecommendedContent",
    "ResourcePreference",
    "SegmentEvidence",
    "SelectionFacts",
    "SelectedSegment",
    "VideoResource",
]
