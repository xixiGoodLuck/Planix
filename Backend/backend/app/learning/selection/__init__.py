"""Deterministic minimum-sufficient content selection for Planix Learning."""

from .services import (
    ContentSelector,
    CoverageAnalyzer,
    PlanComposer,
    RedundancyAnalyzer,
)
from .validators import ContentSelectionValidator
from ..selection_semantics import (
    marginal_duration_seconds,
    range_union_duration_seconds,
    resolve_selected_knowledge_coverage,
)

__all__ = [
    "ContentSelectionValidator",
    "ContentSelector",
    "CoverageAnalyzer",
    "PlanComposer",
    "RedundancyAnalyzer",
    "marginal_duration_seconds",
    "range_union_duration_seconds",
    "resolve_selected_knowledge_coverage",
]
