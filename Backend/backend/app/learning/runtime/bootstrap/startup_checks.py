from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..factory import LearningRuntimeFactory, RuntimeUnavailable
from ..factory.provider_factory import (
    create_model_provider,
    create_transcript_provider,
    create_video_provider,
)
from ..factory.runtime_factory import create_artifact_store
from ..learning_runtime import LearningRuntime


StartupStatus = Literal["ready", "unavailable"]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StartupComponentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str = Field(min_length=1)
    status: StartupStatus
    error_type: str = ""


class StartupCheckReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StartupStatus
    checks: list[StartupComponentCheck]
    checked_at: str = Field(default_factory=_now)


def _error_type(exc: Exception) -> str:
    if not isinstance(exc, RuntimeUnavailable):
        return type(exc).__name__
    message = exc.message.casefold()
    if "not configured" in message:
        return "missing_configuration"
    if "mock" in message and "forbidden" in message:
        return "mock_forbidden"
    if "does not implement" in message or "missing methods" in message:
        return "invalid_component"
    if "authorized or srt/vtt" in message:
        return "unsupported_source"
    if "schema version" in message:
        return "schema_incompatible"
    if "namespace" in message:
        return "invalid_namespace"
    if "health check failed" in message:
        return "unavailable"
    return "runtime_unavailable"


def run_startup_checks(
    factory: LearningRuntimeFactory,
) -> tuple[StartupCheckReport, LearningRuntime | None]:
    checks: list[StartupComponentCheck] = []
    operations = (
        ("model_provider", create_model_provider),
        ("video_provider", create_video_provider),
        ("transcript_provider", create_transcript_provider),
        ("artifact_store", create_artifact_store),
    )
    for component, operation in operations:
        try:
            operation(factory.config)
            checks.append(StartupComponentCheck(component=component, status="ready"))
        except Exception as exc:
            checks.append(
                StartupComponentCheck(
                    component=component,
                    status="unavailable",
                    error_type=_error_type(exc),
                )
            )

    runtime: LearningRuntime | None = None
    if all(check.status == "ready" for check in checks):
        try:
            runtime = factory.create()
            checks.append(StartupComponentCheck(component="runtime", status="ready"))
        except Exception as exc:
            checks.append(
                StartupComponentCheck(
                    component="runtime",
                    status="unavailable",
                    error_type=_error_type(exc),
                )
            )
    else:
        checks.append(
            StartupComponentCheck(
                component="runtime",
                status="unavailable",
                error_type="dependency_unavailable",
            )
        )

    status: StartupStatus = (
        "ready" if all(check.status == "ready" for check in checks) else "unavailable"
    )
    return StartupCheckReport(status=status, checks=checks), runtime


def unavailable_startup_report(
    component: str,
    error_type: str,
) -> StartupCheckReport:
    return StartupCheckReport(
        status="unavailable",
        checks=[
            StartupComponentCheck(
                component=component,
                status="unavailable",
                error_type=error_type,
            ),
            StartupComponentCheck(
                component="runtime",
                status="unavailable",
                error_type="dependency_unavailable",
            ),
        ],
    )


__all__ = [
    "StartupCheckReport",
    "StartupComponentCheck",
    "run_startup_checks",
    "unavailable_startup_report",
]
