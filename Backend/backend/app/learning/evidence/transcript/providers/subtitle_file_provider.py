from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Literal

from ....contracts import VideoResource
from ..validators import TranscriptValidator
from .base import (
    TranscriptDocument,
    TranscriptProviderError,
    TranscriptSegment,
    TranscriptSourceMetadata,
)


SubtitleFormat = Literal["srt", "vtt"]


@dataclass(frozen=True)
class _SubtitleSource:
    resource_id: str
    fingerprint: str
    source_id: str
    source_name: str
    language: str
    format: SubtitleFormat
    content: str


@dataclass(frozen=True)
class _ParsedCue:
    start_ms: int
    end_ms: int
    text: str


class SubtitleFileTranscriptProvider:
    """User-authorized SRT/VTT source; it performs no network scraping."""

    source_type = "srt_vtt"

    _SRT_TIME = re.compile(
        r"^(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
    )
    _VTT_TIME = re.compile(
        r"^(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*"
        r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})(?:\s+.*)?$"
    )

    def __init__(self, *, validator: TranscriptValidator | None = None):
        self.validator = validator or TranscriptValidator()
        self._sources: dict[str, _SubtitleSource] = {}

    def register_upload(
        self,
        resource: VideoResource,
        *,
        filename: str,
        content: str | bytes,
        language: str = "",
    ) -> None:
        suffix = Path(filename).suffix.casefold()
        if suffix not in {".srt", ".vtt"}:
            raise TranscriptProviderError("subtitle upload must be one .srt or .vtt file")
        text = self._decode(content)
        self._sources[resource.id] = _SubtitleSource(
            resource_id=resource.id,
            fingerprint=resource.content_fingerprint,
            source_id=f"upload:{sha256(text.encode('utf-8')).hexdigest()}",
            source_name=Path(filename).name,
            language=language,
            format=suffix.removeprefix("."),
            content=text,
        )

    def register_file(
        self,
        resource: VideoResource,
        path: str | Path,
        *,
        language: str = "",
    ) -> None:
        resolved = Path(path)
        if resolved.suffix.casefold() not in {".srt", ".vtt"}:
            raise TranscriptProviderError("subtitle file must use .srt or .vtt")
        try:
            content = resolved.read_bytes()
        except OSError as exc:
            raise TranscriptProviderError("subtitle file could not be read") from exc
        self.register_upload(
            resource,
            filename=resolved.name,
            content=content,
            language=language,
        )

    def fetch_transcript(self, resource: VideoResource) -> TranscriptDocument:
        source = self._sources.get(resource.id)
        if source is None:
            raise TranscriptProviderError("subtitle source is not registered")
        if source.fingerprint != resource.content_fingerprint:
            raise TranscriptProviderError(
                "subtitle source fingerprint does not match the video resource"
            )
        cues = self._parse(source)
        document = TranscriptDocument(
            resourceId=source.resource_id,
            fingerprint=source.fingerprint,
            language=source.language,
            segments=self._integer_segments(cues, source.source_id),
            sourceMetadata=TranscriptSourceMetadata(
                sourceType=source.format,
                sourceId=source.source_id,
                sourceName=source.source_name,
                checksum=source.source_id.removeprefix("upload:"),
                authorized=True,
            ),
        )
        self.validator.validate(resource, document)
        return document

    def health_check(self) -> bool:
        return True

    @classmethod
    def _parse(cls, source: _SubtitleSource) -> list[_ParsedCue]:
        normalized = source.content.replace("\r\n", "\n").replace("\r", "\n")
        if source.format == "vtt":
            lines = normalized.lstrip("\ufeff").split("\n")
            if not lines or lines[0].strip().upper() != "WEBVTT":
                raise TranscriptProviderError("VTT source must start with WEBVTT")
            normalized = "\n".join(lines[1:])
            pattern = cls._VTT_TIME
        else:
            normalized = normalized.lstrip("\ufeff")
            pattern = cls._SRT_TIME

        cues: list[_ParsedCue] = []
        for block in re.split(r"\n\s*\n", normalized.strip()):
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if not lines:
                continue
            time_index = next(
                (index for index, line in enumerate(lines) if "-->" in line),
                -1,
            )
            if time_index < 0:
                continue
            match = pattern.fullmatch(lines[time_index])
            if match is None:
                raise TranscriptProviderError("subtitle timestamp is invalid")
            text = " ".join(lines[time_index + 1 :]).strip()
            if not text:
                raise TranscriptProviderError("subtitle cue text is empty")
            start_ms = cls._timestamp_ms(match.group("start"))
            end_ms = cls._timestamp_ms(match.group("end"))
            if end_ms <= start_ms:
                raise TranscriptProviderError("subtitle cue end must be after start")
            cues.append(_ParsedCue(start_ms=start_ms, end_ms=end_ms, text=text))
        if not cues:
            raise TranscriptProviderError("subtitle source contains no cues")
        return cues

    @staticmethod
    def _timestamp_ms(value: str) -> int:
        normalized = value.replace(",", ".")
        parts = normalized.split(":")
        if len(parts) == 2:
            hours = 0
            minutes_text, seconds_text = parts
        elif len(parts) == 3:
            hours_text, minutes_text, seconds_text = parts
            hours = int(hours_text)
        else:
            raise TranscriptProviderError("subtitle timestamp is invalid")
        seconds, milliseconds = seconds_text.split(".", 1)
        minutes = int(minutes_text)
        second_value = int(seconds)
        if minutes >= 60 or second_value >= 60:
            raise TranscriptProviderError("subtitle timestamp is invalid")
        return ((hours * 60 + minutes) * 60 + second_value) * 1000 + int(
            milliseconds
        )

    @staticmethod
    def _integer_segments(
        cues: list[_ParsedCue],
        source_id: str,
    ) -> list[TranscriptSegment]:
        groups: list[tuple[int, int, list[str]]] = []
        previous_start = -1
        previous_end = -1
        for cue in cues:
            if cue.start_ms < previous_start or cue.start_ms < previous_end:
                raise TranscriptProviderError(
                    "subtitle cues are out of order or overlap"
                )
            previous_start = cue.start_ms
            previous_end = cue.end_ms
            start = cue.start_ms // 1000
            end = max(start + 1, (cue.end_ms + 999) // 1000)
            if groups and start < groups[-1][1]:
                group_start, group_end, texts = groups[-1]
                groups[-1] = (group_start, max(group_end, end), [*texts, cue.text])
            else:
                groups.append((start, end, [cue.text]))
        return [
            TranscriptSegment(
                id=f"subtitle-{sha256(f'{source_id}:{index}'.encode()).hexdigest()[:16]}",
                startSeconds=start,
                endSeconds=end,
                text=" ".join(texts),
            )
            for index, (start, end, texts) in enumerate(groups)
        ]

    @staticmethod
    def _decode(content: str | bytes) -> str:
        if isinstance(content, str):
            return content
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TranscriptProviderError("subtitle file must be UTF-8 encoded") from exc


__all__ = ["SubtitleFileTranscriptProvider"]
