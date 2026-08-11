from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field

from ...contracts import (
    LearningArtifact,
    LearningArtifactRef,
    LearningArtifactType,
    LearningContract,
)
from ..contracts import LearningRunCheckpoint


@dataclass(frozen=True)
class ValidatedStageContext(Mapping[LearningArtifactType, LearningArtifact]):
    run_id: str
    stage: str
    artifacts: Mapping[LearningArtifactType, LearningArtifact]

    def __getitem__(self, key: LearningArtifactType) -> LearningArtifact:
        return self.artifacts[key]

    def __iter__(self) -> Iterator[LearningArtifactType]:
        return iter(self.artifacts)

    def __len__(self) -> int:
        return len(self.artifacts)


@dataclass(frozen=True)
class ArtifactBundle:
    artifacts: tuple[LearningArtifact, ...] = ()


class StageExecutor(Protocol):
    def __call__(self, context: ValidatedStageContext) -> ArtifactBundle: ...


class ResumeExecutionResult(LearningContract):
    run_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    artifact_refs: list[LearningArtifactRef] = Field(default_factory=list)
    checkpoint_before: LearningRunCheckpoint | None = None
    checkpoint_after: LearningRunCheckpoint | None = None
    audit_ref: str | None = None
    error: str = ""


__all__ = [
    "ArtifactBundle",
    "ResumeExecutionResult",
    "StageExecutor",
    "ValidatedStageContext",
]
