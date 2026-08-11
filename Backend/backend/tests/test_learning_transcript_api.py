from __future__ import annotations

from app.db import get_conn
from app.learning.evidence.transcript import (
    LearningTranscriptRegistrationService,
    LearningTranscriptRepository,
)
from app.main import app
from app.routers.learning import get_learning_transcript_service

from test_learning_transcript_repository import transcript_resource


SRT_SECRET = """1
00:00:01,000 --> 00:00:04,000
PLANIX_TRANSCRIPT_SECRET_728391

2
00:00:05,000 --> 00:00:09,000
FastAPI routing maps requests to handlers.
"""

VTT = """WEBVTT

00:01.000 --> 00:04.000
FastAPI routing introduction.

00:05.000 --> 00:09.000
GET and POST route implementation.
"""


class ControlledBilibiliMetadataAdapter:
    def resolve_url(self, value):
        if value != transcript_resource().canonical_url:
            from app.learning.evidence.providers import VideoSourceProviderError

            raise VideoSourceProviderError("video URL must be one verified Bilibili URL")
        return transcript_resource()


def _service(*, maximum_bytes=512 * 1024):
    return LearningTranscriptRegistrationService(
        ControlledBilibiliMetadataAdapter(),
        LearningTranscriptRepository(),
        maximum_bytes=maximum_bytes,
    )


def _payload(content=SRT_SECRET, **updates):
    payload = {
        "videoUrl": transcript_resource().canonical_url,
        "format": "srt",
        "language": "zh-CN",
        "sourceName": "authorized-routing.srt",
        "content": content,
    }
    payload.update(updates)
    return payload


def test_register_get_and_revoke_expose_metadata_only_and_isolate_secret(client, caplog) -> None:
    app.dependency_overrides[get_learning_transcript_service] = _service
    try:
        created = client.post("/api/learning/transcripts", json=_payload())
        assert created.status_code == 201
        source_id = created.json()["source_id"]
        assert "content" not in created.text
        assert "PLANIX_TRANSCRIPT_SECRET_728391" not in created.text

        fetched = client.get(f"/api/learning/transcripts/{source_id}")
        assert fetched.status_code == 200
        assert fetched.json()["segment_count"] == 2
        assert "PLANIX_TRANSCRIPT_SECRET_728391" not in fetched.text

        with get_conn() as conn:
            secret_counts = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM learning_transcript_segments WHERE text LIKE %s) AS transcript_segments,
                  (SELECT COUNT(*) FROM learning_runs WHERE row_to_json(learning_runs)::text LIKE %s) AS runs,
                  (SELECT COUNT(*) FROM learning_artifacts WHERE content_json::text LIKE %s) AS artifacts,
                  (SELECT COUNT(*) FROM learning_checkpoints WHERE row_to_json(learning_checkpoints)::text LIKE %s) AS checkpoints,
                  (SELECT COUNT(*) FROM learning_resume_events WHERE row_to_json(learning_resume_events)::text LIKE %s) AS events,
                  (SELECT COUNT(*) FROM ai_settings WHERE row_to_json(ai_settings)::text LIKE %s) AS ai_settings
                """,
                tuple(["%PLANIX_TRANSCRIPT_SECRET_728391%"] * 6),
            ).fetchone()
        assert secret_counts == {
            "transcript_segments": 1,
            "runs": 0,
            "artifacts": 0,
            "checkpoints": 0,
            "events": 0,
            "ai_settings": 0,
        }
        assert "PLANIX_TRANSCRIPT_SECRET_728391" not in caplog.text

        revoked = client.delete(f"/api/learning/transcripts/{source_id}")
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        with get_conn() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM learning_transcript_segments
                WHERE text LIKE %s
                """,
                ("%PLANIX_TRANSCRIPT_SECRET_728391%",),
            ).fetchone()
        assert count["count"] == 0
    finally:
        app.dependency_overrides.pop(get_learning_transcript_service, None)


def test_vtt_registration_and_safe_validation_errors(client) -> None:
    app.dependency_overrides[get_learning_transcript_service] = _service
    try:
        created = client.post(
            "/api/learning/transcripts",
            json=_payload(
                VTT,
                format="vtt",
                sourceName="authorized-routing.vtt",
            ),
        )
        assert created.status_code == 201
        assert created.json()["source_format"] == "vtt"

        invalid = client.post(
            "/api/learning/transcripts",
            json=_payload(
                "PLANIX_TRANSCRIPT_SECRET_728391",
                format="txt",
            ),
        )
        assert invalid.status_code == 422
        assert "PLANIX_TRANSCRIPT_SECRET_728391" not in invalid.text
    finally:
        app.dependency_overrides.pop(get_learning_transcript_service, None)


def test_upload_size_path_and_timestamp_limits_are_enforced(client) -> None:
    app.dependency_overrides[get_learning_transcript_service] = lambda: _service(
        maximum_bytes=64
    )
    try:
        too_large = client.post(
            "/api/learning/transcripts",
            json=_payload("x" * 65),
        )
        assert too_large.status_code == 413

        unsafe_name = client.post(
            "/api/learning/transcripts",
            json=_payload("1\n00:00:01,000 --> 00:00:02,000\nok\n", sourceName="../x.srt"),
        )
        assert unsafe_name.status_code == 400

        invalid_time = client.post(
            "/api/learning/transcripts",
            json=_payload("1\n00:00:09,000 --> 00:00:01,000\nbad\n"),
        )
        assert invalid_time.status_code == 400
    finally:
        app.dependency_overrides.pop(get_learning_transcript_service, None)
