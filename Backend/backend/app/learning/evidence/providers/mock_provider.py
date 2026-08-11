from __future__ import annotations

from ...contracts import VideoResource
from .base import (
    ProviderVideoDocument,
    VideoSearchHit,
    VideoSearchQuery,
    VideoSourceProviderError,
)


class MockVideoProvider:
    """In-memory provider used by Phase 3 fixtures; it never performs network I/O."""

    def __init__(self, documents: list[ProviderVideoDocument]):
        self._documents = {
            document.metadata.external_id: document for document in documents
        }
        if len(self._documents) != len(documents):
            raise ValueError("mock video external ids must be unique")
        self.search_calls = 0
        self.search_queries: list[VideoSearchQuery] = []
        self.metadata_calls: list[str] = []
        self.fetch_calls: list[str] = []

    def search(self, query: VideoSearchQuery) -> list[VideoSearchHit]:
        self.search_calls += 1
        self.search_queries.append(query)
        hits = [
            VideoSearchHit(
                provider=document.metadata.provider,
                externalId=document.metadata.external_id,
            )
            for document in self._documents.values()
        ]
        return hits[: query.maximum_results]

    def fetch_metadata(self, external_id: str) -> VideoResource:
        self.metadata_calls.append(external_id)
        document = self._document(external_id)
        metadata = document.metadata
        return VideoResource(
            id=f"video-{metadata.provider}-{metadata.external_id}",
            provider=metadata.provider,
            externalId=metadata.external_id,
            canonicalUrl=metadata.canonical_url,
            title=metadata.title,
            author=metadata.author,
            language=metadata.language,
            technologyVersions=metadata.technology_versions,
            durationSeconds=metadata.duration_seconds,
            publishedAt=metadata.published_at,
            contentFingerprint=metadata.content_fingerprint,
        )

    def fetch_evidence(self, external_id: str) -> ProviderVideoDocument:
        self.fetch_calls.append(external_id)
        return self._document(external_id)

    def _document(self, external_id: str) -> ProviderVideoDocument:
        try:
            return self._documents[external_id]
        except KeyError as exc:
            raise VideoSourceProviderError(
                f"mock video metadata does not exist for {external_id}"
            ) from exc


__all__ = ["MockVideoProvider"]
