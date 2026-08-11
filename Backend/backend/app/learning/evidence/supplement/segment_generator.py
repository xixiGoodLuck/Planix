from __future__ import annotations

from ...contracts import ContentSegment, VideoResource
from ..transcript import TranscriptBuildResult, TranscriptDocument, TranscriptSegmentBuilder


class TranscriptSegmentGenerator:
    """Projects validated transcript timestamps; it never asks a model for ranges."""

    def __init__(self, builder: TranscriptSegmentBuilder | None = None):
        self.builder = builder or TranscriptSegmentBuilder()

    def generate(
        self,
        document: TranscriptDocument,
        resource: VideoResource,
    ) -> list[ContentSegment]:
        return self.generate_with_evidence(document, resource).segments

    def generate_with_evidence(
        self,
        document: TranscriptDocument,
        resource: VideoResource,
    ) -> TranscriptBuildResult:
        return self.builder.build(resource, document)


__all__ = ["TranscriptSegmentGenerator"]
