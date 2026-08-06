from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ....db import get_conn
from ..contracts import ConversationTurn


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


class CognitivePlanningPersistence:
    JSON_COLUMNS = {
        "cognitive_metadata": "cognitive_metadata_json",
        "goal_model": "goal_model_json",
        "goal_completion": "goal_completion_json",
        "reality_assessment": "reality_assessment_json",
        "evidence_pack": "evidence_pack_json",
        "strategy_portfolio": "strategy_portfolio_json",
        "execution_blueprint": "execution_blueprint_json",
        "critique_report": "critique_report_json",
        "planning_learning_update": "planning_learning_update_json",
        "user_need_contract": "user_need_contract_json",
        "memory_insight": "memory_insight_json",
        "resource_brief": "resource_brief_json",
        "design_proposal": "design_proposal_json",
        "execution_draft": "execution_draft_json",
        "learning_patch": "latest_learning_patch_json",
        "request_context": "request_context_json",
    }
    SCALAR_COLUMNS = {"approved_strategy_id": "approved_strategy_id"}

    def create(self, *, thread_id: str, user_input: str, context: dict[str, Any] | None = None) -> str:
        session_id = str(uuid4())
        now = now_iso()
        history = [ConversationTurn(role="user", content=user_input).model_dump(by_alias=True)]
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO planning_sessions(
                  id, thread_id, entry_point, status, user_input,
                  conversation_history_json, request_context_json,
                  cognitive_metadata_json, version, created_at, updated_at
                ) VALUES (?, ?, 'p_mode', 'needs_goal_clarification', ?, ?, ?, '{}', 1, ?, ?)
                """,
                (session_id, thread_id, user_input, dump(history), dump(context or {}), now, now),
            )
        return session_id

    def get_row(self, session_id: str):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM planning_sessions WHERE id = ?", (session_id,)).fetchone()

    def latest_active(self, thread_id: str):
        active = {
            "needs_goal_clarification",
            "waiting_design_approval",
            "design_revision",
            "waiting_execution_approval",
            "execution_revision",
            "ready_to_write_calendar",
            "waiting_calendar_write_approval",
            "learning_from_feedback",
            "MODEL_UNAVAILABLE",
        }
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM planning_sessions
                WHERE thread_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
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
                """
                UPDATE planning_sessions
                SET user_input = ?, conversation_history_json = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
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
                """
                UPDATE planning_sessions
                SET conversation_history_json = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
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
        approved_strategy_id: str | None = None,
        clear: tuple[str, ...] = (),
        **values: Any,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if status:
            assignments.append("status = ?")
            params.append(status)
        if business_status is not None:
            assignments.append("business_status = ?")
            params.append(business_status)
        if runtime_status is not None:
            assignments.append("runtime_status = ?")
            params.append(runtime_status)
        if repair_count is not None:
            assignments.append("repair_count = ?")
            params.append(max(0, min(int(repair_count), 2)))
        if approved_strategy_id is not None:
            assignments.append("approved_strategy_id = ?")
            params.append(approved_strategy_id)
        for key in clear:
            column = self.JSON_COLUMNS.get(key)
            if column:
                assignments.append(f"{column} = '{{}}'")
                continue
            column = self.SCALAR_COLUMNS.get(key)
            if column:
                assignments.append(f"{column} = ''")
        for key, value in values.items():
            column = self.JSON_COLUMNS.get(key)
            if not column:
                continue
            assignments.append(f"{column} = ?")
            params.append(dump(value) if value is not None else "{}")
        assignments.extend(["version = version + 1", "updated_at = ?"])
        params.extend([now_iso(), session_id])
        with get_conn() as conn:
            conn.execute(f"UPDATE planning_sessions SET {', '.join(assignments)} WHERE id = ?", params)

    def mark_written(self, session_id: str) -> None:
        self.update(
            session_id,
            status="written_to_calendar",
            business_status="completed",
            runtime_status="idle",
        )

    def mark_cancelled(self, session_id: str) -> None:
        self.update(
            session_id,
            status="cancelled",
            business_status="cancelled",
            runtime_status="idle",
        )
