from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SchedulerAction(StrEnum):
    """Lifecycle actions emitted by the formal planning graph."""

    INVOKE_AGENT = "invoke_agent"
    INVOKE_CONTROLLER = "invoke_controller"
    WAIT_USER = "wait_user"
    REPAIR = "repair"
    RECOVER = "recover"
    BLOCK = "block"
    COMPLETE = "complete"


@dataclass(frozen=True)
class SchedulerDecision:
    action: SchedulerAction
    next_node: str
    reason_code: str
    agent_id: str | None = None


AGENT_BY_NODE: dict[str, str] = {
    "understanding": "goal_intelligence",
    "understanding_readiness": "goal_completion",
    "assess_context": "reality",
    "synthesize_context": "evidence",
    "design_approach": "strategy",
    "generate_plan": "execution",
    "semantic_review": "critic",
    "record_learning": "feedback_learning",
}


class AgentScheduler:
    """Marker for Harness scheduler ownership.

    The single formal graph owns its explicit transition table; the Harness
    validates and records each typed ``SchedulerDecision`` without a second,
    competing V1 routing table.
    """


DEFAULT_SCHEDULER = AgentScheduler()


__all__ = [
    "AGENT_BY_NODE",
    "DEFAULT_SCHEDULER",
    "AgentScheduler",
    "SchedulerAction",
    "SchedulerDecision",
]
