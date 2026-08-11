from __future__ import annotations

from collections.abc import Iterable, Mapping
from string import hexdigits
from urllib.parse import urlsplit

from ...contracts import VideoResource
from ..retrieval import EvidenceCandidate
from .contracts import QualificationCheck, QualifiedCandidate


class CandidateQualificationValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


REQUIRED_CHECKS = (
    "metadata_complete",
    "duration_valid",
    "url_valid",
    "fingerprint_valid",
    "external_identity_consistent",
    "technology_version_compatible",
    "duplicate_free",
)


def evaluate_candidate_checks(
    candidate: EvidenceCandidate,
    *,
    required_technology_versions: Mapping[str, str] | None = None,
    existing_resources: Iterable[VideoResource] = (),
) -> tuple[list[QualificationCheck], list[str]]:
    provider = getattr(candidate, "provider", "")
    external_id = getattr(candidate, "external_id", "")
    url = getattr(candidate, "url", "")
    title = getattr(candidate, "title", "")
    duration = getattr(candidate, "duration_seconds", None)
    fingerprint = getattr(candidate, "content_fingerprint", "")
    source = getattr(candidate, "retrieval_source", None)

    source_complete = source is not None and all(
        isinstance(getattr(source, field, None), str)
        and bool(getattr(source, field).strip())
        for field in ("retrieval_plan_id", "knowledge_id", "query")
    )
    metadata_complete = all(
        isinstance(value, str) and bool(value.strip())
        for value in (provider, external_id, url, title, fingerprint)
    ) and source_complete
    duration_valid = (
        isinstance(duration, int)
        and not isinstance(duration, bool)
        and duration > 0
    )
    parsed = urlsplit(url.strip()) if isinstance(url, str) else None
    url_valid = bool(parsed and parsed.scheme in {"http", "https"} and parsed.netloc)
    digest = (
        fingerprint[7:]
        if isinstance(fingerprint, str) and fingerprint.startswith("sha256:")
        else ""
    )
    fingerprint_valid = len(digest) == 64 and all(
        character in hexdigits for character in digest
    )
    identity_valid = bool(
        isinstance(provider, str)
        and provider.strip()
        and isinstance(external_id, str)
        and external_id.strip()
    )

    expected_versions = {
        str(name).casefold(): str(version).strip()
        for name, version in (required_technology_versions or {}).items()
    }
    actual_versions = {
        str(name).casefold(): str(version).strip()
        for name, version in getattr(candidate, "technology_versions", {}).items()
    }
    incompatible_versions = [
        name
        for name, expected in expected_versions.items()
        if actual_versions.get(name) != expected
    ]
    version_compatible = not incompatible_versions

    duplicate = any(
        (resource.provider == provider and resource.external_id == external_id)
        or (
            bool(fingerprint)
            and resource.content_fingerprint == fingerprint
        )
        for resource in existing_resources
    )

    checks = [
        QualificationCheck(
            name="metadata_complete",
            passed=metadata_complete,
            blocking=True,
            reason=(
                "required provider metadata is complete"
                if metadata_complete
                else "required provider metadata is incomplete"
            ),
        ),
        QualificationCheck(
            name="duration_valid",
            passed=duration_valid,
            blocking=True,
            reason=(
                "duration is available and positive"
                if duration_valid
                else "duration is missing or invalid"
            ),
        ),
        QualificationCheck(
            name="url_valid",
            passed=url_valid,
            blocking=True,
            reason=(
                "URL is an absolute HTTP(S) resource"
                if url_valid
                else "URL is not a valid absolute HTTP(S) resource"
            ),
        ),
        QualificationCheck(
            name="fingerprint_valid",
            passed=fingerprint_valid,
            blocking=True,
            reason=(
                "content fingerprint is valid"
                if fingerprint_valid
                else "content fingerprint is missing or invalid"
            ),
        ),
        QualificationCheck(
            name="external_identity_consistent",
            passed=identity_valid,
            blocking=True,
            reason=(
                "provider and external identity are present"
                if identity_valid
                else "provider or external identity is missing"
            ),
        ),
        QualificationCheck(
            name="technology_version_compatible",
            passed=version_compatible,
            blocking=False,
            reason=(
                "technology versions are compatible"
                if version_compatible
                else "technology version differs or is unavailable: "
                + ", ".join(incompatible_versions)
            ),
        ),
        QualificationCheck(
            name="duplicate_free",
            passed=not duplicate,
            blocking=True,
            reason=(
                "candidate is not a duplicate"
                if not duplicate
                else "candidate duplicates an existing resource"
            ),
        ),
    ]
    warnings = []
    if incompatible_versions:
        warnings.append(
            "Technology version compatibility could not be confirmed for: "
            + ", ".join(incompatible_versions)
        )
    return checks, warnings


