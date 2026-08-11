"""Verified transcript evidence boundary for Planix Learning."""

from .builders import TranscriptBuildError, TranscriptBuildResult, TranscriptSegmentBuilder
from .evidence_pipeline import (
    TranscriptEvidenceGenerationResult,
    TranscriptEvidencePipeline,
    TranscriptEvidencePipelineError,
)
from .providers import (
    AuthorizedTranscriptPayload,
    AuthorizedTranscriptSource,
    MockTranscriptProvider,
    SubtitleFileTranscriptProvider,
    TranscriptDocument,
    TranscriptProvider,
    TranscriptProviderError,
    TranscriptSegment,
    TranscriptSourceAdapter,
    TranscriptSourceMetadata,
)
from .services import (
    TranscriptAcquisitionError,
    TranscriptAcquisitionResult,
    TranscriptAcquisitionStatus,
    TranscriptAcquirer,
)
from .validators import TranscriptValidationError, TranscriptValidator

__all__ = [
    "AuthorizedTranscriptPayload",
    "AuthorizedTranscriptSource",
    "MockTranscriptProvider",
    "SubtitleFileTranscriptProvider",
    "TranscriptAcquisitionError",
    "TranscriptAcquisitionResult",
    "TranscriptAcquisitionStatus",
    "TranscriptAcquirer",
    "TranscriptBuildError",
    "TranscriptBuildResult",
    "TranscriptDocument",
    "TranscriptEvidenceGenerationResult",
    "TranscriptEvidencePipeline",
    "TranscriptEvidencePipelineError",
    "TranscriptProvider",
    "TranscriptProviderError",
    "TranscriptSegment",
    "TranscriptSourceAdapter",
    "TranscriptSourceMetadata",
    "TranscriptSegmentBuilder",
    "TranscriptValidationError",
    "TranscriptValidator",
]
