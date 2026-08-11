from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import LearningArtifactRef, LearningContract


CoverageStrength = Literal["FULL", "PARTIAL", "WEAK", "MISSING"]
CoverageStatus = Literal["sufficient", "insufficient", "missing"]


class KnowledgeCoverageResult(LearningContract):
    knowledge_id: str = Field(min_length=1)
    status: CoverageStatus
    coverage_strength: CoverageStrength
    evidence_refs: list[str] = Field(default_factory=list)
    segment_refs: list[str] = Field(default_factory=list)


class EvidenceCoverageGap(LearningContract):
    knowledge_id: str = Field(min_length=1)
    gap_type: Literal["missing_knowledge", "weak_coverage", "unsupported_required"]
    current_strength: CoverageStrength
    reason: str = Field(min_length=1)


class SegmentCoverageAnalysis(LearningContract):
    knowledge_id: str = Field(min_length=1)
    classification: Literal["REDUNDANT", "COMPLEMENTARY", "CONTEXT_REQUIRED"]
    segment_refs: list[str] = Field(min_length=2)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class VersionObservation(LearningContract):
    version: str = Field(min_length=1)
    resource_refs: list[str] = Field(min_length=1)
    segment_refs: list[str] = Field(min_length=1)


class VersionConflict(LearningContract):
    knowledge_id: str = Field(min_length=1)
    technology: str = Field(min_length=1)
    observations: list[VersionObservation] = Field(min_length=2)
    reason: str = Field(min_length=1)


class CoverageReport(LearningContract):
    knowledge_graph_ref: LearningArtifactRef
    evidence_graph_ref: LearningArtifactRef
    knowledge_coverage: list[KnowledgeCoverageResult] = Field(min_length=1)
    gaps: list[EvidenceCoverageGap] = Field(default_factory=list)
    conflicts: list[VersionConflict] = Field(default_factory=list)
    redundancy: list[SegmentCoverageAnalysis] = Field(default_factory=list)


__all__ = [
    "CoverageReport",
    "CoverageStatus",
    "CoverageStrength",
    "EvidenceCoverageGap",
    "KnowledgeCoverageResult",
    "SegmentCoverageAnalysis",
    "VersionConflict",
    "VersionObservation",
]
