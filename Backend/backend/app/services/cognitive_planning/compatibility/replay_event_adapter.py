from __future__ import annotations

from ....schemas import PlanningSessionResponse


COGNITIVE_EVENT_FIELDS = (
    ("goal_model_updated", "goal_model"),
    ("goal_completion_updated", "goal_completion"),
    ("reality_assessment_ready", "reality_assessment"),
    ("evidence_pack_ready", "evidence_pack"),
    ("strategy_portfolio_ready", "strategy_portfolio"),
    ("execution_blueprint_ready", "execution_blueprint"),
    ("critique_report_ready", "critique_report"),
    ("planning_learning_updated", "planning_learning_update"),
)


def cognitive_events(session: PlanningSessionResponse) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for event_type, field in COGNITIVE_EVENT_FIELDS:
        value = getattr(session, field, None)
        if isinstance(value, dict) and value:
            events.append((event_type, value))
    return events
