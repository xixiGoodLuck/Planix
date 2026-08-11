from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.learning.runtime import PostgresArtifactStore
from app.learning.runtime.artifact_store import ArtifactStoreError
from app.learning.runtime.contracts import LearningRunCheckpoint

from learning_fixtures import build_fastapi_crud_learning_fixture


class LearningRepositoryFixture:
    repository_namespace = "learning_artifacts"
    schema_version = 1

    def __init__(self):
        self.artifacts = {}
        self.checkpoints = {}
        self.events = []

    @staticmethod
    def _key(session_id, artifact_type, artifact_id, version):
        return session_id, artifact_type, artifact_id, version

    def save(self, envelope):
        key = self._key(
            envelope.session_id,
            envelope.artifact_type,
            envelope.artifact_id,
            envelope.version,
        )
        if key in self.artifacts:
            raise ValueError("duplicate")
        self.artifacts[key] = envelope.model_copy(deep=True)

    def get(self, session_id, artifact_type, artifact_id, version):
        value = self.artifacts.get(
            self._key(session_id, artifact_type, artifact_id, version)
        )
        return value.model_copy(deep=True) if value is not None else None

    def list_versions(self, session_id, artifact_type, artifact_id=None):
        return [
            value.model_copy(deep=True)
            for key, value in self.artifacts.items()
            if key[0] == session_id
            and key[1] == artifact_type
            and (artifact_id is None or key[2] == artifact_id)
        ]

    def delete(self, session_id, artifact_type, artifact_id, version):
        self.artifacts.pop(
            self._key(session_id, artifact_type, artifact_id, version),
            None,
        )

    def save_checkpoint(self, checkpoint):
        self.checkpoints[checkpoint.run_id] = checkpoint.model_copy(deep=True)

    def get_checkpoint(self, run_id):
        value = self.checkpoints.get(run_id)
        return value.model_copy(deep=True) if value is not None else None

    @contextmanager
    def transaction(self):
        yield

    def save_resume_event(self, event):
        self.events.append(event)

    def get_resume_events(self, run_id):
        return [item for item in self.events if getattr(item, "run_id", None) == run_id]


def test_repository_boundary_saves_reads_lists_and_checkpoints() -> None:
    repository = LearningRepositoryFixture()
    store = PostgresArtifactStore(repository)
    scope = build_fastapi_crud_learning_fixture().scope

    ref = store.save_artifact("learning-session-22", scope)
    loaded = store.get_artifact("learning-session-22", ref)
    versions = store.list_versions(
        "learning-session-22",
        ref.artifact_type,
        ref.artifact_id,
    )
    checkpoint = LearningRunCheckpoint(
        runId="learning-session-22",
        currentStage="understanding",
        status="running",
        artifactRefs=[ref],
    )
    store.save_checkpoint(checkpoint)

    assert loaded == scope
    assert versions == [ref]
    assert store.get_checkpoint("learning-session-22") == checkpoint


def test_repository_rejects_shared_settings_namespace() -> None:
    repository = LearningRepositoryFixture()
    repository.repository_namespace = "ai_settings"

    with pytest.raises(ArtifactStoreError, match="dedicated learning_artifacts"):
        PostgresArtifactStore(repository)


def test_repository_rejects_incompatible_schema() -> None:
    repository = LearningRepositoryFixture()
    repository.schema_version = 2

    with pytest.raises(ArtifactStoreError, match="schema version is incompatible"):
        PostgresArtifactStore(repository)
