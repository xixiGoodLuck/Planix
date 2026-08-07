from .runtime import CognitiveOSRuntime


def get_planning_orchestrator() -> CognitiveOSRuntime:
    """Return the only production planning runtime."""

    return CognitiveOSRuntime()


__all__ = ["CognitiveOSRuntime", "get_planning_orchestrator"]
