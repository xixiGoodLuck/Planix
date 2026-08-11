from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Iterator, Protocol, cast

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningArtifact,
    LearningArtifactRef,
    LearningArtifactType,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ..generators.base import artifact_ref
from .contracts import (
    LearningArtifactEnvelope,
    LearningProgressEvent,
    LearningRunCheckpoint,
    LearningSessionState,
)


SUPPORTED_LEARNING_SCHEMA_VERSION = 1
ArtifactValidator = Callable[[LearningArtifact], None]


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactVersionConflict(ArtifactStoreError):
    pass


class LearningCheckpointConflict(ArtifactStoreError):
    pass


class ArtifactStore(Protocol):
    def save_artifact(
        self,
        session_id: str,
        artifact: LearningArtifact,
    ) -> LearningArtifactRef: ...

    def get_artifact(
        self,
        session_id: str,
        ref: LearningArtifactRef,
    ) -> LearningArtifact | None: ...

    def get_latest_version(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> LearningArtifactRef | None: ...

    def get_latest_artifact(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> LearningArtifact | None: ...

    def list_versions(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
    ) -> list[LearningArtifactRef]: ...

    def exists(self, session_id: str, ref: LearningArtifactRef) -> bool: ...

    def delete_if_invalid(
        self,
        session_id: str,
        ref: LearningArtifactRef,
        validator: ArtifactValidator | None = None,
    ) -> bool: ...


class CheckpointStore(Protocol):
    def save_checkpoint(
        self,
        checkpoint: LearningRunCheckpoint,
        *,
        expected_version: int | None = None,
    ) -> LearningRunCheckpoint: ...

    def get_checkpoint(self, run_id: str) -> LearningRunCheckpoint | None: ...


class LearningRunStore(Protocol):
    def save_run(
        self,
        state: LearningSessionState,
        *,
        run_fingerprint: str = "",
    ) -> None: ...

    def get_run(self, run_id: str) -> LearningSessionState | None: ...

    def save_progress_event(
        self,
        run_id: str,
        event: LearningProgressEvent,
    ) -> None: ...

    def get_progress_events(self, run_id: str) -> list[LearningProgressEvent]: ...


_ARTIFACT_MODELS: dict[LearningArtifactType, type[LearningArtifact]] = {
    "learning_scope": LearningScope,
    "capability_graph": CapabilityGraph,
    "knowledge_graph": KnowledgeGraph,
    "evidence_graph": EvidenceGraph,
    "content_selection": ContentSelection,
    "learning_content_plan": LearningContentPlan,
    "learning_quality_report": LearningQualityReport,
}


def artifact_type_for(artifact: LearningArtifact) -> LearningArtifactType:
    for artifact_type, model in _ARTIFACT_MODELS.items():
        if isinstance(artifact, model):
            return artifact_type
    raise ArtifactStoreError(
        f"unsupported Learning artifact type: {type(artifact).__name__}"
    )


def artifact_envelope(
    session_id: str,
    artifact: LearningArtifact,
) -> LearningArtifactEnvelope:
    if not session_id.strip():
        raise ArtifactStoreError("session_id is required")
    computed_fields = set(type(artifact).model_computed_fields)
    return LearningArtifactEnvelope(
        artifactType=artifact_type_for(artifact),
        artifactId=artifact.artifact_id,
        version=artifact.version,
        sessionId=session_id,
        schemaVersion=artifact.schema_version,
        createdAt=artifact.created_at,
        content=artifact.model_dump(
            mode="json",
            by_alias=True,
            exclude=computed_fields,
        ),
    )


def canonical_artifact_payload(envelope: LearningArtifactEnvelope) -> dict[str, Any]:
    content = deepcopy(envelope.content)
    content.pop("createdAt", None)
    content.pop("created_at", None)
    return {
        "artifactType": envelope.artifact_type,
        "artifactId": envelope.artifact_id,
        "version": envelope.version,
        "sessionId": envelope.session_id,
        "schemaVersion": envelope.schema_version,
        "content": content,
    }


def artifact_content_hash(envelope: LearningArtifactEnvelope) -> str:
    canonical = json.dumps(
        canonical_artifact_payload(envelope),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


def artifact_from_envelope(envelope: LearningArtifactEnvelope) -> LearningArtifact:
    if envelope.schema_version != SUPPORTED_LEARNING_SCHEMA_VERSION:
        raise ArtifactStoreError(
            f"unsupported Learning artifact schema: {envelope.schema_version}"
        )
    model = _ARTIFACT_MODELS.get(envelope.artifact_type)
    if model is None:
        raise ArtifactStoreError(
            f"unsupported Learning artifact type: {envelope.artifact_type}"
        )
    try:
        artifact = cast(LearningArtifact, model.model_validate(envelope.content))
    except Exception as exc:
        raise ArtifactStoreError("stored Learning artifact content is invalid") from exc
    if (
        artifact.artifact_id != envelope.artifact_id
        or artifact.version != envelope.version
        or artifact.schema_version != envelope.schema_version
    ):
        raise ArtifactStoreError("stored Learning artifact envelope does not match content")
    return artifact


class InMemoryArtifactStore:
    """Thread-safe ArtifactStore/CheckpointStore reference implementation."""

    def __init__(self):
        self._artifacts: dict[
            str,
            dict[str, dict[str, dict[int, LearningArtifactEnvelope]]],
        ] = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
        self._latest: dict[tuple[str, str], LearningArtifactRef] = {}
        self._checkpoints: dict[str, LearningRunCheckpoint] = {}
        self._resume_events: dict[str, list[Any]] = defaultdict(list)
        self._runs: dict[str, LearningSessionState] = {}
        self._progress_events: dict[str, list[LearningProgressEvent]] = defaultdict(list)
        self._lock = RLock()

    @contextmanager
    def atomic(self) -> Iterator["InMemoryArtifactStore"]:
        with self._lock:
            snapshot = (
                deepcopy(self._artifacts),
                deepcopy(self._latest),
                deepcopy(self._checkpoints),
                deepcopy(self._resume_events),
                deepcopy(self._runs),
                deepcopy(self._progress_events),
            )
            try:
                yield self
            except BaseException:
                (
                    self._artifacts,
                    self._latest,
                    self._checkpoints,
                    self._resume_events,
                    self._runs,
                    self._progress_events,
                ) = snapshot
                raise

    def save_artifact(
        self,
        session_id: str,
        artifact: LearningArtifact,
    ) -> LearningArtifactRef:
        envelope = artifact_envelope(session_id, artifact)
        ref = artifact_ref(envelope.artifact_type, artifact)
        with self._lock:
            versions = self._artifacts[session_id][envelope.artifact_type][
                artifact.artifact_id
            ]
            existing = versions.get(artifact.version)
            if existing is not None:
                if existing.model_dump(mode="json") != envelope.model_dump(mode="json"):
                    raise ArtifactVersionConflict(
                        "an artifact id/version cannot be overwritten with different content"
                    )
                return ref
            versions[artifact.version] = envelope.model_copy(deep=True)
            latest = self._latest.get((session_id, envelope.artifact_type))
            if latest is None or artifact.version >= latest.version:
                self._latest[(session_id, envelope.artifact_type)] = ref.model_copy(
                    deep=True
                )
        return ref

    def get_artifact(
        self,
        session_id: str,
        ref: LearningArtifactRef,
    ) -> LearningArtifact | None:
        with self._lock:
            envelope = (
                self._artifacts.get(session_id, {})
                .get(ref.artifact_type, {})
                .get(ref.artifact_id, {})
                .get(ref.version)
            )
            if envelope is None:
                return None
            return artifact_from_envelope(envelope.model_copy(deep=True))

    def get_latest_version(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> LearningArtifactRef | None:
        with self._lock:
            if artifact_id is None:
                latest = self._latest.get((session_id, artifact_type))
                return latest.model_copy(deep=True) if latest is not None else None
            versions = (
                self._artifacts.get(session_id, {})
                .get(artifact_type, {})
                .get(artifact_id, {})
            )
            if not versions:
                return None
            return LearningArtifactRef(
                artifactType=artifact_type,
                artifactId=artifact_id,
                version=max(versions),
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
        with self._lock:
            versions = (
                self._artifacts.get(session_id, {})
                .get(artifact_type, {})
                .get(artifact_id, {})
            )
            return [
                LearningArtifactRef(
                    artifactType=artifact_type,
                    artifactId=artifact_id,
                    version=version,
                )
                for version in sorted(versions)
            ]

    def exists(self, session_id: str, ref: LearningArtifactRef) -> bool:
        with self._lock:
            return (
                ref.version
                in self._artifacts.get(session_id, {})
                .get(ref.artifact_type, {})
                .get(ref.artifact_id, {})
            )

    def delete_if_invalid(
        self,
        session_id: str,
        ref: LearningArtifactRef,
        validator: ArtifactValidator | None = None,
    ) -> bool:
        with self._lock:
            envelope = (
                self._artifacts.get(session_id, {})
                .get(ref.artifact_type, {})
                .get(ref.artifact_id, {})
                .get(ref.version)
            )
            if envelope is None:
                return False
            try:
                artifact = artifact_from_envelope(envelope)
                if validator is not None:
                    validator(artifact)
                return False
            except Exception:
                del self._artifacts[session_id][ref.artifact_type][ref.artifact_id][
                    ref.version
                ]
                self._refresh_latest(session_id, ref.artifact_type)
                return True

    def save_checkpoint(
        self,
        checkpoint: LearningRunCheckpoint,
        *,
        expected_version: int | None = None,
    ) -> LearningRunCheckpoint:
        with self._lock:
            current = self._checkpoints.get(checkpoint.run_id)
            current_version = current.checkpoint_version if current is not None else 0
            if expected_version is not None and expected_version != current_version:
                raise LearningCheckpointConflict(
                    f"stale Learning checkpoint: expected {expected_version}, current {current_version}"
                )
            next_version = 1 if current is None else current_version + 1
            stored = checkpoint.model_copy(update={"checkpoint_version": next_version})
            self._checkpoints[checkpoint.run_id] = stored.model_copy(deep=True)
            return stored.model_copy(deep=True)

    def get_checkpoint(self, run_id: str) -> LearningRunCheckpoint | None:
        with self._lock:
            checkpoint = self._checkpoints.get(run_id)
            return checkpoint.model_copy(deep=True) if checkpoint is not None else None

    def save_resume_event(self, event: Any) -> None:
        run_id = str(getattr(event, "run_id", "")).strip()
        if not run_id:
            raise ArtifactStoreError("resume event run_id is required")
        with self._lock:
            self._resume_events[run_id].append(deepcopy(event))

    def get_resume_events(self, run_id: str) -> list[Any]:
        with self._lock:
            return deepcopy(self._resume_events.get(run_id, []))

    def save_run(
        self,
        state: LearningSessionState,
        *,
        run_fingerprint: str = "",
    ) -> None:
        del run_fingerprint
        with self._lock:
            self._runs[state.session_id] = state.model_copy(deep=True)

    def get_run(self, run_id: str) -> LearningSessionState | None:
        with self._lock:
            state = self._runs.get(run_id)
            return state.model_copy(deep=True) if state is not None else None

    def save_progress_event(
        self,
        run_id: str,
        event: LearningProgressEvent,
    ) -> None:
        with self._lock:
            self._progress_events[run_id].append(event.model_copy(deep=True))

    def get_progress_events(self, run_id: str) -> list[LearningProgressEvent]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in self._progress_events.get(run_id, [])
            ]

    def _refresh_latest(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
    ) -> None:
        candidates = [
            envelope
            for versions in self._artifacts.get(session_id, {})
            .get(artifact_type, {})
            .values()
            for envelope in versions.values()
        ]
        if not candidates:
            self._latest.pop((session_id, artifact_type), None)
            return
        latest = max(candidates, key=lambda item: (item.version, item.created_at))
        self._latest[(session_id, artifact_type)] = LearningArtifactRef(
            artifactType=artifact_type,
            artifactId=latest.artifact_id,
            version=latest.version,
        )


__all__ = [
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactVersionConflict",
    "ArtifactValidator",
    "CheckpointStore",
    "LearningCheckpointConflict",
    "LearningRunStore",
    "InMemoryArtifactStore",
    "SUPPORTED_LEARNING_SCHEMA_VERSION",
    "artifact_envelope",
    "artifact_content_hash",
    "canonical_artifact_payload",
    "artifact_from_envelope",
    "artifact_type_for",
]
