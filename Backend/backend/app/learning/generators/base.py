from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ...services.llm import LlmClient
from ..contracts import LearningArtifact, LearningArtifactRef, LearningArtifactType


DraftT = TypeVar("DraftT", bound=BaseModel)


class LearningModelOutputError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


class LearningGenerationError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


@dataclass(frozen=True)
class LearningModelResponse(Generic[DraftT]):
    value: DraftT
    model_usage: dict[str, Any]


class LearningSemanticModel(Protocol):
    def complete(
        self,
        *,
        stage: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        response_type: type[DraftT],
        max_tokens: int,
    ) -> LearningModelResponse[DraftT]: ...


class RouterLearningModel:
    """Strict JSON adapter over the existing LlmClient -> ModelRouter path."""

    def __init__(self, llm: LlmClient | None = None):
        self.llm = llm or LlmClient()

    def complete(
        self,
        *,
        stage: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        response_type: type[DraftT],
        max_tokens: int,
    ) -> LearningModelResponse[DraftT]:
        user = json.dumps(
            {
                "input": payload,
                "requiredOutputSchema": response_type.model_json_schema(by_alias=True),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result, error = self.llm.complete(
            feature,
            system,
            user,
            max_tokens=max_tokens,
            max_token_cap=4000,
            temperature=0.1,
            response_format_json=True,
            task_type="planning_learning",
            record_run=False,
        )
        if not result:
            message = error.message if error else "the configured model returned no result"
            raise LearningModelOutputError(stage, message)
        try:
            raw = json.loads(result.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LearningModelOutputError(stage, "model output is not one JSON object") from exc
        try:
            value = response_type.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", ())) or "root"
            message = str(first.get("msg") or "schema validation failed")
            raise LearningModelOutputError(
                stage,
                f"model output failed contract validation at {location}: {message}",
            ) from exc
        return LearningModelResponse(
            value=value,
            model_usage={
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "latencyMs": result.latency_ms,
                "attempts": result.attempts or [],
            },
        )


def generated_id(kind: str, owner_id: str, index: int, label: str) -> str:
    digest = sha256(f"{owner_id}\x1f{index}\x1f{label}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def artifact_ref(
    artifact_type: LearningArtifactType,
    artifact: LearningArtifact,
) -> LearningArtifactRef:
    return LearningArtifactRef(
        artifactType=artifact_type,
        artifactId=artifact.artifact_id,
        version=artifact.version,
    )


def require_index(index: int, size: int, *, stage: str, field: str) -> int:
    if index < 0 or index >= size:
        raise LearningGenerationError(
            stage,
            f"{field} references index {index}, but the available range is 0..{size - 1}",
        )
    return index


__all__ = [
    "LearningGenerationError",
    "LearningModelOutputError",
    "LearningModelResponse",
    "LearningSemanticModel",
    "RouterLearningModel",
    "artifact_ref",
    "generated_id",
    "require_index",
]
