from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import get_conn
from app.learning.runtime import (
    ArtifactStoreError,
    ArtifactVersionConflict,
    LearningCheckpointConflict,
    LearningRunCheckpoint,
    LearningSessionState,
    PostgresArtifactStore,
    PostgresLearningArtifactRepository,
    ResumeEvent,
)

from learning_fixtures import build_fastapi_crud_learning_fixture


def _create_run(run_id: str) -> None:
    PostgresLearningArtifactRepository().save_run(
        LearningSessionState(sessionId=run_id)
    )


def test_ten_same_content_writers_are_idempotent() -> None:
    run_id = "learning-pg-concurrent-idempotent"
    _create_run(run_id)
    scope = build_fastapi_crud_learning_fixture().scope

    def save_once(_index):
        return PostgresArtifactStore(
            PostgresLearningArtifactRepository()
        ).save_artifact(run_id, scope)

    with ThreadPoolExecutor(max_workers=10) as executor:
        refs = list(executor.map(save_once, range(10)))

    assert len(set((ref.artifact_id, ref.version) for ref in refs)) == 1
    with get_conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM learning_artifacts WHERE run_id = %s",
            (run_id,),
        ).fetchone()["count"]
    assert count == 1


def test_different_content_same_version_has_one_winner_and_explicit_conflict() -> None:
    run_id = "learning-pg-concurrent-conflict"
    _create_run(run_id)
    scope = build_fastapi_crud_learning_fixture().scope
    changed = scope.model_copy(update={"user_goal": "competing content"})

    def save(candidate):
        try:
            PostgresArtifactStore(
                PostgresLearningArtifactRepository()
            ).save_artifact(run_id, candidate)
            return "saved"
        except ArtifactVersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, (scope, changed)))

    assert sorted(outcomes) == ["conflict", "saved"]


def test_checkpoint_compare_and_swap_allows_one_writer() -> None:
    run_id = "learning-pg-checkpoint-cas"
    _create_run(run_id)
    repository = PostgresLearningArtifactRepository()
    initial = repository.save_checkpoint(
        LearningRunCheckpoint(
            runId=run_id,
            currentStage="created",
            status="created",
        ),
        expected_version=0,
    )
    assert initial.checkpoint_version == 1

    def advance(stage):
        candidate = initial.model_copy(
            update={"current_stage": stage, "status": "running"}
        )
        try:
            return PostgresLearningArtifactRepository().save_checkpoint(
                candidate,
                expected_version=1,
            ).checkpoint_version
        except LearningCheckpointConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(advance, ("understanding", "knowledge_generating"))
        )

    assert sorted(outcomes, key=str) == [2, "conflict"]
    assert repository.get_checkpoint(run_id).checkpoint_version == 2


class _FailingRepository(PostgresLearningArtifactRepository):
    def __init__(self, failure: str):
        super().__init__()
        self.failure = failure

    def save_checkpoint(self, checkpoint, *, expected_version=None):
        if self.failure == "checkpoint":
            raise RuntimeError("injected checkpoint failure")
        return super().save_checkpoint(
            checkpoint,
            expected_version=expected_version,
        )

    def save_resume_event(self, event):
        if self.failure == "audit":
            raise RuntimeError("injected audit failure")
        return super().save_resume_event(event)


@pytest.mark.parametrize("failure", ["checkpoint", "audit"])
def test_atomic_boundary_rolls_back_artifact_checkpoint_and_audit(failure) -> None:
    run_id = f"learning-pg-atomic-{failure}"
    _create_run(run_id)
    repository = _FailingRepository(failure)
    store = PostgresArtifactStore(repository)
    scope = build_fastapi_crud_learning_fixture().scope

    with pytest.raises(ArtifactStoreError):
        with store.atomic():
            ref = store.save_artifact(run_id, scope)
            checkpoint = store.save_checkpoint(
                LearningRunCheckpoint(
                    runId=run_id,
                    currentStage="understanding",
                    status="running",
                    artifactRefs=[ref],
                )
            )
            store.save_resume_event(
                ResumeEvent(
                    runId=run_id,
                    previousStage=None,
                    resumeStage="scope",
                    reason="atomic persistence test",
                    artifactRefs=[ref],
                    checkpointAfter=checkpoint,
                )
            )

    with get_conn() as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM learning_artifacts WHERE run_id = %s) AS artifacts,
              (SELECT COUNT(*) FROM learning_checkpoints WHERE run_id = %s) AS checkpoints,
              (SELECT COUNT(*) FROM learning_resume_events WHERE run_id = %s) AS events
            """,
            (run_id, run_id, run_id),
        ).fetchone()
    assert counts == {"artifacts": 0, "checkpoints": 0, "events": 0}
