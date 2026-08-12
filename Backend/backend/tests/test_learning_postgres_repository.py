from __future__ import annotations

import inspect

import pytest

from app.db import ALEMBIC_REVISION, get_conn
from app.learning.runtime import (
    ArtifactVersionConflict,
    LearningRunCheckpoint,
    LearningSessionState,
    PostgresArtifactStore,
    PostgresLearningArtifactRepository,
)

from learning_fixtures import build_fastapi_crud_learning_fixture


def _store(run_id: str):
    repository = PostgresLearningArtifactRepository()
    store = PostgresArtifactStore(repository)
    repository.save_run(LearningSessionState(sessionId=run_id))
    return repository, store


def test_learning_schema_is_postgresql17_current_and_isolated() -> None:
    with get_conn() as connection:
        server_version = int(
            connection.execute("SHOW server_version_num").fetchone()["server_version_num"]
        )
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()["version_num"]
        tables = {
            row["table_name"]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name LIKE 'learning_%'
                """
            ).fetchall()
        }
    assert 170000 <= server_version < 180000
    assert revision == ALEMBIC_REVISION == "20260812_02"
    assert tables == {
        "learning_runs",
        "learning_artifacts",
        "learning_checkpoints",
        "learning_resume_events",
        "learning_video_resources",
        "learning_transcript_sources",
        "learning_transcript_segments",
    }
    source = inspect.getsource(PostgresLearningArtifactRepository)
    assert "ai_settings" not in source


def test_repository_saves_reads_versions_and_health() -> None:
    run_id = "learning-pg-save"
    repository, store = _store(run_id)
    scope = build_fastapi_crud_learning_fixture().scope
    version_two = scope.model_copy(update={"version": 2, "confirmed": False})

    first = store.save_artifact(run_id, scope)
    second = store.save_artifact(run_id, version_two)

    assert repository.health_check() is True
    assert store.get_artifact(run_id, first) == scope
    assert store.get_latest_artifact(run_id, "learning_scope") == version_two
    assert store.list_versions(run_id, "learning_scope", scope.artifact_id) == [
        first,
        second,
    ]


def test_artifact_is_idempotent_and_immutable() -> None:
    run_id = "learning-pg-immutable"
    _, store = _store(run_id)
    scope = build_fastapi_crud_learning_fixture().scope

    assert store.save_artifact(run_id, scope) == store.save_artifact(run_id, scope)
    changed = scope.model_copy(update={"user_goal": "different immutable content"})
    with pytest.raises(ArtifactVersionConflict):
        store.save_artifact(run_id, changed)

    with get_conn() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM learning_artifacts
            WHERE run_id = %s AND artifact_type = 'learning_scope'
            """,
            (run_id,),
        ).fetchone()["count"]
    assert count == 1


def test_invalid_artifact_is_quarantined_not_deleted() -> None:
    run_id = "learning-pg-quarantine"
    _, store = _store(run_id)
    scope = build_fastapi_crud_learning_fixture().scope
    ref = store.save_artifact(run_id, scope)
    with get_conn() as connection:
        connection.execute(
            """
            UPDATE learning_artifacts
            SET content_json = content_json - 'userGoal'
            WHERE run_id = %s AND artifact_id = %s AND version = %s
            """,
            (run_id, ref.artifact_id, ref.version),
        )

    assert store.delete_if_invalid(run_id, ref) is True
    assert store.get_artifact(run_id, ref) is None
    with get_conn() as connection:
        row = connection.execute(
            """
            SELECT status, invalidated_at, invalid_reason
            FROM learning_artifacts
            WHERE run_id = %s AND artifact_id = %s AND version = %s
            """,
            (run_id, ref.artifact_id, ref.version),
        ).fetchone()
    assert row["status"] == "invalid"
    assert row["invalidated_at"] is not None
    assert row["invalid_reason"]


def test_learning_persistence_does_not_store_process_secret(monkeypatch) -> None:
    secret = "PLANIX_LEARNING_REPOSITORY_SECRET_483921"
    monkeypatch.setenv("PLANIX_LEARNING_TEST_SECRET", secret)
    run_id = "learning-pg-secret-safety"
    repository, store = _store(run_id)
    scope = build_fastapi_crud_learning_fixture().scope
    ref = store.save_artifact(run_id, scope)
    repository.save_checkpoint(
        LearningRunCheckpoint(
            runId=run_id,
            currentStage="understanding",
            status="running",
            artifactRefs=[ref],
        )
    )

    with get_conn() as connection:
        occurrences = connection.execute(
            """
            SELECT COUNT(*) AS count FROM (
                SELECT run_id || run_fingerprint || completed_stages_json::text ||
                       COALESCE(error_json::text, '') AS body FROM learning_runs
                UNION ALL
                SELECT run_id || artifact_type || artifact_id || content_json::text
                    FROM learning_artifacts
                UNION ALL
                SELECT run_id || artifact_refs_json::text FROM learning_checkpoints
                UNION ALL
                SELECT run_id || event_type || message || reason || artifact_refs_json::text
                    FROM learning_resume_events
            ) AS persisted
            WHERE body LIKE %s
            """,
            (f"%{secret}%",),
        ).fetchone()["count"]
    assert occurrences == 0
