"""Transcript acquisition boundary for qualified Learning candidates."""

from .contracts import TranscriptAcquisitionResult, TranscriptAcquisitionStatus
from .transcript_acquirer import TranscriptAcquisitionError, TranscriptAcquirer
from .registry_service import (
    DEFAULT_TRANSCRIPT_MAX_BYTES,
    HARD_TRANSCRIPT_MAX_BYTES,
    LearningTranscriptRegistrationService,
    TranscriptRegistrationError,
    configured_transcript_max_bytes,
)

__all__ = [
    "TranscriptAcquisitionError",
    "TranscriptAcquisitionResult",
    "TranscriptAcquisitionStatus",
    "TranscriptAcquirer",
    "DEFAULT_TRANSCRIPT_MAX_BYTES",
    "HARD_TRANSCRIPT_MAX_BYTES",
    "LearningTranscriptRegistrationService",
    "TranscriptRegistrationError",
    "configured_transcript_max_bytes",
]
