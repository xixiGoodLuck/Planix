from __future__ import annotations

import os
from collections.abc import Callable
from threading import RLock

from ...evidence.providers import BilibiliProvider
from ...evidence.transcript import (
    LearningTranscriptRegistrationService,
    LearningTranscriptRepository,
    PersistentTranscriptProvider,
    SubtitleFileTranscriptProvider,
)
from ...generators import RouterLearningModel
from ..factory import LearningRuntimeConfig, LearningRuntimeFactory, RuntimeUnavailable
from ..learning_runtime import LearningRuntime
from ..storage import PostgresLearningArtifactRepository
from .health_checks import learning_health_snapshot
from .startup_checks import (
    StartupCheckReport,
    run_startup_checks,
    unavailable_startup_report,
)


LearningRuntimeConfigLoader = Callable[[], LearningRuntimeConfig]


def load_learning_runtime_config() -> LearningRuntimeConfig:
    environment = (
        os.getenv("PLANIX_LEARNING_ENVIRONMENT", "production").strip().casefold()
    )
    if environment not in {"development", "production"}:
        raise ValueError("PLANIX_LEARNING_ENVIRONMENT must be development or production")
    repository = (
        None
        if environment == "development"
        else PostgresLearningArtifactRepository()
    )
    video_provider = BilibiliProvider()
    transcript_repository = (
        None if environment == "development" else LearningTranscriptRepository()
    )
    transcript_provider = (
        SubtitleFileTranscriptProvider()
        if transcript_repository is None
        else PersistentTranscriptProvider(transcript_repository)
    )
    return LearningRuntimeConfig(
        video_provider=video_provider,
        transcript_provider=transcript_provider,
        artifact_store="memory" if environment == "development" else "postgres",
        model_provider=RouterLearningModel(),
        environment=environment,
        artifact_repository=repository,
        transcript_repository=transcript_repository,
    )


class LearningRuntimeBootstrap:
    """Application-startup composition boundary for the Learning Runtime."""

    def __init__(
        self,
        config_loader: LearningRuntimeConfigLoader = load_learning_runtime_config,
    ):
        self.config_loader = config_loader
        self._lock = RLock()
        self._config: LearningRuntimeConfig | None = None
        self._factory: LearningRuntimeFactory | None = None
        self._runtime: LearningRuntime | None = None
        self._report = unavailable_startup_report("runtime", "not_started")

    @property
    def report(self) -> StartupCheckReport:
        with self._lock:
            return self._report.model_copy(deep=True)

    def startup(self) -> StartupCheckReport:
        with self._lock:
            self._close_components()
            self._config = None
            self._factory = None
            self._runtime = None
            try:
                config = self.config_loader()
                factory = LearningRuntimeFactory(config)
                report, runtime = run_startup_checks(factory)
            except Exception as exc:
                self._report = unavailable_startup_report(
                    "configuration",
                    type(exc).__name__,
                )
                return self._report.model_copy(deep=True)

            self._config = config
            self._factory = factory
            self._runtime = runtime
            self._report = report
            return report.model_copy(deep=True)

    def create_runtime(self) -> LearningRuntime:
        with self._lock:
            if self._factory is None or self._report.status != "ready":
                raise RuntimeUnavailable(
                    "runtime",
                    "Learning Runtime startup validation failed",
                )
            return self._factory.create()

    def health(self) -> dict:
        with self._lock:
            report = self._report.model_copy(deep=True)
            transcript_provider = (
                self._config.transcript_provider if self._config is not None else None
            )
        return learning_health_snapshot(report, transcript_provider)

    def transcript_registration_service(
        self,
    ) -> LearningTranscriptRegistrationService | None:
        with self._lock:
            if (
                self._config is None
                or self._config.video_provider is None
                or self._config.transcript_repository is None
            ):
                return None
            return LearningTranscriptRegistrationService(
                self._config.video_provider,
                self._config.transcript_repository,
            )

    def shutdown(self) -> None:
        with self._lock:
            self._close_components()
            self._config = None
            self._factory = None
            self._runtime = None
            self._report = unavailable_startup_report("runtime", "stopped")

    def _close_components(self) -> None:
        if self._config is None:
            return
        seen: set[int] = set()
        for component in (
            self._config.video_provider,
            self._config.transcript_provider,
            self._config.model_provider,
            self._config.artifact_repository,
            self._config.transcript_repository,
        ):
            if component is None or id(component) in seen:
                continue
            seen.add(id(component))
            close = getattr(component, "close", None)
            if callable(close):
                close()


_learning_runtime_bootstrap = LearningRuntimeBootstrap()


def get_learning_runtime_bootstrap() -> LearningRuntimeBootstrap:
    return _learning_runtime_bootstrap


__all__ = [
    "LearningRuntimeBootstrap",
    "LearningRuntimeConfigLoader",
    "get_learning_runtime_bootstrap",
    "load_learning_runtime_config",
]
