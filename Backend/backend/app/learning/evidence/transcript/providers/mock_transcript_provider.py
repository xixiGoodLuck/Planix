from __future__ import annotations

from ....contracts import VideoResource
from .base import TranscriptDocument, TranscriptProviderError


class MockTranscriptProvider:
    """Deterministic transcript source for tests; never performs network I/O."""

    source_type = "mock"

    def __init__(self, documents: list[TranscriptDocument]):
        self._documents = {item.resource_id: item for item in documents}
        if len(self._documents) != len(documents):
            raise ValueError("mock transcript resource ids must be unique")
        self.fetch_calls: list[str] = []

    def fetch_transcript(self, resource: VideoResource) -> TranscriptDocument:
        self.fetch_calls.append(resource.id)
        try:
            return self._documents[resource.id].model_copy(deep=True)
        except KeyError as exc:
            raise TranscriptProviderError(
                f"mock transcript does not exist for resource {resource.id}"
            ) from exc

    def health_check(self) -> bool:
        return True


__all__ = ["MockTranscriptProvider"]
