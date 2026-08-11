from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from ....db import ALEMBIC_REVISION, get_conn, jsonb
from ...contracts import LearningArtifactType
from ..artifact_store import (
    ArtifactVersionConflict,
    LearningCheckpointConflict,
    SUPPORTED_LEARNING_SCHEMA_VERSION,
    artifact_content_hash,
)
from ..contracts import (
    LearningArtifactEnvelope,
    LearningProgressEvent,
    LearningRunCheckpoint,
    LearningSessionState,
)
from .postgres_store import LEARNING_ARTIFACT_REPOSITORY_NAMESPACE


ConnectionFactory = Callable[[], Any]
_LEARNING_TABLES = {
    "learning_runs",
    "learning_artifacts",
    "learning_checkpoints",
    "learning_resume_events",
}


def _model_json(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


class PostgresLearningArtifactRepository:
    """Psycopg 3 repository for the isolated Learning namespace.

    The repository reuses Planix's existing connection pool.  A surrounding
    ``transaction()`` pins every operation to the same connection so artifact,
    checkpoint, and audit writes commit or roll back together.
    """

    repository_namespace = LEARNING_ARTIFACT_REPOSITORY_NAMESPACE
    schema_version = SUPPORTED_LEARNING_SCHEMA_VERSION

    def __init__(self, connection_factory: ConnectionFactory = get_conn):
        self._connection_factory = connection_factory
        self._active_connection: ContextVar[Any | None] = ContextVar(
            f"learning_repository_connection_{id(self)}",
            default=None,
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._active_connection.get() is not None:
            yield
            return
        with self._connection_factory() as connection:
            token = self._active_connection.set(connection)
            try:
                yield
            finally:
                self._active_connection.reset(token)

    @contextmanager
    def _connection(self):
        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        with self._connection_factory() as connection:
            yield connection

    def health_check(self) -> bool:
        with self._connection() as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (sorted(_LEARNING_TABLES),),
            ).fetchall()
            connection.execute("SELECT 1").fetchone()
        return bool(
            revision
            and revision["version_num"] == ALEMBIC_REVISION
            and {row["table_name"] for row in rows} == _LEARNING_TABLES
        )

    def save_run(
        self,
        state: LearningSessionState,
        *,
        run_fingerprint: str = "",
    ) -> None:
        current_ref = (
            _model_json(state.current_artifact_ref)
            if state.current_artifact_ref is not None
            else None
        )
        error = _model_json(state.error) if state.error is not None else None
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO learning_runs(
                    run_id, run_fingerprint, status, current_stage,
                    completed_stages_json, current_artifact_ref_json, error_json,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    run_fingerprint = CASE
                        WHEN EXCLUDED.run_fingerprint <> '' THEN EXCLUDED.run_fingerprint
                        ELSE learning_runs.run_fingerprint
                    END,
                    status = EXCLUDED.status,
                    current_stage = EXCLUDED.current_stage,
                    completed_stages_json = EXCLUDED.completed_stages_json,
                    current_artifact_ref_json = EXCLUDED.current_artifact_ref_json,
                    error_json = EXCLUDED.error_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    state.session_id,
                    run_fingerprint,
                    state.status,
                    state.current_stage,
                    jsonb(list(state.completed_stages)),
                    jsonb(current_ref) if current_ref is not None else None,
                    jsonb(error) if error is not None else None,
                    state.created_at,
                    state.updated_at,
                ),
            )

    def get_run(self, run_id: str) -> LearningSessionState | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, status, current_stage, completed_stages_json,
                       current_artifact_ref_json, error_json, created_at, updated_at
                FROM learning_runs
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return LearningSessionState.model_validate(
            {
                "sessionId": row["run_id"],
                "status": row["status"],
                "currentStage": row["current_stage"],
                "completedStages": row["completed_stages_json"],
                "currentArtifactRef": row["current_artifact_ref_json"],
                "error": row["error_json"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )

    def save(self, envelope: LearningArtifactEnvelope) -> None:
        content_hash = artifact_content_hash(envelope)
        with self._connection() as connection:
            inserted = connection.execute(
                """
                INSERT INTO learning_artifacts(
                    row_id, run_id, artifact_type, artifact_id, version,
                    schema_version, content_hash, content_json, status, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'valid', %s)
                ON CONFLICT (run_id, artifact_type, artifact_id, version)
                DO NOTHING
                RETURNING content_hash
                """,
                (
                    f"learning-artifact-row-{uuid4()}",
                    envelope.session_id,
                    envelope.artifact_type,
                    envelope.artifact_id,
                    envelope.version,
                    envelope.schema_version,
                    content_hash,
                    jsonb(envelope.content),
                    envelope.created_at,
                ),
            ).fetchone()
            if inserted is not None:
                return
            existing = connection.execute(
                """
                SELECT content_hash, status
                FROM learning_artifacts
                WHERE run_id = %s AND artifact_type = %s
                  AND artifact_id = %s AND version = %s
                """,
                (
                    envelope.session_id,
                    envelope.artifact_type,
                    envelope.artifact_id,
                    envelope.version,
                ),
            ).fetchone()
        if (
            existing is None
            or existing["content_hash"] != content_hash
            or existing["status"] != "valid"
        ):
            raise ArtifactVersionConflict(
                "an artifact id/version cannot be overwritten with different content"
            )

    def get(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
        version: int,
    ) -> LearningArtifactEnvelope | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, artifact_type, artifact_id, version, schema_version,
                       content_json, created_at
                FROM learning_artifacts
                WHERE run_id = %s AND artifact_type = %s
                  AND artifact_id = %s AND version = %s AND status = 'valid'
                """,
                (session_id, artifact_type, artifact_id, version),
            ).fetchone()
        return self._envelope(row) if row is not None else None

    def list_versions(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str | None = None,
    ) -> list[LearningArtifactEnvelope]:
        params: list[Any] = [session_id, artifact_type]
        artifact_filter = ""
        if artifact_id is not None:
            artifact_filter = " AND artifact_id = %s"
            params.append(artifact_id)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT run_id, artifact_type, artifact_id, version, schema_version,
                       content_json, created_at
                FROM learning_artifacts
                WHERE run_id = %s AND artifact_type = %s AND status = 'valid'
                {artifact_filter}
                ORDER BY version, created_at
                """,
                tuple(params),
            ).fetchall()
        return [self._envelope(row) for row in rows]

    def delete(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
        version: int,
    ) -> None:
        self.invalidate(
            session_id,
            artifact_type,
            artifact_id,
            version,
            reason="artifact validation failed",
        )

    def invalidate(
        self,
        session_id: str,
        artifact_type: LearningArtifactType,
        artifact_id: str,
        version: int,
        *,
        reason: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE learning_artifacts
                SET status = 'invalid', invalidated_at = CURRENT_TIMESTAMP,
                    invalid_reason = %s
                WHERE run_id = %s AND artifact_type = %s
                  AND artifact_id = %s AND version = %s AND status = 'valid'
                """,
                (reason[:500], session_id, artifact_type, artifact_id, version),
            )

    def save_checkpoint(
        self,
        checkpoint: LearningRunCheckpoint,
        *,
        expected_version: int | None = None,
    ) -> LearningRunCheckpoint:
        refs = [_model_json(item) for item in checkpoint.artifact_refs]
        values = (
            checkpoint.current_stage,
            checkpoint.status,
            jsonb(refs),
            checkpoint.last_successful_stage,
            checkpoint.schema_version,
            checkpoint.updated_at,
        )
        with self._connection() as connection:
            if expected_version is None:
                row = connection.execute(
                    """
                    INSERT INTO learning_checkpoints(
                        run_id, checkpoint_version, current_stage, status,
                        artifact_refs_json, last_successful_stage, schema_version,
                        updated_at
                    ) VALUES (%s, 1, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        checkpoint_version = learning_checkpoints.checkpoint_version + 1,
                        current_stage = EXCLUDED.current_stage,
                        status = EXCLUDED.status,
                        artifact_refs_json = EXCLUDED.artifact_refs_json,
                        last_successful_stage = EXCLUDED.last_successful_stage,
                        schema_version = EXCLUDED.schema_version,
                        updated_at = EXCLUDED.updated_at
                    RETURNING checkpoint_version
                    """,
                    (checkpoint.run_id, *values),
                ).fetchone()
            elif expected_version == 0:
                row = connection.execute(
                    """
                    INSERT INTO learning_checkpoints(
                        run_id, checkpoint_version, current_stage, status,
                        artifact_refs_json, last_successful_stage, schema_version,
                        updated_at
                    ) VALUES (%s, 1, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    RETURNING checkpoint_version
                    """,
                    (checkpoint.run_id, *values),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE learning_checkpoints
                    SET checkpoint_version = checkpoint_version + 1,
                        current_stage = %s,
                        status = %s,
                        artifact_refs_json = %s,
                        last_successful_stage = %s,
                        schema_version = %s,
                        updated_at = %s
                    WHERE run_id = %s AND checkpoint_version = %s
                    RETURNING checkpoint_version
                    """,
                    (*values, checkpoint.run_id, expected_version),
                ).fetchone()
        if row is None:
            current = self.get_checkpoint(checkpoint.run_id)
            current_version = current.checkpoint_version if current is not None else 0
            raise LearningCheckpointConflict(
                f"stale Learning checkpoint: expected {expected_version}, current {current_version}"
            )
        stored = self.get_checkpoint(checkpoint.run_id)
        if stored is None:
            raise LearningCheckpointConflict(
                "Learning checkpoint was not readable after save"
            )
        return stored

    def get_checkpoint(self, run_id: str) -> LearningRunCheckpoint | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, checkpoint_version, current_stage, status,
                       artifact_refs_json, last_successful_stage, schema_version,
                       updated_at
                FROM learning_checkpoints
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return LearningRunCheckpoint.model_validate(
            {
                "runId": row["run_id"],
                "checkpointVersion": row["checkpoint_version"],
                "currentStage": row["current_stage"],
                "status": row["status"],
                "artifactRefs": row["artifact_refs_json"],
                "lastSuccessfulStage": row["last_successful_stage"],
                "schemaVersion": row["schema_version"],
                "updatedAt": row["updated_at"],
            }
        )

    def save_progress_event(
        self,
        run_id: str,
        event: LearningProgressEvent,
    ) -> None:
        self._save_event(
            event_id=f"learning-progress-{uuid4()}",
            run_id=run_id,
            event_type=event.event_type,
            stage=event.stage,
            status=event.status,
            message=event.message,
            created_at=event.timestamp,
        )

    def get_progress_events(self, run_id: str) -> list[LearningProgressEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event_type, stage, status, message, created_at
                FROM learning_resume_events
                WHERE run_id = %s AND stage IS NOT NULL AND status IS NOT NULL
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [
            LearningProgressEvent.model_validate(
                {
                    "eventType": row["event_type"],
                    "stage": row["stage"],
                    "status": row["status"],
                    "message": row["message"],
                    "timestamp": row["created_at"],
                }
            )
            for row in rows
        ]

    def save_resume_event(self, event: Any) -> None:
        self._save_event(
            event_id=str(event.event_id),
            run_id=str(event.run_id),
            event_type="resume_commit",
            previous_stage=getattr(event, "previous_stage", None),
            resume_stage=getattr(event, "resume_stage", None),
            reason=str(getattr(event, "reason", "")),
            artifact_refs=[
                _model_json(item) for item in getattr(event, "artifact_refs", [])
            ],
            checkpoint_before=(
                _model_json(event.checkpoint_before)
                if getattr(event, "checkpoint_before", None) is not None
                else None
            ),
            checkpoint_after=(
                _model_json(event.checkpoint_after)
                if getattr(event, "checkpoint_after", None) is not None
                else None
            ),
            created_at=str(getattr(event, "timestamp", "")),
        )

    def get_resume_events(self, run_id: str) -> list[Any]:
        from ..resume.resume_coordinator import ResumeEvent

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event_id, run_id, previous_stage, resume_stage, reason,
                       artifact_refs_json, checkpoint_before_json,
                       checkpoint_after_json, created_at
                FROM learning_resume_events
                WHERE run_id = %s AND event_type = 'resume_commit'
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [
            ResumeEvent.model_validate(
                {
                    "eventId": row["event_id"],
                    "runId": row["run_id"],
                    "previousStage": row["previous_stage"],
                    "resumeStage": row["resume_stage"],
                    "reason": row["reason"],
                    "artifactRefs": row["artifact_refs_json"],
                    "checkpointBefore": row["checkpoint_before_json"],
                    "checkpointAfter": row["checkpoint_after_json"],
                    "timestamp": row["created_at"],
                }
            )
            for row in rows
        ]

    def _save_event(
        self,
        *,
        event_id: str,
        run_id: str,
        event_type: str,
        stage: str | None = None,
        status: str | None = None,
        message: str = "",
        previous_stage: str | None = None,
        resume_stage: str | None = None,
        reason: str = "",
        artifact_refs: list[dict[str, Any]] | None = None,
        checkpoint_before: dict[str, Any] | None = None,
        checkpoint_after: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (run_id,),
            )
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM learning_resume_events
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()["next_sequence"]
            connection.execute(
                """
                INSERT INTO learning_resume_events(
                    event_id, run_id, sequence, event_type, stage, status,
                    message, previous_stage, resume_stage, reason,
                    artifact_refs_json, checkpoint_before_json,
                    checkpoint_after_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    COALESCE(NULLIF(%s, '')::timestamptz, CURRENT_TIMESTAMP))
                """,
                (
                    event_id,
                    run_id,
                    sequence,
                    event_type,
                    stage,
                    status,
                    message,
                    previous_stage,
                    resume_stage,
                    reason,
                    jsonb(artifact_refs or []),
                    jsonb(checkpoint_before) if checkpoint_before is not None else None,
                    jsonb(checkpoint_after) if checkpoint_after is not None else None,
                    created_at,
                ),
            )

    @staticmethod
    def _envelope(row: dict[str, Any]) -> LearningArtifactEnvelope:
        return LearningArtifactEnvelope.model_validate(
            {
                "artifactType": row["artifact_type"],
                "artifactId": row["artifact_id"],
                "version": row["version"],
                "sessionId": row["run_id"],
                "schemaVersion": row["schema_version"],
                "createdAt": row["created_at"],
                "content": row["content_json"],
            }
        )


__all__ = ["PostgresLearningArtifactRepository"]
