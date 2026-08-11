from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.db import get_conn
from app.learning.evidence.transcript import LearningTranscriptRepository

from test_learning_transcript_repository import register_source


def test_ten_concurrent_identical_registrations_are_idempotent() -> None:
    def register(_index: int) -> str:
        return register_source(LearningTranscriptRepository()).source_id

    with ThreadPoolExecutor(max_workers=10) as executor:
        source_ids = list(executor.map(register, range(10)))

    assert len(set(source_ids)) == 1
    with get_conn() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM learning_transcript_sources) AS sources,
              (SELECT COUNT(*) FROM learning_transcript_segments) AS segments
            """
        ).fetchone()
    assert counts == {"sources": 1, "segments": 2}
