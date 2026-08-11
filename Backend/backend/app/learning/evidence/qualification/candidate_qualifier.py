from __future__ import annotations

from collections.abc import Iterable, Mapping

from ...contracts import VideoResource
from ...generators.base import generated_id
from ..retrieval import EvidenceCandidate
from .contracts import QualifiedCandidate
from .validators import CandidateQualificationValidator, evaluate_candidate_checks


class CandidateQualifier:
    """Deterministically qualifies provider metadata without ranking candidates."""

    def __init__(self, validator: CandidateQualificationValidator | None = None):
        self.validator = validator or CandidateQualificationValidator()

    def qualify(
        self,
        candidate: EvidenceCandidate,
        *,
        required_technology_versions: Mapping[str, str] | None = None,
        existing_resources: Iterable[VideoResource] = (),
    ) -> QualifiedCandidate:
        resources = tuple(existing_resources)
        checks, warnings = evaluate_candidate_checks(
            candidate,
            required_technology_versions=required_technology_versions,
            existing_resources=resources,
        )
        blocking_failed = any(not item.passed and item.blocking for item in checks)
        warning_present = any(not item.passed and not item.blocking for item in checks)
        status = (
            "rejected"
            if blocking_failed
            else "warning" if warning_present else "qualified"
        )

        resource = None
        projectable_checks = {
            "metadata_complete",
            "duration_valid",
            "url_valid",
            "fingerprint_valid",
            "external_identity_consistent",
        }
        if all(item.passed for item in checks if item.name in projectable_checks):
            resource = VideoResource(
                id=generated_id(
                    "video-resource",
                    candidate.candidate_id,
                    0,
                    f"{candidate.provider}:{candidate.external_id}:{candidate.content_fingerprint}",
                ),
                provider=candidate.provider,
                externalId=candidate.external_id,
                canonicalUrl=candidate.url,
                title=candidate.title,
                technologyVersions=candidate.technology_versions,
                durationSeconds=candidate.duration_seconds,
                contentFingerprint=candidate.content_fingerprint,
                availability="available",
            )

        qualified = QualifiedCandidate(
            candidateId=candidate.candidate_id,
            resource=resource,
            qualificationStatus=status,
            checks=checks,
            warnings=warnings,
        )
        self.validator.validate(
            candidate,
            qualified,
            required_technology_versions=required_technology_versions,
            existing_resources=resources,
        )
        return qualified


__all__ = ["CandidateQualifier"]
