from __future__ import annotations

from copy import deepcopy

import pytest

from app.learning.runtime import (
    ArtifactStoreError,
    InMemoryArtifactStore,
    LearningRecoveryService,
    LearningRunCheckpoint,
    LearningRuntime,
    LearningRuntimeError,
    PostgresArtifactStore,
)
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


class RecordingArtifactRepository:
    def __init__(self):
        self.artifacts = {}
        self.checkpoints = {}
        self.insert_count = 0

    def insert_artifact(self, envelope):
        key = self._key(envelope)
        if key in self.artifacts:
            raise RuntimeError("repository version conflict")
        self.artifacts[key] = envelope.model_copy(deep=True)
        self.insert_count += 1

    def get_artifact(
        self,
        session_id,
        artifact_type,
        artifact_id,
        version,
    ):
        return self._copy(
            self.artifacts.get(
                (session_id, artifact_type, artifact_id, version),
            )
        )

    def list_artifacts(self, session_id, artifact_type, artifact_id=None):
        return [
            self._copy(value)
            for key, value in self.artifacts.items()
            if key[0] == session_id
            and key[1] == artifact_type
            and (artifact_id is None or key[2] == artifact_id)
        ]

    def delete_artifact(
        self,
        session_id,
        artifact_type,
        artifact_id,
        version,
    ):
        self.artifacts.pop(
            (session_id, artifact_type, artifact_id, version),
            None,
        )

    def save_checkpoint(self, checkpoint):
        self.checkpoints[checkpoint.run_id] = checkpoint.model_copy(deep=True)

    def get_checkpoint(self, run_id):
        return self._copy(self.checkpoints.get(run_id))

    @staticmethod
    def _key(envelope):
        return (
            envelope.session_id,
            envelope.artifact_type,
            envelope.artifact_id,
            envelope.version,
        )

    @staticmethod
    def _copy(value):
        if value is None:
            return None
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)
        return deepcopy(value)


def _persistent_store():
    repository = RecordingArtifactRepository()
    return repository, PostgresArtifactStore(repository)


def _runtime(store, *, model=None):
    fixture = build_fastapi_learning_pipeline_fixture()
    pipeline = LearningPipeline(
        provider=fixture.provider,
        model=model or fixture.model,
    )
    return fixture, LearningRuntime(
        pipeline,
        artifact_store=store,
        checkpoint_store=store,
    )


def test_persistent_store_saves_artifact_in_envelope() -> None:
    repository, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()

    ref = store.save_artifact("learning-run-save", fixture.scope)
    envelope = next(iter(repository.artifacts.values()))

    assert ref.artifact_type == "learning_scope"
    assert envelope.session_id == "learning-run-save"
    assert envelope.artifact_id == fixture.scope.artifact_id
    assert envelope.version == fixture.scope.version
    assert envelope.schema_version == fixture.scope.schema_version
    assert envelope.content["userGoal"] == fixture.scope.user_goal


def test_in_memory_store_remains_compatible_with_extended_interface() -> None:
    store = InMemoryArtifactStore()
    fixture = build_fastapi_learning_pipeline_fixture()
    run_id = "learning-run-memory"

    ref = store.save_artifact(run_id, fixture.scope)
    store.save_checkpoint(
        LearningRunCheckpoint(
            runId=run_id,
            currentStage="understanding",
            status="running",
            artifactRefs=[ref],
        )
    )

    assert store.exists(run_id, ref) is True
    assert store.list_versions(
        run_id,
        "learning_scope",
        fixture.scope.artifact_id,
    ) == [ref]
    assert store.get_latest_artifact(run_id, "learning_scope") == fixture.scope
    assert store.get_checkpoint(run_id) is not None


def test_persistent_store_reads_artifact_and_latest_artifact() -> None:
    _, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    ref = store.save_artifact("learning-run-read", fixture.scope)

    loaded = store.get_artifact("learning-run-read", ref)
    latest = store.get_latest_artifact(
        "learning-run-read",
        "learning_scope",
        fixture.scope.artifact_id,
    )

    assert loaded == fixture.scope
    assert latest == fixture.scope
    assert store.exists("learning-run-read", ref) is True


