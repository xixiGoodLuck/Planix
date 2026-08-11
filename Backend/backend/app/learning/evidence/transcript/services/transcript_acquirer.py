from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from ...qualification import (
    CandidateQualificationValidationError,
    CandidateQualificationValidator,
    QualifiedCandidate,
)
from ..providers import TranscriptDocument, TranscriptProvider, TranscriptProviderError
from ..validators import TranscriptValidationError, TranscriptValidator
from .contracts import TranscriptAcquisitionResult


class TranscriptAcquisitionError(ValueError):
    def __init__(self, rule: str, message: str):
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


class TranscriptAcquirer:
    """Acquires raw transcript documents without producing evidence or coverage."""

    def __init__(
        self,
        provider: TranscriptProvider,
        *,
        qualification_validator: CandidateQualificationValidator | None = None,
        transcript_validator: TranscriptValidator | None = None,
    ):
        self.provider = provider
        self.qualification_validator = (
            qualification_validator or CandidateQualificationValidator()
        )
        self.transcript_validator = transcript_validator or TranscriptValidator()

    def acquire(self, candidate: QualifiedCandidate) -> TranscriptAcquisitionResult:
        try:
            self.qualification_validator.validate_boundary(candidate)
        except CandidateQualificationValidationError as exc:
            raise TranscriptAcquisitionError("qualified_candidate", str(exc)) from exc
        if candidate.qualification_status == "rejected" or candidate.resource is None:
            raise TranscriptAcquisitionError(
                "qualified_candidate",
                "transcript acquisition requires a qualified or warning candidate",
            )

        resource = candidate.resource
        try:
            raw_document = self.provider.fetch_transcript(resource)
        except TranscriptProviderError as exc:
            return TranscriptAcquisitionResult(
                status="TRANSCRIPT_UNAVAILABLE",
                candidateId=candidate.candidate_id,
                resourceId=resource.id,
                error=str(exc) or "transcript is unavailable",
            )

        document = self._coerce_document(raw_document)
        try:
            self.transcript_validator.validate(resource, document)
        except TranscriptValidationError as exc:
            raise TranscriptAcquisitionError("transcript_validation", str(exc)) from exc
        return TranscriptAcquisitionResult(
            status="ACQUIRED",
            candidateId=candidate.candidate_id,
            resourceId=resource.id,
            transcript=document,
        )

    @staticmethod
    def _coerce_document(value: Any) -> TranscriptDocument:
        if isinstance(value, TranscriptDocument):
            return value
        if isinstance(value, Mapping):
            try:
                return TranscriptDocument.model_validate(value)
            except ValidationError as exc:
                raise TranscriptAcquisitionError(
                    "transcript_contract",
                    "provider returned an invalid TranscriptDocument",
                ) from exc
        raise TranscriptAcquisitionError(
            "transcript_contract",
            "provider metadata cannot be used as a TranscriptDocument",
        )


__all__ = ["TranscriptAcquisitionError", "TranscriptAcquirer"]
