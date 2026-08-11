"""Semantic mapping from verified content evidence to knowledge coverage."""

from .coverage_mapper import CoverageMapper, CoverageMappingError
from .schemas import (
    CoverageMappingResponse,
    SemanticCoverageMapping,
    SegmentCoverageMappings,
)
from .validators import CoverageMappingValidationError, CoverageMappingValidator

__all__ = [
    "CoverageMapper",
    "CoverageMappingError",
    "CoverageMappingResponse",
    "CoverageMappingValidationError",
    "CoverageMappingValidator",
    "SemanticCoverageMapping",
    "SegmentCoverageMappings",
]
