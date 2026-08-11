from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, ContextManager, Iterator, Protocol

from ...contracts import (
    LearningArtifact,
    LearningArtifactRef,
    LearningArtifactType,
)
from ..artifact_store import (
    ArtifactStoreError,
    ArtifactValidator,
    SUPPORTED_LEARNING_SCHEMA_VERSION,
    artifact_envelope,
    artifact_from_envelope,
)
from ..contracts import LearningArtifactEnvelope, LearningRunCheckpoint


StoredObject = LearningArtifactEnvelope | LearningRunCheckpoint | Mapping[str, Any]
LEARNING_ARTIFACT_REPOSITORY_NAMESPACE = "learning_artifacts"


class LearningArtifactRepository(Protocol):
    """Dedicated Learning persistence boundary for a future PostgreSQL adapter."""

    repository_namespace: str
    schema_version: int

    def save(self, envelope: LearningArtifactEnvelope) -> None: ...

    def get(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
        version: int,
    ) -> StoredObject | None: ...

    def list_versions(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> list[StoredObject]: ...

    def delete(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
        version: int,
    ) -> None: ...

    def save_checkpoint(self, checkpoint: LearningRunCheckpoint) -> None: ...

    def get_checkpoint(self, run_id: str) -> StoredObject | None: ...

    def transaction(self) -> ContextManager[None]: ...

    def save_resume_event(self, event: Any) -> None: ...

    def get_resume_events(self, run_id: str) -> list[Any]: ...


def validate_learning_artifact_repository(
    repository: object,
    *,
    require_metadata: bool,
) -> None:
    namespace = getattr(repository, "repository_namespace", None)
    if namespace is not None and namespace != LEARNING_ARTIFACT_REPOSITORY_NAMESPACE:
        raise ArtifactStoreError(
            "Learning repository must use the dedicated learning_artifacts namespace"
        )
    if require_metadata and namespace != LEARNING_ARTIFACT_REPOSITORY_NAMESPACE:
        raise ArtifactStoreError(
            "Learning repository namespace metadata is required"
        )
    schema_version = getattr(repository, "schema_version", None)
    if schema_version is not None and schema_version != SUPPORTED_LEARNING_SCHEMA_VERSION:
        raise ArtifactStoreError("Learning repository schema version is incompatible")
    if require_metadata and schema_version != SUPPORTED_LEARNING_SCHEMA_VERSION:
        raise ArtifactStoreError("Learning repository schema version metadata is required")


class PostgresArtifactStore:
    """ArtifactStore adapter with no SQL or production-table assumptions.

    The injected repository owns PostgreSQL transactions and must enforce a unique
    key on ``session_id, artifact_type, artifact_id, version``. This adapter stays
    isolated until a production repository is explicitly approved.
    """

    def __init__(self, repository: LearningArtifactRepository):
        validate_learning_artifact_repository(repository, require_metadata=False)
        self.repository = repository

    @contextmanager
    def atomic(self) -> Iterator["PostgresArtifactStore"]:
        try:
            with self.repository.transaction():
                yield self
        except Exception as exc:
            if isinstance(exc, ArtifactStoreError):
                raise
            raise ArtifactStoreError("persistent resume transaction failed") from exc

    def save_artifact(
        self,
        session_id: str,
        artifact: LearningArtifact,
    ) -> LearningArtifactRef:
        envelope = artifact_envelope(session_id, artifact)
        existing_raw = self._repo_get(
            session_id,
            envelope.artifact_type,
            envelope.artifact_id,
            envelope.version,
        )
        if existing_raw is not None:
            existing = self._as_envelope(existing_raw)
            if existing.model_dump(mode="json") != envelope.model_dump(mode="json"):
                raise ArtifactStoreError(
                    "an artifact id/version cannot be overwritten with different content"
                )
        else:
            try:
                self._repo_save(envelope.model_copy(deep=True))
            except Exception as exc:
                concurrent_raw = self._repo_get(
                    session_id,
                    envelope.artifact_type,
                    envelope.artifact_id,
                    envelope.version,
                )
                if concurrent_raw is None:
                    raise ArtifactStoreError("persistent artifact insert failed") from exc
                concurrent = self._as_envelope(concurrent_raw)
                if concurrent.model_dump(mode="json") != envelope.model_dump(mode="json"):
                    raise ArtifactStoreError(
                        "an artifact id/version cannot be overwritten with different content"
                    ) from exc
        return LearningArtifactRef(
            artifactType=envelope.artifact_type,
            artifactId=envelope.artifact_id,
            version=envelope.version,
        )

    def get_artifact(
        self,
        session_id: str,
        ref: LearningArtifactRef,
    ) -> LearningArtifact | None:
        raw = self._repo_get(
            session_id,
            ref.artifact_type,
            ref.artifact_id,
            ref.version,
        )
        if raw is None:
            return None
        envelope = self._as_envelope(raw)
        self._assert_key(session_id, ref, envelope)
        return artifact_from_envelope(envelope)

    def get_latest_version(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> LearningArtifactRef | None:
        envelopes = self._list_envelopes(session_id, artifact_type, artifact_id)
        if not envelopes:
            return None
        latest = max(envelopes, key=lambda item: (item.version, item.created_at))
        return LearningArtifactRef(
            artifactType=latest.artifact_type,
            artifactId=latest.artifact_id,
            version=latest.version,
        )

    def get_latest_artifact(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> LearningArtifact | None:
        ref = self.get_latest_version(session_id, artifact_type, artifact_id)
        return self.get_artifact(session_id, ref) if ref is not None else None

    def list_versions(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
    ) -> list[LearningArtifactRef]:
        envelopes = self._list_envelopes(session_id, artifact_type, artifact_id)
        return [
            LearningArtifactRef(
                artifactType=item.artifact_type,
                artifactId=item.artifact_id,
                version=item.version,
            )
            for item in sorted(envelopes, key=lambda item: item.version)
        ]

    def exists(self, session_id: str, ref: LearningArtifactRef) -> bool:
        return (
            self._repo_get(
                session_id,
                ref.artifact_type,
                ref.artifact_id,
                ref.version,
            )
            is not None
        )

    def delete_if_invalid(
        self,
        session_id: str,
        ref: LearningArtifactRef,
        validator: ArtifactValidator | None = None,
    ) -> bool:
        raw = self._repo_get(
            session_id,
            ref.artifact_type,
            ref.artifact_id,
            ref.version,
        )
        if raw is None:
            return False
        try:
            artifact = self.get_artifact(session_id, ref)
            if artifact is None:
                return False
            if validator is not None:
                validator(artifact)
            return False
        except Exception:
            self._repo_delete(
                session_id,
                ref.artifact_type,
                ref.artifact_id,
                ref.version,
            )
            return True

    def save_checkpoint(self, checkpoint: LearningRunCheckpoint) -> None:
        try:
            self.repository.save_checkpoint(checkpoint.model_copy(deep=True))
        except Exception as exc:
            raise ArtifactStoreError("persistent checkpoint save failed") from exc

    def get_checkpoint(self, run_id: str) -> LearningRunCheckpoint | None:
        raw = self.repository.get_checkpoint(run_id)
        if raw is None:
            return None
        try:
            checkpoint = (
                raw
                if isinstance(raw, LearningRunCheckpoint)
                else LearningRunCheckpoint.model_validate(raw)
            )
        except Exception as exc:
            raise ArtifactStoreError("stored Learning checkpoint is invalid") from exc
        if checkpoint.run_id != run_id:
            raise ArtifactStoreError("stored Learning checkpoint run_id mismatch")
        return checkpoint.model_copy(deep=True)

    def save_resume_event(self, event: Any) -> None:
        try:
            self._repo_save_resume_event(event)
        except Exception as exc:
            raise ArtifactStoreError("persistent resume audit save failed") from exc

    def get_resume_events(self, run_id: str) -> list[Any]:
        return self._repo_get_resume_events(run_id)

    def _list_envelopes(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None,
    ) -> list[LearningArtifactEnvelope]:
        envelopes = [
            self._as_envelope(item)
            for item in self._repo_list_versions(
                session_id,
                artifact_type,
                artifact_id,
            )
        ]
        for envelope in envelopes:
            if envelope.session_id != session_id or envelope.artifact_type != artifact_type:
                raise ArtifactStoreError("persistent artifact query returned a foreign row")
            if artifact_id is not None and envelope.artifact_id != artifact_id:
                raise ArtifactStoreError("persistent artifact query returned a foreign id")
        return envelopes

    def _repo_save(self, envelope: LearningArtifactEnvelope) -> None:
        operation = getattr(self.repository, "save", None)
        if not callable(operation):
            operation = getattr(self.repository, "insert_artifact")
        operation(envelope)

    def _repo_get(self, *args) -> StoredObject | None:
        operation = getattr(self.repository, "get", None)
        if not callable(operation):
            operation = getattr(self.repository, "get_artifact")
        return operation(*args)

    def _repo_list_versions(self, *args) -> list[StoredObject]:
        operation = getattr(self.repository, "list_versions", None)
        if not callable(operation):
            operation = getattr(self.repository, "list_artifacts")
        return operation(*args)

    def _repo_delete(self, *args) -> None:
        operation = getattr(self.repository, "delete", None)
        if not callable(operation):
            operation = getattr(self.repository, "delete_artifact")
        operation(*args)

    def _repo_save_resume_event(self, event: Any) -> None:
        operation = getattr(self.repository, "save_resume_event", None)
        if not callable(operation):
            operation = getattr(self.repository, "insert_resume_event")
        operation(event)

    def _repo_get_resume_events(self, run_id: str) -> list[Any]:
        operation = getattr(self.repository, "get_resume_events", None)
        if not callable(operation):
            operation = getattr(self.repository, "list_resume_events")
        return operation(run_id)

    @staticmethod
    def _as_envelope(raw: StoredObject) -> LearningArtifactEnvelope:
        try:
            return (
                raw
                if isinstance(raw, LearningArtifactEnvelope)
                else LearningArtifactEnvelope.model_validate(raw)
            ).model_copy(deep=True)
        except Exception as exc:
            raise ArtifactStoreError("stored Learning artifact envelope is invalid") from exc

    @staticmethod
    def _assert_key(
        session_id: str,
        ref: LearningArtifactRef,
        envelope: LearningArtifactEnvelope,
    ) -> None:
        if (
            envelope.session_id != session_id
            or envelope.artifact_type != ref.artifact_type
            or envelope.artifact_id != ref.artifact_id
            or envelope.version != ref.version
        ):
            raise ArtifactStoreError("stored Learning artifact key mismatch")


__all__ = [
    "LEARNING_ARTIFACT_REPOSITORY_NAMESPACE",
    "LearningArtifactRepository",
    "PostgresArtifactStore",
    "validate_learning_artifact_repository",
]
