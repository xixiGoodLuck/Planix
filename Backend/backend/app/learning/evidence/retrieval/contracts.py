from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import LearningContract


RetrievalGapType = Literal[
    "MISSING_EVIDENCE",
    "WEAK_EVIDENCE",
    "PARTIAL_COVERAGE",
    "VERSION_CONFLICT",
    "INSUFFICIENT_CONTEXT",
]
RetrievalPriority = Literal["HIGH", "MEDIUM", "LOW"]
RetrievalEvidenceLevel = Literal[
    "verified_transcript",
    "updated_source_metadata",
    "additional_context_transcript",
]


class RetrievalGapPlan(LearningContract):
    retrieval_plan_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    gap_type: RetrievalGapType
    priority: RetrievalPriority
    reason: str = Field(min_length=1)
    required_evidence_level: RetrievalEvidenceLevel
    query_hints: list[str] = Field(min_length=1, max_length=8)
    constraints: list[str] = Field(min_length=1)


__all__ = [
    "RetrievalEvidenceLevel",
    "RetrievalGapPlan",
    "RetrievalGapType",
    "RetrievalPriority",
]
