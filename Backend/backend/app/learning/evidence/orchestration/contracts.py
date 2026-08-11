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
    "FAILED",
]


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


__all__ = [
    "GapCompletionResult",
    "GapCompletionRun",
    "GapCompletionStatus",
    "GapCompletionTermination",
]
