from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import LearningContract


MappingCoverageType = Literal[
    "introduction",
    "explanation",
    "demonstration",
    "implementation",
    "comparison",
]


class SemanticCoverageMapping(LearningContract):
    """LLM-owned semantics; source identity is deliberately absent."""

    knowledge_id: str = Field(min_length=1)
    coverage_type: MappingCoverageType
    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    supported_requirement_indexes: list[int] = Field(default_factory=list)


class SegmentCoverageMappings(LearningContract):
    """Mappings for the segment at this response-list position."""

    mappings: list[SemanticCoverageMapping] = Field(default_factory=list, max_length=40)


class CoverageMappingResponse(LearningContract):
    segments: list[SegmentCoverageMappings] = Field(min_length=1, max_length=80)


__all__ = [
    "CoverageMappingResponse",
    "MappingCoverageType",
    "SemanticCoverageMapping",
    "SegmentCoverageMappings",
]
