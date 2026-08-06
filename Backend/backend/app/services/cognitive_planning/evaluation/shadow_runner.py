from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from ....db import get_conn
from ....schemas import CreatePlanningSessionRequest, PlanningSessionResponse
from ..contracts.base import CognitiveContract
from .deterministic_guards import template_phrase_hits


class PlanningSessionCreator(Protocol):
    def create_session(self, payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
        ...


class PlanningShadowComparison(CognitiveContract):
    legacy_session_id: str
    cognitive_session_id: str
    legacy_status: str
    cognitive_status: str
    legacy_task_count: int
    cognitive_task_count: int
    cognitive_planning_mode: str
    cognitive_critic_status: str
    cognitive_calendar_writable: bool
    forbidden_template_hits: list[str]
    notes: list[str]


def _legacy_titles(session: PlanningSessionResponse) -> list[str]:
    if not session.execution_draft:
        return []
    return [task.title for task in session.execution_draft.tasks]


def _cognitive_titles(session: PlanningSessionResponse) -> list[str]:
    raw = session.execution_blueprint or {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else []
    return [str(item.get("title") or "") for item in tasks or [] if isinstance(item, dict)]


class CognitivePlanningShadowRunner:
    """Opt-in rollout evaluator; never participates in normal P Mode routing."""

    def __init__(self, *, legacy: PlanningSessionCreator, cognitive: PlanningSessionCreator):
        self.legacy = legacy
        self.cognitive = cognitive

    def run_create(self, payload: CreatePlanningSessionRequest) -> PlanningShadowComparison:
        base_thread = payload.thread_id or "manual-shadow"
        legacy_payload = payload.model_copy(update={"thread_id": f"{base_thread}:legacy-shadow"})
        cognitive_payload = payload.model_copy(update={"thread_id": f"{base_thread}:cognitive-shadow"})
        legacy = self.legacy.create_session(legacy_payload)
        cognitive = self.cognitive.create_session(cognitive_payload)
        comparison = self.compare(legacy, cognitive)
        self._record(comparison)
        return comparison

    def compare(
        self,
        legacy: PlanningSessionResponse,
        cognitive: PlanningSessionResponse,
    ) -> PlanningShadowComparison:
        legacy_titles = _legacy_titles(legacy)
        cognitive_titles = _cognitive_titles(cognitive)
        critique = cognitive.critique_report or {}
        mode = cognitive.cognitive_metadata.planning_mode if cognitive.cognitive_metadata else ""
        hits = template_phrase_hits([*legacy_titles, *cognitive_titles])
        notes: list[str] = []
        if not cognitive.goal_model:
            notes.append("cognitive run did not produce a goal model")
        if mode != "model_backed":
            notes.append("cognitive run was not model-backed")
        if hits:
            notes.append("one or both runs contain frozen template phrases")
        return PlanningShadowComparison(
            legacySessionId=legacy.session_id,
            cognitiveSessionId=cognitive.session_id,
            legacyStatus=legacy.status,
            cognitiveStatus=cognitive.status,
            legacyTaskCount=len(legacy_titles),
            cognitiveTaskCount=len(cognitive_titles),
            cognitivePlanningMode=mode,
            cognitiveCriticStatus=str(critique.get("status") or ""),
            cognitiveCalendarWritable=bool(critique.get("calendarWritable", False)),
            forbiddenTemplateHits=hits,
            notes=notes,
        )

    def _record(self, comparison: PlanningShadowComparison) -> None:
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO planning_shadow_runs(
                  id, legacy_session_id, cognitive_session_id, comparison_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    comparison.legacy_session_id,
                    comparison.cognitive_session_id,
                    json.dumps(comparison.model_dump(by_alias=True), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )


__all__ = ["CognitivePlanningShadowRunner", "PlanningShadowComparison"]
