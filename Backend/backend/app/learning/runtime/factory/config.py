from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...evidence.providers import VideoSourceProvider
from ...evidence.transcript import TranscriptProvider
from ...generators import LearningSemanticModel
from ..storage import LearningArtifactRepository


LearningEnvironment = Literal["development", "production"]
LearningArtifactStoreKind = Literal["memory", "postgres"]


@dataclass(frozen=True)
class LearningRuntimeConfig:
    video_provider: VideoSourceProvider | None
    transcript_provider: TranscriptProvider | None
    artifact_store: LearningArtifactStoreKind
    model_provider: LearningSemanticModel | None
    environment: LearningEnvironment
    artifact_repository: LearningArtifactRepository | None = None


__all__ = [
    "LearningArtifactStoreKind",
    "LearningEnvironment",
    "LearningRuntimeConfig",
]
