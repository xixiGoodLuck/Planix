from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..db import get_conn
from .contracts import ConversationTurn


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class PlanningPersistence:
    """Lifecycle persistence; planning artifacts live only in planning_artifacts."""

    def create(self, *, thread_id: str, user_input: str, context: dict[str, Any] | None = None) -> str:
        from ..services.calendar_snapshot import calendar_snapshot

        session_id = str(uuid4())
        now = now_iso()
        history = [ConversationTurn(role="user", content=user_input).model_dump(by_alias=True)]
        request_context = {**(context or {}), **calendar_snapshot(str((context or {}).get("timezone") or "Asia/Shanghai"))}
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO planning_sessions(
                  id, thread_id, entry_point, status, business_status, runtime_status,
                  user_input, conversation_history_json, request_context_json,
                  cognitive_metadata_json, repair_count, version, created_at, updated_at
                ) VALUES (?, ?, 'p_mode', 'needs_goal_clarification', 'goal_clarification',
                          'running', ?, ?, ?, '{}', 0, 1, ?, ?)
                """,
                (session_id, thread_id, user_input, dump(history), dump(request_context), now, now),
            )
        return session_id

    def get_row(self, session_id: str):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM planning_sessions WHERE id = ?", (session_id,)).fetchone()

    def latest_active(self, thread_id: str):
        active = {
            "needs_goal_clarification",
            "waiting_understanding_confirmation",
            "planning",
            "final_revision",
            "waiting_final_review",
            "waiting_calendar_write_approval",
            "learning_from_feedback",
            "MODEL_UNAVAILABLE",
        }
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM planning_sessions WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return row if row and row["status"] in active else None

    def conversation(self, row) -> list[ConversationTurn]:
        values = json_list(row["conversation_history_json"] if "conversation_history_json" in row.keys() else "[]")
        result: list[ConversationTurn] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            try:
                result.append(ConversationTurn.model_validate(item))
            except Exception:
                continue
        if not result and row["user_input"]:
            result.append(ConversationTurn(role="user", content=row["user_input"]))
        return result

    def append_user_turn(self, session_id: str, text: str) -> list[ConversationTurn]:
        row = self.get_row(session_id)
        if not row:
            return []
        history = self.conversation(row)
        history.append(ConversationTurn(role="user", content=text))
        combined = "\n".join(turn.content for turn in history if turn.role == "user")
        with get_conn() as conn:
            conn.execute(
                "UPDATE planning_sessions SET user_input = ?, conversation_history_json = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (combined, dump([item.model_dump(by_alias=True) for item in history]), now_iso(), session_id),
            )
        return history

    def append_assistant_turn(self, session_id: str, text: str) -> list[ConversationTurn]:
        row = self.get_row(session_id)
        if not row or not text.strip():
            return self.conversation(row) if row else []
        history = self.conversation(row)
        if history and history[-1].role == "assistant" and history[-1].content == text:
            return history
        history.append(ConversationTurn(role="assistant", content=text))
        with get_conn() as conn:
            conn.execute(
                "UPDATE planning_sessions SET conversation_history_json = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (dump([item.model_dump(by_alias=True) for item in history]), now_iso(), session_id),
            )
        return history

    def update(
        self,
        session_id: str,
        *,
        status: str | None = None,
        business_status: str | None = None,
        runtime_status: str | None = None,
        repair_count: int | None = None,
        schedule_repair_count: int | None = None,
        cognitive_metadata: Any | None = None,
        expected_version: int | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("business_status", business_status),
            ("runtime_status", runtime_status),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        if repair_count is not None:
            assignments.append("repair_count = ?")
            params.append(max(0, min(int(repair_count), 2)))
        if schedule_repair_count is not None:
            assignments.append("schedule_repair_count = ?")
            params.append(max(0, min(int(schedule_repair_count), 2)))
        if cognitive_metadata is not None:
            assignments.append("cognitive_metadata_json = ?")
            params.append(dump(cognitive_metadata))
        assignments.extend(["version = version + 1", "updated_at = ?"])
        params.extend([now_iso(), session_id])
        where = "id = ?"
        if expected_version is not None:
            where += " AND version = ?"
            params.append(expected_version)
        with get_conn() as conn:
            cursor = conn.execute(f"UPDATE planning_sessions SET {', '.join(assignments)} WHERE {where}", params)
            if expected_version is not None and cursor.rowcount != 1:
                raise ValueError("planning session version changed")

    def mark_written(self, session_id: str) -> None:
        self.update(session_id, status="written_to_calendar", business_status="completed", runtime_status="idle")

    def mark_cancelled(self, session_id: str) -> None:
        self.update(session_id, status="cancelled", business_status="cancelled", runtime_status="idle")


__all__ = ["PlanningPersistence", "dump", "json_list", "json_object", "now_iso"]
