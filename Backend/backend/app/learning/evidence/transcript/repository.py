from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any, ContextManager

from ...contracts import VideoResource
from ....db import get_conn, jsonb
from .registry_contracts import (
    NormalizedTranscriptSegment,
    TranscriptFormat,
    TranscriptSourceRecord,
    TranscriptSourceSummary,
    TranscriptSourceType,
)


class TranscriptRepositoryError(RuntimeError):
    pass


class TranscriptConflict(TranscriptRepositoryError):
    pass


class TranscriptSourceNotFound(TranscriptRepositoryError):
    pass


ConnectionFactory = Callable[[], ContextManager[Any]]


class LearningTranscriptRepository:
    """Psycopg-backed registry for verified video identity and normalized subtitles."""

    def __init__(self, connection_factory: ConnectionFactory = get_conn):
        self._connection_factory = connection_factory

    @staticmethod
    def source_id_for(
        resource_id: str,
        source_type: TranscriptSourceType,
        source_name: str,
    ) -> str:
        identity = f"{resource_id}|{source_type}|{source_name}".encode("utf-8")
        return f"learning-transcript-{sha256(identity).hexdigest()[:24]}"

    def health_check(self) -> bool:
        with self._connection_factory() as conn:
            rows = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (
                    [
                        "learning_video_resources",
                        "learning_transcript_sources",
                        "learning_transcript_segments",
                    ],
                ),
            ).fetchall()
        return {row["table_name"] for row in rows} == {
            "learning_video_resources",
            "learning_transcript_sources",
            "learning_transcript_segments",
        }

    def register(
        self,
        resource: VideoResource,
        *,
        source_type: TranscriptSourceType,
        source_format: TranscriptFormat,
        source_name: str,
        language: str,
        source_checksum: str,
        segments: list[NormalizedTranscriptSegment],
    ) -> TranscriptSourceRecord:
        if not segments:
            raise TranscriptRepositoryError("transcript must contain normalized segments")
        source_id = self.source_id_for(resource.id, source_type, source_name)
        with self._connection_factory() as conn:
            self._upsert_video_resource(conn, resource)
            self._mark_previous_fingerprints_stale(conn, resource)
            inserted = conn.execute(
                """
                INSERT INTO learning_transcript_sources(
                  id, resource_id, resource_fingerprint, source_type,
                  source_format, source_name, language, source_checksum,
                  authorization_status, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'authorized', 'active')
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    source_id,
                    resource.id,
                    resource.content_fingerprint,
                    source_type,
                    source_format,
                    source_name,
                    language,
                    source_checksum,
                ),
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    """
                    SELECT id, resource_fingerprint, source_checksum, status
                    FROM learning_transcript_sources
                    WHERE resource_id = %s AND source_checksum = %s
                    """,
                    (resource.id, source_checksum),
                ).fetchone()
                if existing is None:
                    existing = conn.execute(
                        """
                        SELECT id, resource_fingerprint, source_checksum, status
                        FROM learning_transcript_sources
                        WHERE resource_id = %s
                          AND source_type = %s
                          AND source_name = %s
                        """,
                        (resource.id, source_type, source_name),
                    ).fetchone()
                if existing is None:
                    raise TranscriptConflict("transcript source identity conflicts")
                if existing["source_checksum"] != source_checksum:
                    raise TranscriptConflict(
                        "the same transcript source name has different content"
                    )
                if existing["resource_fingerprint"] != resource.content_fingerprint:
                    raise TranscriptConflict(
                        "transcript checksum is already bound to another video fingerprint"
                    )
                if existing["status"] != "active":
                    raise TranscriptConflict(
                        f"transcript source is {existing['status']} and cannot be reused"
                    )
                if not self._segments_match(conn, existing["id"], segments):
                    raise TranscriptConflict(
                        "transcript source checksum does not match its normalized segments"
                    )
                source_id = existing["id"]
            else:
                for segment in segments:
                    self._insert_segment(conn, source_id, segment)
            return self._get_source(conn, source_id)

    def get_transcript(self, source_id: str) -> TranscriptSourceRecord | None:
        with self._connection_factory() as conn:
            return self._get_source(conn, source_id, required=False)

    def find_active_by_resource_fingerprint(
        self,
        resource_id: str,
        fingerprint: str,
    ) -> TranscriptSourceRecord | None:
        with self._connection_factory() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM learning_transcript_sources
                WHERE resource_id = %s
                  AND resource_fingerprint = %s
                  AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (resource_id, fingerprint),
            ).fetchone()
            return self._get_source(conn, row["id"]) if row is not None else None

    def list_active_sources(self, *, limit: int = 100) -> list[TranscriptSourceRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("active transcript source limit must be between 1 and 500")
        with self._connection_factory() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM learning_transcript_sources
                WHERE status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [self._get_source(conn, row["id"]) for row in rows]

    def get_source_metadata(self, source_id: str) -> TranscriptSourceSummary | None:
        with self._connection_factory() as conn:
            row = self._metadata_row(conn, source_id)
            return self._summary(row) if row is not None else None

    def list_source_metadata(self, resource_id: str) -> list[TranscriptSourceSummary]:
        with self._connection_factory() as conn:
            rows = conn.execute(
                self._metadata_query("WHERE source.resource_id = %s")
                + " ORDER BY source.created_at DESC, source.id DESC",
                (resource_id,),
            ).fetchall()
            return [self._summary(row) for row in rows]

    def mark_source_stale(self, source_id: str) -> bool:
        with self._connection_factory() as conn:
            row = conn.execute(
                """
                UPDATE learning_transcript_sources
                SET status = 'stale', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'active'
                RETURNING id
                """,
                (source_id,),
            ).fetchone()
            return row is not None

    def revoke_source(self, source_id: str) -> bool:
        with self._connection_factory() as conn:
            row = conn.execute(
                """
                UPDATE learning_transcript_sources
                SET status = 'revoked', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "DELETE FROM learning_transcript_segments WHERE source_id = %s",
                (source_id,),
            )
            return True

    @staticmethod
    def _upsert_video_resource(conn, resource: VideoResource) -> None:
        conn.execute(
            """
            INSERT INTO learning_video_resources(
              id, provider, external_id, canonical_url, title, author, language,
              duration_seconds, published_at, content_fingerprint,
              technology_versions_json, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider, external_id, content_fingerprint)
            DO UPDATE SET
              canonical_url = EXCLUDED.canonical_url,
              title = EXCLUDED.title,
              author = EXCLUDED.author,
              language = EXCLUDED.language,
              duration_seconds = EXCLUDED.duration_seconds,
              published_at = EXCLUDED.published_at,
              technology_versions_json = EXCLUDED.technology_versions_json,
              observed_at = EXCLUDED.observed_at,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                resource.id,
                resource.provider,
                resource.external_id,
                resource.canonical_url,
                resource.title,
                resource.author,
                resource.language,
                resource.duration_seconds,
                resource.published_at,
                resource.content_fingerprint,
                jsonb(resource.technology_versions),
                resource.observed_at,
            ),
        )

    @staticmethod
    def _mark_previous_fingerprints_stale(conn, resource: VideoResource) -> None:
        conn.execute(
            """
            UPDATE learning_transcript_sources AS source
            SET status = 'stale', updated_at = CURRENT_TIMESTAMP
            FROM learning_video_resources AS video
            WHERE source.resource_id = video.id
              AND source.resource_fingerprint = video.content_fingerprint
              AND video.provider = %s
              AND video.external_id = %s
              AND video.content_fingerprint <> %s
              AND source.status = 'active'
            """,
            (
                resource.provider,
                resource.external_id,
                resource.content_fingerprint,
            ),
        )

    @staticmethod
    def _insert_segment(conn, source_id: str, segment: NormalizedTranscriptSegment) -> None:
        conn.execute(
            """
            INSERT INTO learning_transcript_segments(
              source_id, segment_index, start_ms, end_ms, text, text_checksum
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                source_id,
                segment.segment_index,
                segment.start_ms,
                segment.end_ms,
                segment.text,
                segment.text_checksum,
            ),
        )

    @staticmethod
    def _segments_match(conn, source_id: str, segments: list[NormalizedTranscriptSegment]) -> bool:
        rows = conn.execute(
            """
            SELECT segment_index, start_ms, end_ms, text_checksum
            FROM learning_transcript_segments
            WHERE source_id = %s
            ORDER BY segment_index
            """,
            (source_id,),
        ).fetchall()
        expected = [
            {
                "segment_index": item.segment_index,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text_checksum": item.text_checksum,
            }
            for item in segments
        ]
        return [dict(row) for row in rows] == expected

    def _get_source(
        self,
        conn,
        source_id: str,
        *,
        required: bool = True,
    ) -> TranscriptSourceRecord | None:
        row = conn.execute(
            """
            SELECT
              source.id AS source_id,
              source.resource_id,
              source.resource_fingerprint,
              source.source_type,
              source.source_format,
              source.source_name,
              source.language AS source_language,
              source.source_checksum,
              source.authorization_status,
              source.status,
              source.created_at,
              source.updated_at,
              video.provider,
              video.external_id,
              video.canonical_url,
              video.title,
              video.author,
              video.language AS video_language,
              video.duration_seconds,
              video.published_at,
              video.technology_versions_json,
              video.observed_at
            FROM learning_transcript_sources AS source
            JOIN learning_video_resources AS video
              ON video.id = source.resource_id
             AND video.content_fingerprint = source.resource_fingerprint
            WHERE source.id = %s
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            if required:
                raise TranscriptSourceNotFound("transcript source does not exist")
            return None
        segment_rows = conn.execute(
            """
            SELECT segment_index, start_ms, end_ms, text, text_checksum
            FROM learning_transcript_segments
            WHERE source_id = %s
            ORDER BY segment_index
            """,
            (source_id,),
        ).fetchall()
        return TranscriptSourceRecord(
            sourceId=row["source_id"],
            resource=VideoResource(
                id=row["resource_id"],
                provider=row["provider"],
                externalId=row["external_id"],
                canonicalUrl=row["canonical_url"],
                title=row["title"],
                author=row["author"],
                language=row["video_language"],
                durationSeconds=row["duration_seconds"],
                publishedAt=row["published_at"],
                contentFingerprint=row["resource_fingerprint"],
                technologyVersions=row["technology_versions_json"],
                observedAt=row["observed_at"],
            ),
            sourceType=row["source_type"],
            sourceFormat=row["source_format"],
            sourceName=row["source_name"],
            language=row["source_language"],
            sourceChecksum=row["source_checksum"],
            authorizationStatus=row["authorization_status"],
            status=row["status"],
            segments=[NormalizedTranscriptSegment.model_validate(item) for item in segment_rows],
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )

    @classmethod
    def _metadata_query(cls, where: str) -> str:
        return f"""
            SELECT
              source.id AS source_id,
              source.resource_id,
              source.resource_fingerprint,
              source.source_type,
              source.source_format,
              source.source_name,
              source.language,
              source.source_checksum,
              source.authorization_status,
              source.status,
              source.created_at,
              video.provider,
              video.external_id,
              video.canonical_url,
              video.title,
              COUNT(segment.segment_index)::int AS segment_count,
              MIN(segment.start_ms)::bigint AS start_ms,
              MAX(segment.end_ms)::bigint AS end_ms
            FROM learning_transcript_sources AS source
            JOIN learning_video_resources AS video
              ON video.id = source.resource_id
             AND video.content_fingerprint = source.resource_fingerprint
            LEFT JOIN learning_transcript_segments AS segment
              ON segment.source_id = source.id
            {where}
            GROUP BY source.id, video.id, video.content_fingerprint
        """

    def _metadata_row(self, conn, source_id: str):
        return conn.execute(
            self._metadata_query("WHERE source.id = %s"),
            (source_id,),
        ).fetchone()

    @staticmethod
    def _summary(row) -> TranscriptSourceSummary:
        return TranscriptSourceSummary(
            sourceId=row["source_id"],
            resourceId=row["resource_id"],
            resourceFingerprint=row["resource_fingerprint"],
            provider=row["provider"],
            externalId=row["external_id"],
            canonicalUrl=row["canonical_url"],
            title=row["title"],
            sourceType=row["source_type"],
            sourceFormat=row["source_format"],
            sourceName=row["source_name"],
            language=row["language"],
            checksumPrefix=row["source_checksum"].removeprefix("sha256:")[:12],
            authorizationStatus=row["authorization_status"],
            status=row["status"],
            segmentCount=row["segment_count"],
            startMs=row["start_ms"],
            endMs=row["end_ms"],
            createdAt=row["created_at"],
        )


__all__ = [
    "LearningTranscriptRepository",
    "TranscriptConflict",
    "TranscriptRepositoryError",
    "TranscriptSourceNotFound",
]