class CandidateQualificationValidator:
    def validate(
        self,
        candidate: EvidenceCandidate,
        qualified: QualifiedCandidate,
        *,
        required_technology_versions: Mapping[str, str] | None = None,
        existing_resources: Iterable[VideoResource] = (),
    ) -> None:
        if qualified.candidate_id != getattr(candidate, "candidate_id", None):
            self._fail(
                "candidate_reference",
                "qualifiedCandidate.candidateId",
                "candidate reference does not match",
            )

        names = [item.name for item in qualified.checks]
        if len(names) != len(set(names)) or set(names) != set(REQUIRED_CHECKS):
            self._fail(
                "qualification_checks",
                "qualifiedCandidate.checks",
                "qualification checks are incomplete or duplicated",
            )

        expected_checks, expected_warnings = evaluate_candidate_checks(
            candidate,
            required_technology_versions=required_technology_versions,
            existing_resources=existing_resources,
        )
        actual_by_name = {item.name: item for item in qualified.checks}
        for expected in expected_checks:
            if actual_by_name[expected.name] != expected:
                self._fail(
                    "qualification_check_result",
                    f"qualifiedCandidate.checks.{expected.name}",
                    "qualification check result was not derived from candidate data",
                )

        blocking_failed = any(not item.passed and item.blocking for item in expected_checks)
        warning_present = any(not item.passed and not item.blocking for item in expected_checks)
        expected_status = (
            "rejected"
            if blocking_failed
            else "warning" if warning_present else "qualified"
        )
        if qualified.qualification_status != expected_status:
            self._fail(
                "qualification_status",
                "qualifiedCandidate.qualificationStatus",
                "qualification status does not match check results",
            )
        if qualified.warnings != expected_warnings:
            self._fail(
                "qualification_warnings",
                "qualifiedCandidate.warnings",
                "warnings do not match qualification checks",
            )

        if expected_status in {"qualified", "warning"} and qualified.resource is None:
            self._fail(
                "qualified_resource",
                "qualifiedCandidate.resource",
                "accepted candidate must include a resource",
            )
        if qualified.resource is not None:
            self._validate_resource_projection(candidate, qualified.resource)

    def validate_boundary(self, qualified: QualifiedCandidate) -> None:
        names = [item.name for item in qualified.checks]
        if len(names) != len(set(names)) or set(names) != set(REQUIRED_CHECKS):
            self._fail(
                "qualification_checks",
                "qualifiedCandidate.checks",
                "qualification checks are incomplete or duplicated",
            )
        blocking_failed = any(not item.passed and item.blocking for item in qualified.checks)
        warning_present = any(not item.passed and not item.blocking for item in qualified.checks)
        expected_status = (
            "rejected"
            if blocking_failed
            else "warning" if warning_present else "qualified"
        )
        if qualified.qualification_status != expected_status:
            self._fail(
                "qualification_status",
                "qualifiedCandidate.qualificationStatus",
                "qualification status does not match check results",
            )
        if expected_status in {"qualified", "warning"} and qualified.resource is None:
            self._fail(
                "qualified_resource",
                "qualifiedCandidate.resource",
                "accepted candidate must include a resource",
            )
        if warning_present and not qualified.warnings:
            self._fail(
                "qualification_warnings",
                "qualifiedCandidate.warnings",
                "warning status requires an explicit warning",
            )
        if not warning_present and qualified.warnings:
            self._fail(
                "qualification_warnings",
                "qualifiedCandidate.warnings",
                "warnings require a failed non-blocking check",
            )

    def _validate_resource_projection(
        self,
        candidate: EvidenceCandidate,
        resource: VideoResource,
    ) -> None:
        expected = (
            getattr(candidate, "provider", None),
            getattr(candidate, "external_id", None),
            getattr(candidate, "url", None),
            getattr(candidate, "title", None),
            getattr(candidate, "duration_seconds", None),
            getattr(candidate, "content_fingerprint", None),
            getattr(candidate, "technology_versions", {}),
        )
        actual = (
            resource.provider,
            resource.external_id,
            resource.canonical_url,
            resource.title,
            resource.duration_seconds,
            resource.content_fingerprint,
            resource.technology_versions,
        )
        if actual != expected:
            self._fail(
                "resource_projection",
                "qualifiedCandidate.resource",
                "resource does not match the candidate metadata",
            )

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise CandidateQualificationValidationError(rule, path, message)


__all__ = [
    "CandidateQualificationValidationError",
    "CandidateQualificationValidator",
    "REQUIRED_CHECKS",
    "evaluate_candidate_checks",
]
