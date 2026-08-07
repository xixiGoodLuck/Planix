from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import CognitiveOSRuntime


def get_planning_orchestrator() -> "CognitiveOSRuntime":
    """Return the only production planning runtime."""

    from .runtime import CognitiveOSRuntime

    return CognitiveOSRuntime()


__all__ = ["get_planning_orchestrator"]
