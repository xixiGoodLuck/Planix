"""Deterministic evidence coverage intelligence for Planix Learning."""

from .conflict_analyzer import ConflictAnalysisResult, ConflictAnalyzer
from .coverage_aggregator import CoverageAggregator
from .coverage_report import (
    CoverageReport,
    CoverageStatus,
    CoverageStrength,
    EvidenceCoverageGap,
    KnowledgeCoverageResult,
    SegmentCoverageAnalysis,
    VersionConflict,
    VersionObservation,
)
from .gap_analyzer import GapAnalyzer
from .validators import CoverageReportValidationError, CoverageReportValidator

__all__ = [
    "ConflictAnalysisResult",
    "ConflictAnalyzer",
    "CoverageAggregator",
    "CoverageReport",
    "CoverageReportValidationError",
    "CoverageReportValidator",
    "CoverageStatus",
    "CoverageStrength",
    "EvidenceCoverageGap",
    "GapAnalyzer",
    "KnowledgeCoverageResult",
    "SegmentCoverageAnalysis",
    "VersionConflict",
    "VersionObservation",
]
