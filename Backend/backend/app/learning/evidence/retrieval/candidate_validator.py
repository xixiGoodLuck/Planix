from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .candidate import EvidenceCandidate, RetrievalRequest


class CandidateValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class CandidateValidator:
    _FORBIDDEN_KEYS = {
        "timestamp",
        "timestamps",
        "startseconds",
        "endseconds",
        "timerange",
        "segment",
        "segments",
        "segmentid",
        "coverage",
        "coverageedge",
        "coverageedges",
    }

    def validate_payload(
        self,
        payload: Mapping[str, Any],
        *,
        request: RetrievalRequest | None = None,
    ) -> EvidenceCandidate:
        self._reject_evidence_fields(payload)
        try:
            candidate = EvidenceCandidate.model_validate(payload)
        except ValidationError as exc:
            raise CandidateValidationError(
                "candidate_contract",
                "candidate",
                str(exc),
            ) from exc
        self.validate(candidate, request=request)
        return candidate

    def validate(
        self,
        candidate: EvidenceCandidate,
        *,
        request: RetrievalRequest | None = None,
    ) -> None:
        parsed = urlsplit(candidate.url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self._fail("candidate_url", "candidate.url", "candidate URL must be absolute HTTP(S)")
        if not candidate.external_id.strip():
            self._fail(
                "candidate_external_id",
                "candidate.externalId",
                "candidate external id is required",
            )
        if candidate.duration_seconds <= 0:
            self._fail(
                "candidate_duration",
                "candidate.durationSeconds",
                "candidate duration must be positive",
            )
        if not candidate.content_fingerprint.startswith("sha256:") or not candidate.content_fingerprint[7:]:
            self._fail(
                "candidate_fingerprint",
                "candidate.contentFingerprint",
                "candidate fingerprint was not generated successfully",
            )
        source = candidate.retrieval_source
        if not source.retrieval_plan_id or not source.knowledge_id or not source.query:
            self._fail(
                "candidate_retrieval_source",
                "candidate.retrievalSource",
                "candidate retrieval source is required",
            )
        if request is not None and (
            source.retrieval_plan_id != request.retrieval_plan_id
            or source.knowledge_id != request.knowledge_id
            or source.query != request.query
        ):
            self._fail(
                "candidate_retrieval_source",
                "candidate.retrievalSource",
                "candidate source does not match the retrieval request",
            )

    def _reject_evidence_fields(self, value: Any, path: str = "candidate") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).casefold().replace("_", "").replace("-", "")
                if normalized in self._FORBIDDEN_KEYS:
                    self._fail(
                        "candidate_evidence_field",
                        f"{path}.{key}",
                        "candidate must not contain timestamp, segment, or coverage fields",
                    )
                self._reject_evidence_fields(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._reject_evidence_fields(item, f"{path}.{index}")

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise CandidateValidationError(rule, path, message)


__all__ = ["CandidateValidationError", "CandidateValidator"]
