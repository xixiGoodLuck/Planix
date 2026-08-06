from __future__ import annotations

from typing import Any

from .legacy_deep_planning import (
    LEGACY_PLANNING_ENGINE_VERSION,
    DeepPlanningService as LegacyTemplatePlanningService,
)


class DeepPlanningService:
    """Compatibility facade for the legacy Planning Session service path.

    P Mode selects the cognitive runtime before constructing this facade. The
    flag check here also protects direct legacy imports without allowing a
    cognitive failure to fall back to template-generated planning content.
    """

    def __init__(self, *args: Any, force_legacy: bool = False, **kwargs: Any):
        from .cognitive_planning import CognitivePlanningRuntime, use_cognitive_planning

        self._delegate = (
            LegacyTemplatePlanningService(*args, **kwargs)
            if force_legacy or not use_cognitive_planning()
            else CognitivePlanningRuntime()
        )

    @property
    def engine_version(self) -> str:
        return str(getattr(self._delegate, "engine_version", "cognitive-v2"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


__all__ = [
    "DeepPlanningService",
    "LegacyTemplatePlanningService",
    "LEGACY_PLANNING_ENGINE_VERSION",
]
