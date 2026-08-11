from __future__ import annotations

import pytest

from app.learning.evidence.qualification import (
    CandidateQualificationValidationError,
    CandidateQualificationValidator,
    CandidateQualifier,
)
from app.learning.evidence.retrieval import CandidateRetrievalSource, EvidenceCandidate
from app.learning.evidence.transcript import (
    MockTranscriptProvider,
    TranscriptAcquisitionError,
    TranscriptAcquirer,
    TranscriptDocument,
    TranscriptSegment,
)


def _candidate(**updates) -> EvidenceCandidate:
    candidate = EvidenceCandidate(
        candidateId="candidate-fastapi-routing",
        provider="bilibili",
        externalId="BV1FASTAPI",
        url="https://www.bilibili.com/video/BV1FASTAPI",
        title="FastAPI Routing Tutorial",
        durationSeconds=1800,
        contentFingerprint=f"sha256:{'a' * 64}",
        technologyVersions={"FastAPI": "0.115"},
        retrievalSource=CandidateRetrievalSource(
            retrievalPlanId="retrieval-plan-routing",
            knowledgeId="knowledge-routing",
            query="FastAPI Routing tutorial",
        ),
    )
    return candidate.model_copy(update=updates)


def _document(resource_id: str, fingerprint: str) -> TranscriptDocument:
    return TranscriptDocument(
        resourceId=resource_id,
        fingerprint=fingerprint,
        language="zh-CN",
        segments=[
            TranscriptSegment(
                id="cue-routing-1",
                startSeconds=60,
                endSeconds=120,
                text="这一段演示 FastAPI 路由和 GET 请求。",
            )
        ],
    )


def test_candidate_is_qualified_with_explicit_checks() -> None:
    qualified = CandidateQualifier().qualify(
        _candidate(),
        required_technology_versions={"FastAPI": "0.115"},
    )

    assert qualified.qualification_status == "qualified"
    assert qualified.resource is not None
    assert qualified.resource.external_id == "BV1FASTAPI"
    assert qualified.resource.content_fingerprint.startswith("sha256:")
    assert all(check.passed for check in qualified.checks)
    assert qualified.warnings == []
    assert "score" not in qualified.model_dump()


def test_version_mismatch_is_an_explicit_warning() -> None:
    qualified = CandidateQualifier().qualify(
        _candidate(),
        required_technology_versions={"FastAPI": "0.116"},
    )

    version_check = next(
        check for check in qualified.checks if check.name == "technology_version_compatible"
    )
    assert qualified.qualification_status == "warning"
    assert version_check.passed is False
    assert version_check.blocking is False
    assert qualified.warnings


def test_transcript_is_acquired_and_bound_to_qualified_resource() -> None:
    qualified = CandidateQualifier().qualify(_candidate())
    assert qualified.resource is not None
    document = _document(
        qualified.resource.id,
        qualified.resource.content_fingerprint,
    )
    provider = MockTranscriptProvider([document])

    result = TranscriptAcquirer(provider).acquire(qualified)

    assert result.status == "ACQUIRED"
    assert result.candidate_id == qualified.candidate_id
    assert result.resource_id == qualified.resource.id
    assert result.transcript == document
    assert provider.fetch_calls == [qualified.resource.id]


def test_missing_transcript_returns_unavailable_without_fabrication() -> None:
    qualified = CandidateQualifier().qualify(_candidate())

    result = TranscriptAcquirer(MockTranscriptProvider([])).acquire(qualified)

    assert result.status == "TRANSCRIPT_UNAVAILABLE"
    assert result.transcript is None
    assert result.error


def test_candidate_without_duration_is_rejected() -> None:
    qualified = CandidateQualifier().qualify(_candidate(duration_seconds=0))

    duration_check = next(
        check for check in qualified.checks if check.name == "duration_valid"
    )
    assert qualified.qualification_status == "rejected"
    assert qualified.resource is None
    assert duration_check.passed is False


def test_candidate_with_illegal_url_is_rejected() -> None:
    qualified = CandidateQualifier().qualify(_candidate(url="javascript:alert(1)"))

    url_check = next(check for check in qualified.checks if check.name == "url_valid")
    assert qualified.qualification_status == "rejected"
    assert qualified.resource is None
    assert url_check.passed is False


def test_duplicate_candidate_is_rejected_without_ranking() -> None:
    first = CandidateQualifier().qualify(_candidate())
    assert first.resource is not None

    duplicate = CandidateQualifier().qualify(
        _candidate(),
        existing_resources=[first.resource],
    )

    duplicate_check = next(
        check for check in duplicate.checks if check.name == "duplicate_free"
    )
    assert duplicate.qualification_status == "rejected"
    assert duplicate_check.passed is False


def test_qualification_validator_rejects_forged_candidate_reference() -> None:
    candidate = _candidate()
    qualified = CandidateQualifier().qualify(candidate)
    forged = qualified.model_copy(update={"candidate_id": "candidate-missing"})

    with pytest.raises(CandidateQualificationValidationError, match="candidate_reference"):
        CandidateQualificationValidator().validate(candidate, forged)


def test_transcript_fingerprint_mismatch_is_rejected() -> None:
    qualified = CandidateQualifier().qualify(_candidate())
    assert qualified.resource is not None
    document = _document(qualified.resource.id, f"sha256:{'b' * 64}")

    with pytest.raises(TranscriptAcquisitionError, match="transcript_fingerprint"):
        TranscriptAcquirer(MockTranscriptProvider([document])).acquire(qualified)


def test_forged_transcript_timestamp_is_rejected() -> None:
    qualified = CandidateQualifier().qualify(_candidate())
    assert qualified.resource is not None
    document = _document(
        qualified.resource.id,
        qualified.resource.content_fingerprint,
    )
    forged_segment = document.segments[0].model_copy(update={"end_seconds": 1900})
    forged_document = document.model_copy(update={"segments": [forged_segment]})

    with pytest.raises(TranscriptAcquisitionError, match="transcript_timestamp"):
        TranscriptAcquirer(MockTranscriptProvider([forged_document])).acquire(qualified)


def test_provider_metadata_cannot_be_used_as_transcript() -> None:
    qualified = CandidateQualifier().qualify(_candidate())
    assert qualified.resource is not None

    class MetadataOnlyProvider:
        def fetch_transcript(self, resource):
            return resource

    with pytest.raises(TranscriptAcquisitionError, match="metadata cannot be used"):
        TranscriptAcquirer(MetadataOnlyProvider()).acquire(qualified)


def test_transcript_contract_rejects_provider_owned_metadata_fields() -> None:
    qualified = CandidateQualifier().qualify(_candidate())
    assert qualified.resource is not None
    payload = _document(
        qualified.resource.id,
        qualified.resource.content_fingerprint,
    ).model_dump(by_alias=True)
    payload["url"] = qualified.resource.canonical_url
    payload["durationSeconds"] = qualified.resource.duration_seconds

    class ForgedTranscriptProvider:
        def fetch_transcript(self, resource):
            return payload

    with pytest.raises(TranscriptAcquisitionError, match="invalid TranscriptDocument"):
        TranscriptAcquirer(ForgedTranscriptProvider()).acquire(qualified)
