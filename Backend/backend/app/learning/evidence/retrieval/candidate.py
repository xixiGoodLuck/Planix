from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ...contracts import LearningContract, VideoProvider
from .contracts import RetrievalGapPlan


class RetrievalRequest(LearningContract):
    retrieval_plan_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)

    @classmethod
    def from_plan(
        cls,
        plan: RetrievalGapPlan,
        *,
        query: str | None = None,
    ) -> "RetrievalRequest":
        if not isinstance(plan, RetrievalGapPlan):
            raise ValueError("RetrievalRequest must be created from RetrievalGapPlan")
        selected_query = query or plan.query_hints[0]
        if selected_query not in plan.query_hints:
            raise ValueError("retrieval query must be one of the plan query hints")
        return cls(
            retrievalPlanId=plan.retrieval_plan_id,
            knowledgeId=plan.knowledge_id,
            query=selected_query,
            constraints=list(plan.constraints),
        )


class CandidateRetrievalSource(LearningContract):
    retrieval_plan_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    query: str = Field(min_length=1)


class EvidenceCandidate(LearningContract):
    """Provider metadata candidate. This contract is deliberately not EvidenceGraph-compatible."""

    candidate_id: str = Field(min_length=1)
    provider: VideoProvider
    external_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    duration_seconds: int = Field(ge=1)
    content_fingerprint: str = Field(min_length=1)
    technology_versions: dict[str, str] = Field(default_factory=dict)
    retrieval_source: CandidateRetrievalSource
    status: Literal["candidate"] = "candidate"


CandidatePayload = dict[str, Any]


__all__ = [
    "CandidatePayload",
    "CandidateRetrievalSource",
    "EvidenceCandidate",
    "RetrievalRequest",
]
