from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import EvidenceGraph, LearningArtifactRef, LearningContract
from ..coverage import CoverageReport, EvidenceCoverageGap


GapCompletionStatus = Literal["RUNNING", "COMPLETED", "INCOMPLETE", "FAILED"]
GapCompletionTermination = Literal[
    "REQUIRED_COVERAGE_FULL",
    "NO_COVERAGE_IMPROVEMENT",
    "MAX_ROUNDS_REACHED",
    "NO_EXECUTABLE_GAP",
    "BUDGET_EXHAUSTED",
    "FAILED",
]


class GapCompletionBudget(LearningContract):
    max_rounds: int = Field(default=3, ge=1, le=10)
    max_retrieval_plans: int = Field(default=12, ge=1, le=100)
    max_candidates: int = Field(default=24, ge=1, le=200)
    max_transcript_acquisitions: int = Field(default=12, ge=1, le=100)
    max_model_mapping_calls: int = Field(default=8, ge=1, le=50)


class GapCompletionRun(LearningContract):
    run_id: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    status: GapCompletionStatus
    before_report: CoverageReport
    after_report: CoverageReport
    resolved_gaps: list[EvidenceCoverageGap] = Field(default_factory=list)
    remaining_gaps: list[EvidenceCoverageGap] = Field(default_factory=list)
    termination_reason: GapCompletionTermination | None = None
    error: str | None = None


class GapCompletionResult(LearningContract):
    run_id: str = Field(min_length=1)
    status: GapCompletionStatus
    max_rounds: int = Field(ge=1, le=10)
    initial_graph_ref: LearningArtifactRef
    initial_report: CoverageReport
    final_graph: EvidenceGraph
    final_report: CoverageReport
    rounds: list[GapCompletionRun] = Field(default_factory=list)
    termination_reason: GapCompletionTermination
    error: str | None = None
    budget: GapCompletionBudget = Field(default_factory=GapCompletionBudget)
    retrieval_plan_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    transcript_acquisition_count: int = Field(default=0, ge=0)
    model_mapping_call_count: int = Field(default=0, ge=0)
    searched_queries: list[str] = Field(default_factory=list)
    searched_resource_refs: list[str] = Field(default_factory=list)
    transcript_unavailable_resource_refs: list[str] = Field(default_factory=list)


__all__ = [
    "GapCompletionResult",
    "GapCompletionRun",
    "GapCompletionBudget",
    "GapCompletionStatus",
    "GapCompletionTermination",
]
