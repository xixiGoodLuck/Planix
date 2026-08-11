from __future__ import annotations

from dataclasses import dataclass

from ....contracts import (
    ContentSegment,
    EvidenceSourceRange,
    SegmentEvidence,
    VideoResource,
)
from ....generators.base import generated_id
from ..providers import TranscriptDocument, TranscriptSegment
from ..validators import TranscriptValidator


class TranscriptBuildError(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptBuildResult:
    segments: list[ContentSegment]
    evidence: list[SegmentEvidence]


class TranscriptSegmentBuilder:
    """Projects validated raw transcript cues into fixed-size evidence segments."""

    def __init__(
        self,
        *,
        cues_per_segment: int = 2,
        validator: TranscriptValidator | None = None,
    ):
        if cues_per_segment < 1:
            raise ValueError("cues_per_segment must be at least one")
        self.cues_per_segment = cues_per_segment
        self.validator = validator or TranscriptValidator()

    def build(
        self,
        resource: VideoResource,
        document: TranscriptDocument,
    ) -> TranscriptBuildResult:
        if not isinstance(document, TranscriptDocument):
            raise TranscriptBuildError(
                "verified TranscriptDocument is required; VideoResource metadata cannot create timestamps"
            )
        self.validator.validate(resource, document)

        segments: list[ContentSegment] = []
        evidence: list[SegmentEvidence] = []
        for group_index, offset in enumerate(
            range(0, len(document.segments), self.cues_per_segment)
        ):
            cues = document.segments[offset : offset + self.cues_per_segment]
            segment_id = generated_id(
                "segment",
                resource.id,
                group_index,
                "|".join(item.id for item in cues),
            )
            group_evidence = self._build_evidence(
                resource,
                segment_id,
                cues,
                first_cue_index=offset,
            )
            summary = " ".join(item.text.strip() for item in cues)
            segments.append(
                ContentSegment(
                    id=segment_id,
                    resourceId=resource.id,
                    resourceFingerprint=resource.content_fingerprint,
                    startSeconds=cues[0].start_seconds,
                    endSeconds=cues[-1].end_seconds,
                    contentSummary=summary,
                    topics=[],
                    evidenceRefs=[item.id for item in group_evidence],
                )
            )
            evidence.extend(group_evidence)
        return TranscriptBuildResult(segments=segments, evidence=evidence)

    @staticmethod
    def _build_evidence(
        resource: VideoResource,
        segment_id: str,
        cues: list[TranscriptSegment],
        *,
        first_cue_index: int,
    ) -> list[SegmentEvidence]:
        result: list[SegmentEvidence] = []
        cursor = 0
        for local_index, cue in enumerate(cues):
            text = cue.text.strip()
            start_offset = cursor
            end_offset = start_offset + len(text)
            global_index = first_cue_index + local_index
            result.append(
                SegmentEvidence(
                    id=generated_id(
                        "evidence",
                        segment_id,
                        global_index,
                        cue.id,
                    ),
                    resourceId=resource.id,
                    resourceFingerprint=resource.content_fingerprint,
                    segmentId=segment_id,
                    kind="transcript_span",
                    supportedClaim=text,
                    sourceRange=EvidenceSourceRange(
                        locatorType="transcript_chars",
                        startOffset=start_offset,
                        endOffset=end_offset,
                    ),
                    sourceExcerpt=text,
                    verificationStatus="verified",
                )
            )
            cursor = end_offset + 1
        return result


__all__ = ["TranscriptBuildError", "TranscriptBuildResult", "TranscriptSegmentBuilder"]