def test_persistent_store_lists_append_only_versions() -> None:
    _, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    version_two = fixture.scope.model_copy(
        update={"version": 2, "confirmed": not fixture.scope.confirmed}
    )

    store.save_artifact("learning-run-versions", fixture.scope)
    store.save_artifact("learning-run-versions", version_two)

    refs = store.list_versions(
        "learning-run-versions",
        "learning_scope",
        fixture.scope.artifact_id,
    )
    assert [item.version for item in refs] == [1, 2]
    assert (
        store.get_latest_artifact(
            "learning-run-versions",
            "learning_scope",
            fixture.scope.artifact_id,
        )
        == version_two
    )


def test_completed_runtime_checkpoint_recovers_valid_artifact_chain() -> None:
    _, store = _persistent_store()
    fixture, runtime = _runtime(store)

    completed = runtime.run(fixture.scope, session_id="learning-run-completed")
    recovered = LearningRecoveryService(store, store).recover(
        completed.session.session_id
    )

    assert recovered.status == "recovered"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.status == "completed"
    assert recovered.checkpoint.last_successful_stage == "quality_checking"
    assert set(recovered.artifacts) == set(completed.artifacts)


def test_interrupted_runtime_recovers_last_successful_artifact() -> None:
    _, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    responses = fastapi_pipeline_responses()
    responses[1]["capabilities"][0]["outcomeIndexes"] = [99]
    _, runtime = _runtime(store, model=ScriptedPipelineModel(responses))

    with pytest.raises(LearningRuntimeError) as caught:
        runtime.run(fixture.scope, session_id="learning-run-interrupted")

    recovered = LearningRecoveryService(store, store).recover(
        caught.value.session.session_id
    )
    assert recovered.status == "recovered"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.status == "failed"
    assert recovered.checkpoint.last_successful_stage == "understanding"
    assert set(recovered.artifacts) == {"learning_scope"}


def test_repeated_save_is_idempotent_for_same_run_and_version() -> None:
    repository, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()

    first = store.save_artifact("learning-run-idempotent", fixture.scope)
    second = store.save_artifact("learning-run-idempotent", fixture.scope)

    assert first == second
    assert repository.insert_count == 1
    assert len(repository.artifacts) == 1


def test_corrupt_artifact_is_rejected_and_removed_during_recovery() -> None:
    repository, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    run_id = "learning-run-corrupt"
    ref = store.save_artifact(run_id, fixture.scope)
    key = next(iter(repository.artifacts))
    corrupted = repository.artifacts[key].model_dump(mode="json", by_alias=True)
    corrupted["content"].pop("userGoal")
    repository.artifacts[key] = corrupted
    store.save_checkpoint(
        LearningRunCheckpoint(
            runId=run_id,
            currentStage="understanding",
            status="running",
            artifactRefs=[ref],
        )
    )

    recovered = LearningRecoveryService(store, store).recover(run_id)

    assert recovered.status == "failed"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.status == "failed"
    assert store.exists(run_id, ref) is False


def test_version_conflict_refuses_to_overwrite_content() -> None:
    _, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    changed = fixture.scope.model_copy(update={"user_goal": "different goal"})
    store.save_artifact("learning-run-conflict", fixture.scope)

    with pytest.raises(ArtifactStoreError, match="cannot be overwritten"):
        store.save_artifact("learning-run-conflict", changed)


def test_unsupported_schema_is_rejected_on_read() -> None:
    repository, store = _persistent_store()
    fixture = build_fastapi_learning_pipeline_fixture()
    run_id = "learning-run-schema"
    ref = store.save_artifact(run_id, fixture.scope)
    key = next(iter(repository.artifacts))
    wrong_schema = repository.artifacts[key].model_dump(mode="json", by_alias=True)
    wrong_schema["schemaVersion"] = 2
    wrong_schema["content"]["schemaVersion"] = 2
    repository.artifacts[key] = wrong_schema

    with pytest.raises(ArtifactStoreError, match="unsupported Learning artifact schema"):
        store.get_artifact(run_id, ref)
