from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...quality import LearningQualityEngine
from ...services import LearningPipeline
from ..artifact_store import ArtifactStore, ArtifactStoreError, InMemoryArtifactStore
from ..learning_runtime import LearningRuntime
from ..storage import PostgresArtifactStore, validate_learning_artifact_repository
from .config import LearningRuntimeConfig
from .provider_factory import (
    RuntimeUnavailable,
    TranscriptBackedVideoProvider,
    component_name,
    create_model_provider,
    create_transcript_provider,
    create_video_provider,
)


@dataclass(frozen=True)
class _RuntimeComponents:
    video_provider: object
    transcript_provider: object
    model_provider: object
    artifact_store: ArtifactStore


def create_artifact_store(config: LearningRuntimeConfig) -> ArtifactStore:
    if config.artifact_store == "memory":
        if config.environment == "production":
            raise RuntimeUnavailable(
                "artifact_store",
                "InMemory artifact store is forbidden in production",
            )
        return InMemoryArtifactStore()

    repository = config.artifact_repository
    if repository is None:
        raise RuntimeUnavailable(
            "artifact_store",
            "PostgreSQL artifact repository is not configured",
        )
    try:
        validate_learning_artifact_repository(repository, require_metadata=True)
    except ArtifactStoreError as exc:
        raise RuntimeUnavailable("artifact_store", str(exc)) from exc
    required = (
        "save",
        "get",
        "list_versions",
        "delete",
        "save_checkpoint",
        "get_checkpoint",
        "transaction",
        "save_resume_event",
        "get_resume_events",
    )
    missing = [name for name in required if not callable(getattr(repository, name, None))]
    if missing:
        raise RuntimeUnavailable(
            "artifact_store",
            f"PostgreSQL artifact repository is missing methods: {sorted(missing)}",
        )
    health_check = getattr(repository, "health_check", None)
    if callable(health_check):
        try:
            if health_check() is False:
                raise RuntimeUnavailable("artifact_store", "health check failed")
        except RuntimeUnavailable:
            raise
        except Exception as exc:
            raise RuntimeUnavailable(
                "artifact_store",
                "health check failed",
            ) from exc
    return PostgresArtifactStore(repository)


class LearningRuntimeFactory:
    """Production composition root for the existing LearningPipeline/Runtime."""

    def __init__(self, config: LearningRuntimeConfig):
        self.config = config

    def __call__(self) -> LearningRuntime:
        return self.create()

    def create(self) -> LearningRuntime:
        components = self._components()
        return self._create_runtime(components)

    @staticmethod
    def _create_runtime(components: _RuntimeComponents) -> LearningRuntime:
        evidence_provider = TranscriptBackedVideoProvider(
            components.video_provider,
            components.transcript_provider,
        )
        pipeline = LearningPipeline(
            provider=evidence_provider,
            model=components.model_provider,
            quality_engine=LearningQualityEngine(),
        )
        return LearningRuntime(
            pipeline,
            artifact_store=components.artifact_store,
            checkpoint_store=components.artifact_store,
        )

    def health(self) -> dict[str, Any]:
        statuses: dict[str, dict[str, str]] = {
            "video": {"status": "unknown", "name": ""},
            "transcript": {"status": "unknown", "name": ""},
            "model": {"status": "unknown", "name": ""},
        }
        artifact_status = {"status": "unknown", "name": self.config.artifact_store}
        try:
            components = self._components()
            video = components.video_provider
            statuses["video"] = {
                "status": "ready",
                "name": component_name(video),
            }
            transcript = components.transcript_provider
            statuses["transcript"] = {
                "status": "ready",
                "name": component_name(transcript),
            }
            model = components.model_provider
            statuses["model"] = {
                "status": "ready",
                "name": component_name(model),
            }
            store = components.artifact_store
            artifact_status = {
                "status": "ready",
                "name": component_name(store),
            }
            self._create_runtime(components)
            return {
                "status": "ready",
                "environment": self.config.environment,
                "providers": statuses,
                "artifact_store": artifact_status,
                "error": None,
            }
        except RuntimeUnavailable as exc:
            if exc.component in {"video_provider", "transcript_provider", "model_provider"}:
                key = exc.component.removesuffix("_provider")
                statuses[key] = {"status": "unavailable", "name": ""}
            elif exc.component == "artifact_store":
                artifact_status["status"] = "unavailable"
            return {
                "status": "unavailable",
                "environment": self.config.environment,
                "providers": statuses,
                "artifact_store": artifact_status,
                "error": {"component": exc.component, "message": exc.message},
            }

    def _components(self) -> _RuntimeComponents:
        return _RuntimeComponents(
            video_provider=create_video_provider(self.config),
            transcript_provider=create_transcript_provider(self.config),
            model_provider=create_model_provider(self.config),
            artifact_store=create_artifact_store(self.config),
        )


__all__ = [
    "LearningRuntimeFactory",
    "RuntimeUnavailable",
    "create_artifact_store",
]
