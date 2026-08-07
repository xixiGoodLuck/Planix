from __future__ import annotations

from app.cognitive_planning.graph.planning_graph import _from_guard
from app.harness.scheduler import SchedulerAction


def test_continue_from_final_revision_rechecks_current_critic_only() -> None:
    decision = _from_guard(
        {
            "user_action": "continue_current_stage",
            "status": "final_revision",
            "repair_count": 2,
        }
    )

    assert decision.next_node == "semantic_review"
    assert decision.action == SchedulerAction.RECOVER
    assert decision.agent_id == "critic"
