"""Isolated provider-neutral evidence generation for Planix Learning."""

from .builders import EvidenceBuilder
from .coverage import CoverageAggregator, CoverageReport, CoverageReportValidator
from .mapping import CoverageMapper, CoverageMappingValidator
from .orchestration import GapCompletionOrchestrator, GapCompletionResult
from .providers import (
    BilibiliMetadataProvider,
    BilibiliProvider,
    MockVideoProvider,
    VideoEvidenceProvider,
    VideoSourceProvider,
)
from .qualification import CandidateQualifier, QualifiedCandidate
from .retrieval import (
    CandidateValidator,
    EvidenceCandidate,
    RetrievalExecutor,
    RetrievalGapPlan,
    RetrievalPlanValidator,
    RetrievalPlanner,
    RetrievalRequest,
)
from .search_query_generator import SearchQueryGenerator
from .services import EvidenceGenerationPipeline, EvidencePipelineError
from .supplement import EvidenceSupplementResult, EvidenceSupplementer
from .transcript import TranscriptAcquirer, TranscriptEvidencePipeline, TranscriptProvider
from .validators import EvidenceValidator

__all__ = [
    "BilibiliMetadataProvider",
    "BilibiliProvider",
    "CoverageMapper",
    "CoverageMappingValidator",
    "CoverageAggregator",
    "CoverageReport",
    "CoverageReportValidator",
    "EvidenceBuilder",
    "EvidenceGenerationPipeline",
    "EvidencePipelineError",
    "EvidenceSupplementResult",
    "EvidenceSupplementer",
    "EvidenceValidator",
    "GapCompletionOrchestrator",
    "GapCompletionResult",
    "MockVideoProvider",
    "SearchQueryGenerator",
    "RetrievalGapPlan",
    "RetrievalExecutor",
    "RetrievalRequest",
    "EvidenceCandidate",
    "CandidateValidator",
    "CandidateQualifier",
    "QualifiedCandidate",
    "RetrievalPlanValidator",
    "RetrievalPlanner",
    "TranscriptEvidencePipeline",
    "TranscriptAcquirer",
    "TranscriptProvider",
    "VideoEvidenceProvider",
    "VideoSourceProvider",
]
