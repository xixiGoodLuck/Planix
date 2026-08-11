from __future__ import annotations

from typing import Any

from .startup_checks import StartupCheckReport


def learning_health_snapshot(
    report: StartupCheckReport,
    transcript_provider: object | None = None,
) -> dict[str, Any]:
    checks = {item.component: item for item in report.checks}

    def status(component: str) -> dict[str, str]:
        check = checks.get(component)
        if check is None:
            return {"status": "unavailable", "error_type": "not_checked"}
        return {"status": check.status, "error_type": check.error_type}

    first_error = next(
        (item for item in report.checks if item.status == "unavailable"),
        None,
    )
    transcript_source_status = status("transcript_provider")
    transcript_source_status["source_type"] = str(
        getattr(transcript_provider, "source_type", "unspecified")
    )
    return {
        "status": report.status,
        "runtime": status("runtime"),
        "providers": {
            "video": status("video_provider"),
            "transcript": status("transcript_provider"),
            "model": status("model_provider"),
        },
        "artifact_store": status("artifact_store"),
        "transcript_source_status": transcript_source_status,
        "startup_status": report.model_dump(mode="json"),
        "error": (
            {
                "component": first_error.component,
                "error_type": first_error.error_type,
            }
            if first_error is not None
            else None
        ),
    }


__all__ = ["learning_health_snapshot"]
