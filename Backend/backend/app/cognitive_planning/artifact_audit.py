from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..db import get_conn, jsonb
from ..schemas import AgentDecision, AgentMessage, ModelUsage, PlanningArtifact, PlanningBlackboard


ARTIFACT_OWNERS: dict[str, set[str]] = {
    "understanding_snapshot": {"Understanding Agent"},
    "understanding_patch": {"Understanding Agent"},
    "constraint_set": {"Constraint Compiler"},
    "context_pack": {"Context Builder"},
    "plan_blueprint": {"Plan Generator", "Plan Repair Agent"},
    "plan_quality_report": {"Plan Quality Reviewer"},
    "schedule_blueprint": {"Schedule Agent"},
    "schedule_quality_report": {"Schedule Quality Reviewer"},
    "calendar_proposal": {"Calendar Materializer"},
    "final_approval_bundle": {"Final Review Controller"},
    "final_revision_patch": {"Final Review Controller"},
    "execution_outcome": {"Execution Feedback Evaluator"},
    "replan_proposal": {"Execution Feedback Evaluator"},
    "learning_observation": {"Learning Observer"},
    "memory_evaluation": {"Memory Evaluation Agent"},
    "promotion_audit": {"Learning Observer"},
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _content(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    return value if isinstance(value, dict) else {}


class PlanningArtifactAuditStore:
    """Immutable Artifact/decision/message audit log for the native planning runtime."""

    def record_artifact(
        self,
        session_id: str,
        *,
        owner_agent: str,
        artifact_type: str,
        content: Any,
        status: str = "draft",
    ) -> PlanningArtifact:
        allowed = ARTIFACT_OWNERS.get(artifact_type)
        if allowed is None:
            raise ValueError(f"unsupported planning artifact type: {artifact_type}")
        if owner_agent not in allowed:
            raise ValueError(f"{owner_agent} cannot modify {artifact_type}")
        now = _now()
        artifact_id = str(uuid4())
        with get_conn() as conn:
            conn.execute("SELECT id FROM planning_sessions WHERE id = %s FOR UPDATE", (session_id,))
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM planning_artifacts WHERE session_id = %s AND artifact_type = %s",
                (session_id, artifact_type),
            ).fetchone()
            version = int(version_row["next_version"] or 1)
            conn.execute(
                """
                INSERT INTO planning_artifacts(
                  id, session_id, owner_agent, artifact_type, version, status,
                  content_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (artifact_id, session_id, owner_agent, artifact_type, version, status, jsonb(_content(content)), now, now),
            )
            row = conn.execute("SELECT * FROM planning_artifacts WHERE id = %s", (artifact_id,)).fetchone()
        return self._artifact(row)

    def record_decision(
        self,
        session_id: str,
        *,
        agent: str,
        decision: str,
        reason: str,
        summary: str,
        confidence: float = 1,
        input_artifact_ids: list[str] | None = None,
        output_artifact_ids: list[str] | None = None,
        model_usage: ModelUsage | dict[str, Any] | None = None,
    ) -> AgentDecision:
        usage = model_usage.model_dump(by_alias=True) if isinstance(model_usage, ModelUsage) else (model_usage or {})
        decision_id = str(uuid4())
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_decisions(
                  id, session_id, agent, decision, reason, confidence,
                  input_artifact_ids_json, output_artifact_ids_json,
                  user_visible_summary, model_usage_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (decision_id, session_id, agent, decision, reason, confidence,
                 jsonb(input_artifact_ids or []), jsonb(output_artifact_ids or []), summary, jsonb(usage)),
            )
            row = conn.execute("SELECT * FROM agent_decisions WHERE id = %s", (decision_id,)).fetchone()
        return self._decision(row)

    def record_message(
        self,
        session_id: str,
        *,
        from_agent: str,
        to_agent: str,
        message_type: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> AgentMessage:
        message_id = str(uuid4())
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages(
                  id, session_id, from_agent, to_agent, message_type, reason, payload_json, resolved
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (message_id, session_id, from_agent, to_agent, message_type, reason, jsonb(payload or {}), resolved),
            )
            row = conn.execute("SELECT * FROM agent_messages WHERE id = %s", (message_id,)).fetchone()
        return self._message(row)

    def list_artifacts(self, session_id: str) -> list[PlanningArtifact]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM planning_artifacts WHERE session_id = %s ORDER BY created_at, version",
                (session_id,),
            ).fetchall()
        return [self._artifact(row) for row in rows]

    def list_decisions(self, session_id: str) -> list[AgentDecision]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_decisions WHERE session_id = %s ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [self._decision(row) for row in rows]

    def list_messages(self, session_id: str) -> list[AgentMessage]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE session_id = %s ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def blackboard(self, session_id: str, *, status: str = "", user_input_history: list[str] | None = None) -> PlanningBlackboard:
        return PlanningBlackboard(
            sessionId=session_id,
            status=status or "needs_goal_clarification",
            userInputHistory=user_input_history or [],
            artifacts=self.list_artifacts(session_id),
            decisions=self.list_decisions(session_id),
            messages=self.list_messages(session_id),
        )

    @staticmethod
    def _artifact(row) -> PlanningArtifact:
        return PlanningArtifact(
            id=row["id"], sessionId=row["session_id"], ownerAgent=row["owner_agent"],
            artifactType=row["artifact_type"], version=int(row["version"] or 1), status=row["status"],
            contentJson=_object(row["content_json"]), createdAt=row["created_at"], updatedAt=row["updated_at"],
        )

    @staticmethod
    def _decision(row) -> AgentDecision:
        usage = _object(row["model_usage_json"])
        return AgentDecision(
            id=row["id"], sessionId=row["session_id"], agent=row["agent"], decision=row["decision"],
            reason=row["reason"], confidence=float(row["confidence"] or 0),
            inputArtifactIds=_list(row["input_artifact_ids_json"]), outputArtifactIds=_list(row["output_artifact_ids_json"]),
            userVisibleSummary=row["user_visible_summary"], modelUsage=ModelUsage.model_validate(usage) if usage else None,
            createdAt=row["created_at"],
        )

    @staticmethod
    def _message(row) -> AgentMessage:
        return AgentMessage(
            id=row["id"], sessionId=row["session_id"], fromAgent=row["from_agent"], toAgent=row["to_agent"],
            messageType=row["message_type"], reason=row["reason"], payloadJson=_object(row["payload_json"]),
            resolved=bool(row["resolved"]), createdAt=row["created_at"],
        )


__all__ = ["PlanningArtifactAuditStore"]
