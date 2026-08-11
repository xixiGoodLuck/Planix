from __future__ import annotations

from app.db import get_conn
from app.learning.runtime import (
    ArtifactBundle,
    LearningResumeCoordinator,
    LearningRecoveryService,
    LearningRunCheckpoint,
    LearningRuntime,
    LearningSessionState,
    LearningStageRegistry,
    PostgresArtifactStore,
    PostgresLearningArtifactRepository,
    ResumeCommitService,
)
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture
from test_learning_resume_commit import _complete_artifacts


def _runtime(repository):
    fixture = build_fastapi_learning_pipeline_fixture()
    store = PostgresArtifactStore(repository)
    return fixture, LearningRuntime(
        LearningPipeline(provider=fixture.provider, model=fixture.model),
        artifact_store=store,
        checkpoint_store=store,
    )


def test_second_repository_recovers_completed_run_artifacts_and_events() -> None:
    run_id = "learning-pg-cross-process"
    fixture, runtime_a = _runtime(PostgresLearningArtifactRepository())

    completed = runtime_a.run(fixture.scope, session_id=run_id)

    repository_b = PostgresLearningArtifactRepository()
    store_b = PostgresArtifactStore(repository_b)
    recovered = LearningRecoveryService(store_b, store_b).recover(run_id)
    _, runtime_b = _runtime(repository_b)

    assert recovered.status == "recovered"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.status == "completed"
    assert set(recovered.artifacts) == set(completed.artifacts)
    assert runtime_b.get_session(run_id).status == "completed"
    result = runtime_b.get_result(run_id)
    assert result is not None
    assert result.final_plan == completed.final_plan
    assert result.quality_report == completed.quality_report
    events = runtime_b.get_events(run_id)
    assert events[0].event_type == "session_created"
    assert events[-1].event_type == "session_completed"


def test_recovery_quarantines_schema_incompatible_artifact() -> None:
    run_id = "learning-pg-recovery-quarantine"
    fixture, runtime = _runtime(PostgresLearningArtifactRepository())
    runtime.create_session(run_id)
    ref = runtime.artifact_store.save_artifact(run_id, fixture.scope)
    runtime.checkpoint_store.save_checkpoint(
        runtime.checkpoint_store.get_checkpoint(run_id).model_copy(
            update={"artifact_refs": [ref], "current_stage": "understanding"}
        )
    )
    with get_conn() as connection:
        connection.execute(
            """
            UPDATE learning_artifacts SET schema_version = 2
            WHERE run_id = %s AND artifact_id = %s AND version = %s
            """,
            (run_id, ref.artifact_id, ref.version),
        )

    repository_b = PostgresLearningArtifactRepository()
    store_b = PostgresArtifactStore(repository_b)
    recovered = LearningRecoveryService(store_b, store_b).recover(run_id)

    assert recovered.status == "failed"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.status == "failed"
    with get_conn() as connection:
        row = connection.execute(
            """
            SELECT status FROM learning_artifacts
            WHERE run_id = %s AND artifact_id = %s AND version = %s
            """,
            (run_id, ref.artifact_id, ref.version),
        ).fetchone()
    assert row["status"] == "invalid"


def test_resume_commit_persists_artifact_checkpoint_and_full_audit_atomically() -> None:
    run_id = "learning-pg-resume-commit"
    artifacts = _complete_artifacts()
    repository = PostgresLearningArtifactRepository()
    store = PostgresArtifactStore(repository)
    repository.save_run(LearningSessionState(sessionId=run_id))
    refs = [
        store.save_artifact(run_id, artifacts[artifact_type])
        for artifact_type in (
            "learning_scope",
            "capability_graph",
            "knowledge_graph",
        )
    ]
    before = store.save_checkpoint(
        LearningRunCheckpoint(
            runId=run_id,
            currentStage="failed",
            status="failed",
            artifactRefs=refs,
            lastSuccessfulStage="knowledge_generation",
        )
    )
    registry = LearningStageRegistry.default(
        executors={
            "evidence_generation": lambda _context: ArtifactBundle(
                (artifacts["evidence_graph"],)
            )
        }
    )
    coordinator = LearningResumeCoordinator(store, store, registry=registry)

    result = ResumeCommitService(
        coordinator,
        store,
        registry=registry,
    ).execute(run_id)

    assert result.status == "completed"
    assert result.checkpoint_before == before
    assert result.checkpoint_after.checkpoint_version == before.checkpoint_version + 1
    events = store.get_resume_events(run_id)
    assert len(events) == 1
    assert events[0].checkpoint_before == before
    assert events[0].checkpoint_after == result.checkpoint_after
