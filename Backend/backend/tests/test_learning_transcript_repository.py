from __future__ import annotations

from hashlib import sha256

import pytest

from app.db import ALEMBIC_REVISION, get_conn
from app.learning.contracts import VideoResource
from app.learning.evidence.providers import VideoSearchQuery
from app.learning.evidence.transcript import (
    LearningTranscriptRepository,
    NormalizedTranscriptSegment,
    PersistentTranscriptProvider,
    TranscriptConflict,
    TranscriptUnavailableError,
)


def transcript_resource(
    *,
    fingerprint: str = "sha256:phase24-video-v1",
) -> VideoResource:
    return VideoResource(
        id="video-bilibili-BV1xx411c7mD",
        provider="bilibili",
        externalId="BV1xx411c7mD",
        canonicalUrl="https://www.bilibili.com/video/BV1xx411c7mD",
        title="Verified FastAPI Routing",
        author="Verified Author",
        language="zh-CN",
        durationSeconds=180,
        publishedAt="2026-08-01T00:00:00Z",
        contentFingerprint=fingerprint,
        technologyVersions={"FastAPI": "0.116"},
    )


def normalized_segments(
    marker: str = "PLANIX_TRANSCRIPT_SECRET_728391",
) -> list[NormalizedTranscriptSegment]:
    texts = [marker, "FastAPI routing maps GET and POST requests to handlers."]
    return [
        NormalizedTranscriptSegment(
            segmentIndex=index,
            startMs=index * 5000,
            endMs=(index + 1) * 5000,
            text=text,
            textChecksum="sha256:" + sha256(text.encode("utf-8")).hexdigest(),
        )
        for index, text in enumerate(texts)
    ]


def register_source(
    repository: LearningTranscriptRepository,
    *,
    resource: VideoResource | None = None,
    source_name: str = "routing.srt",
    marker: str = "PLANIX_TRANSCRIPT_SECRET_728391",
):
    body = marker + "\nFastAPI routing maps GET and POST requests to handlers."
    return repository.register(
        resource or transcript_resource(),
        source_type="srt_vtt",
        source_format="srt",
        source_name=source_name,
        language="zh-CN",
        source_checksum="sha256:" + sha256(body.encode("utf-8")).hexdigest(),
        segments=normalized_segments(marker),
    )


def test_transcript_schema_is_current_and_separate_from_pure_v2() -> None:
    with get_conn() as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row["table_name"]
            for row in conn.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'learning_transcript%'
                """
            ).fetchall()
        }
    assert revision["version_num"] == ALEMBIC_REVISION == "20260812_01"
    assert tables == {
        "learning_transcript_sources",
        "learning_transcript_segments",
    }


def test_register_read_provider_idempotency_and_revoke_purge() -> None:
    repository = LearningTranscriptRepository()
    first = register_source(repository)
    second = register_source(repository)

    assert first.source_id == second.source_id
    assert len(first.segments) == 2
    document = PersistentTranscriptProvider(repository).fetch_transcript(
        transcript_resource()
    )
    assert document.fingerprint == transcript_resource().content_fingerprint
    assert document.source_metadata is not None
    assert document.source_metadata.source_id == first.source_id

    assert repository.revoke_source(first.source_id) is True
    metadata = repository.get_source_metadata(first.source_id)
    assert metadata is not None
    assert metadata.status == "revoked"
    assert metadata.segment_count == 0
    with pytest.raises(TranscriptUnavailableError, match="TRANSCRIPT_UNAVAILABLE"):
        PersistentTranscriptProvider(repository).fetch_transcript(
            transcript_resource()
        )


def test_active_registered_transcript_resources_are_searchable_before_remote_candidates() -> None:
    repository = LearningTranscriptRepository()
    source = register_source(repository)
    provider = PersistentTranscriptProvider(repository)

    hits = provider.search_registered(
        VideoSearchQuery(
            knowledgeTerms=["FastAPI Routing GET POST"],
            maximumResults=5,
        )
    )

    assert [item.external_id for item in hits] == [transcript_resource().external_id]
    assert repository.list_active_sources()[0].source_id == source.source_id
    assert provider.search_registered(
        VideoSearchQuery(knowledgeTerms=["Rust ownership"], maximumResults=5)
    ) == []


def test_same_source_name_with_different_content_conflicts() -> None:
    repository = LearningTranscriptRepository()
    register_source(repository)

    with pytest.raises(TranscriptConflict, match="different content"):
        register_source(repository, marker="different authorized text")


def test_segment_failure_rolls_back_video_source_and_segments(monkeypatch) -> None:
    repository = LearningTranscriptRepository()
    original = repository._insert_segment
    calls = 0

    def fail_second(conn, source_id, segment):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected segment failure")
        original(conn, source_id, segment)

    monkeypatch.setattr(repository, "_insert_segment", fail_second)
    with pytest.raises(RuntimeError, match="injected segment failure"):
        register_source(repository)

    with get_conn() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM learning_video_resources) AS resources,
              (SELECT COUNT(*) FROM learning_transcript_sources) AS sources,
              (SELECT COUNT(*) FROM learning_transcript_segments) AS segments
            """
        ).fetchone()
    assert counts == {"resources": 0, "sources": 0, "segments": 0}


def test_new_video_fingerprint_marks_old_source_stale() -> None:
    repository = LearningTranscriptRepository()
    old = register_source(repository)
    updated = transcript_resource(fingerprint="sha256:phase24-video-v2")
    new = register_source(
        repository,
        resource=updated,
        source_name="routing-v2.srt",
        marker="updated authorized transcript",
    )

    assert repository.get_source_metadata(old.source_id).status == "stale"
    assert repository.get_source_metadata(new.source_id).status == "active"
    with pytest.raises(TranscriptUnavailableError):
        PersistentTranscriptProvider(repository).fetch_transcript(
            transcript_resource()
        )
    assert PersistentTranscriptProvider(repository).fetch_transcript(
        updated
    ).fingerprint == updated.content_fingerprint
