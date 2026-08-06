from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..db import get_conn
from .contracts import HarnessEvent, HarnessEventType
from .state import PersistentCognitiveState


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(value, (list, tuple)):
        value = [
            item.model_dump(by_alias=True, exclude_none=True)
            if isinstance(item, BaseModel)
            else item
            for item in value
        ]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


class HarnessStateNotFound(KeyError):
    pass


class HarnessCheckpointConflict(RuntimeError):
    def __init__(self, session_id: str, expected_version: int, actual_version: int):
        super().__init__(
            f"harness checkpoint conflict for {session_id}: "
            f"expected version {expected_version}, found {actual_version}"
        )
        self.session_id = session_id
        self.expected_version = expected_version
        self.actual_version = actual_version


@dataclass(frozen=True)
class HarnessCheckpointResult:
    state: PersistentCognitiveState
    event: HarnessEvent


class HarnessStateRepository:
    """Durable source of truth for scheduler state and its audit event stream.

    A checkpoint and its corresponding event are committed in the same SQLite
    transaction. ``checkpoint_version`` is an optimistic-lock token, while
    ``sequence`` is monotonic within one planning session.
    """

    def load(self, session_id: str) -> PersistentCognitiveState | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM harness_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._state_from_row(row) if row else None

    def recover(self, session_id: str) -> PersistentCognitiveState:
        """Load the latest committed checkpoint from a fresh runtime instance."""

        state = self.load(session_id)
        if state is None:
            raise HarnessStateNotFound(session_id)
        return state

    def create_or_load(
        self,
        session_id: str,
        initial_state: PersistentCognitiveState | None = None,
    ) -> PersistentCognitiveState:
        if initial_state is not None and initial_state.session_id != session_id:
            raise ValueError("initial harness state belongs to another session")

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM harness_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                return self._state_from_row(row)

            session = conn.execute(
                "SELECT id FROM planning_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session:
                raise HarnessStateNotFound(session_id)

            now = _now()
            seeded = initial_state or PersistentCognitiveState(sessionId=session_id)
            seeded = self._normalized_state(
                seeded,
                checkpoint_version=max(1, int(seeded.checkpoint_version)),
                created_at=seeded.created_at or now,
                updated_at=now,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO harness_states(
                  session_id, lifecycle, current_stage, completed_agents_json,
                  pending_agent, artifact_versions_json, waiting_state,
                  errors_json, recovery_actions_json, approvals_json, repair_target,
                  checkpoint_version, checkpoint_json, last_decision_json,
                  last_policy_decision_json, last_event_sequence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                self._state_values(seeded),
            )
            row = conn.execute(
                "SELECT * FROM harness_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise HarnessStateNotFound(session_id)
        return self._state_from_row(row)

    def checkpoint(
        self,
        state: PersistentCognitiveState,
        *,
        event_type: HarnessEventType,
        agent_id: str | None = None,
        decision: str = "",
        payload: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        expected = int(state.checkpoint_version if expected_version is None else expected_version)
        now = _now()

        with get_conn() as conn:
            current = conn.execute(
                "SELECT * FROM harness_states WHERE session_id = ?",
                (state.session_id,),
            ).fetchone()
            if not current:
                raise HarnessStateNotFound(state.session_id)

            actual = int(current["checkpoint_version"] or 1)
            next_version = actual + 1
            normalized = self._normalized_state(
                state,
                checkpoint_version=next_version,
                created_at=current["created_at"],
                updated_at=now,
            )
            values = self._state_values(normalized)
            cursor = conn.execute(
                """
                UPDATE harness_states
                SET lifecycle = ?,
                    current_stage = ?,
                    completed_agents_json = ?,
                    pending_agent = ?,
                    artifact_versions_json = ?,
                    waiting_state = ?,
                    errors_json = ?,
                    recovery_actions_json = ?,
                    approvals_json = ?,
                    repair_target = ?,
                    checkpoint_version = ?,
                    checkpoint_json = ?,
                    last_decision_json = ?,
                    last_policy_decision_json = ?,
                    last_event_sequence = last_event_sequence + 1,
                    created_at = ?,
                    updated_at = ?
                WHERE session_id = ? AND checkpoint_version = ?
                """,
                (*values[1:], state.session_id, expected),
            )
            if cursor.rowcount != 1:
                latest = conn.execute(
                    "SELECT checkpoint_version FROM harness_states WHERE session_id = ?",
                    (state.session_id,),
                ).fetchone()
                latest_version = int(latest["checkpoint_version"] or 1) if latest else actual
                raise HarnessCheckpointConflict(state.session_id, expected, latest_version)

            sequence_row = conn.execute(
                "SELECT last_event_sequence FROM harness_states WHERE session_id = ?",
                (state.session_id,),
            ).fetchone()
            sequence = int(sequence_row["last_event_sequence"] or 0)
            event = HarnessEvent(
                id=str(uuid4()),
                sessionId=state.session_id,
                sequence=sequence,
                eventType=event_type,
                agentId=agent_id,
                decision=decision,
                payload=payload or {},
                createdAt=now,
            )
            conn.execute(
                """
                INSERT INTO harness_events(
                  id, session_id, sequence, checkpoint_version, event_type,
                  agent_id, decision, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.session_id,
                    event.sequence,
                    next_version,
                    event.event_type,
                    event.agent_id or "",
                    event.decision,
                    _dump(event.payload),
                    event.created_at,
                ),
            )
        return HarnessCheckpointResult(state=normalized, event=event)

    def events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[HarnessEvent]:
        safe_limit = max(1, min(int(limit), 5000))
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM harness_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (session_id, max(0, int(after_sequence)), safe_limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def latest_event_sequence(self, session_id: str) -> int:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT last_event_sequence FROM harness_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise HarnessStateNotFound(session_id)
        return int(row["last_event_sequence"] or 0)

    @staticmethod
    def _normalized_state(
        state: PersistentCognitiveState,
        *,
        checkpoint_version: int,
        created_at: str,
        updated_at: str,
    ) -> PersistentCognitiveState:
        values = state.model_dump(by_alias=True, exclude_none=True)
        values.update(
            {
                "checkpointVersion": checkpoint_version,
                "createdAt": created_at,
                "updatedAt": updated_at,
            }
        )
        return PersistentCognitiveState.model_validate(values)

    @staticmethod
    def _state_values(state: PersistentCognitiveState) -> tuple[Any, ...]:
        return (
            state.session_id,
            state.lifecycle,
            state.current_stage,
            _dump(state.completed_agents),
            state.pending_agent or "",
            _dump(state.artifact_versions),
            state.waiting_state,
            _dump(state.errors),
            _dump(state.recovery_actions),
            _dump(state.approvals),
            state.repair_target or "",
            state.checkpoint_version,
            _dump(state.checkpoint),
            _dump(state.last_decision) if state.last_decision else "{}",
            _dump(state.last_policy_decision) if state.last_policy_decision else "{}",
            state.created_at,
            state.updated_at,
        )

    @staticmethod
    def _state_from_row(row) -> PersistentCognitiveState:
        last_decision = _json_object(row["last_decision_json"])
        last_policy_decision = _json_object(row["last_policy_decision_json"])
        return PersistentCognitiveState.model_validate(
            {
                "sessionId": row["session_id"],
                "lifecycle": row["lifecycle"],
                "currentStage": row["current_stage"],
                "completedAgents": _json_list(row["completed_agents_json"]),
                "pendingAgent": row["pending_agent"] or None,
                "artifactVersions": _json_object(row["artifact_versions_json"]),
                "waitingState": row["waiting_state"],
                "errors": _json_list(row["errors_json"]),
                "recoveryActions": _json_list(row["recovery_actions_json"]),
                "approvals": _json_list(row["approvals_json"]),
                "repairTarget": row["repair_target"] or None,
                "checkpointVersion": int(row["checkpoint_version"] or 1),
                "checkpoint": _json_object(row["checkpoint_json"]),
                "lastDecision": last_decision or None,
                "lastPolicyDecision": last_policy_decision or None,
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )

    @staticmethod
    def _event_from_row(row) -> HarnessEvent:
        return HarnessEvent(
            id=row["id"],
            sessionId=row["session_id"],
            sequence=int(row["sequence"]),
            eventType=row["event_type"],
            agentId=row["agent_id"] or None,
            decision=row["decision"],
            payload=_json_object(row["payload_json"]),
            createdAt=row["created_at"],
        )


__all__ = [
    "HarnessCheckpointConflict",
    "HarnessCheckpointResult",
    "HarnessStateNotFound",
    "HarnessStateRepository",
]
