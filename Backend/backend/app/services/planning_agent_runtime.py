from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..db import get_conn
from ..schemas import (
    AgentDecision,
    AgentMessage,
    ModelUsage,
    PlanningArtifact,
    PlanningArtifactStatus,
    PlanningBlackboard,
)


# Compatibility persistence protocol. New planning cognition belongs in
# CognitivePlanningRuntime; this service remains for artifact/decision/message
# audit records and replay only.
PLANNING_AGENT_RUNTIME_STATUS = "deprecated-compatibility-protocol"


ARTIFACT_OWNER: dict[str, str] = {
    "user_need_contract": "User Advocate Agent",
    "memory_insight_brief": "Memory Insight Agent",
    "resource_brief": "Resource Intelligence Agent",
    "plan_design_proposal": "Plan Co-Designer Agent",
    "execution_plan_draft": "Execution Planner Agent",
    "learning_patch": "Feedback Evolution Agent",
    "user_goal_model": "Goal Modeling Agent",
    "goal_completion": "Goal Completion Judge",
    "reality_assessment": "Reality Agent",
    "evidence_pack": "Context & Evidence Agent",
    "strategy_portfolio": "Strategy Architect Agent",
    "execution_blueprint": "Execution Designer Agent",
    "critique_report": "Independent Critic & Learning Agent",
    "planning_learning_update": "Independent Critic & Learning Agent",
    "memory_evaluation": "Memory Evaluation Agent",
}

ARTIFACT_OWNER_ALIASES: dict[str, set[str]] = {
    "user_goal_model": {"Goal Modeling Agent", "Goal Intelligence Agent"},
    "evidence_pack": {"Context & Evidence Agent", "Evidence Agent"},
    "strategy_portfolio": {"Strategy Architect Agent", "Strategy Agent"},
    "execution_blueprint": {"Execution Designer Agent", "Execution Agent"},
    "critique_report": {"Independent Critic & Learning Agent", "Critic Agent"},
    "planning_learning_update": {"Independent Critic & Learning Agent", "Critic Agent"},
    "memory_evaluation": {"Memory Evaluation Agent"},
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _jsonable(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    return value if isinstance(value, dict) else {}


class PlanningAgentRuntime:
    def stage_execution_review(
        self,
        session_id: str,
        *,
        execution_owner: str,
        critique_owner: str,
        execution: Any,
        pending_critique: Any,
    ) -> tuple[PlanningArtifact, PlanningArtifact]:
        """Atomically create one Execution version and its one Critique slot.

        The Critique starts as a safe, blocked review placeholder.  The
        independent Critic later finalizes this same artifact id/version.  A
        crash or model failure therefore cannot leave a formal Execution
        version without a corresponding Critique artifact.
        """

        if execution_owner not in ARTIFACT_OWNER_ALIASES["execution_blueprint"]:
            raise ValueError(f"{execution_owner} cannot modify execution_blueprint")
        if critique_owner not in ARTIFACT_OWNER_ALIASES["critique_report"]:
            raise ValueError(f"{critique_owner} cannot modify critique_report")

        now = _now()
        execution_id = str(uuid4())
        critique_id = str(uuid4())
        execution_content = _jsonable(execution)
        critique_content = _jsonable(pending_critique)
        with get_conn() as conn:
            execution_version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM planning_artifacts WHERE session_id = ? AND artifact_type = 'execution_blueprint'",
                (session_id,),
            ).fetchone()
            critique_version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM planning_artifacts WHERE session_id = ? AND artifact_type = 'critique_report'",
                (session_id,),
            ).fetchone()
            execution_version = int(execution_version_row["next_version"] or 1)
            critique_version = int(critique_version_row["next_version"] or 1)
            critique_content.update(
                {
                    "evaluatedExecutionArtifactId": execution_id,
                    "evaluatedExecutionArtifactVersion": execution_version,
                }
            )
            conn.execute(
                """
                INSERT INTO planning_artifacts(
                  id, session_id, owner_agent, artifact_type, version, status,
                  content_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'execution_blueprint', ?, 'draft', ?, ?, ?)
                """,
                (
                    execution_id,
                    session_id,
                    execution_owner,
                    execution_version,
                    _dump(execution_content),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO planning_artifacts(
                  id, session_id, owner_agent, artifact_type, version, status,
                  content_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'critique_report', ?, 'blocked', ?, ?, ?)
                """,
                (
                    critique_id,
                    session_id,
                    critique_owner,
                    critique_version,
                    _dump(critique_content),
                    now,
                    now,
                ),
            )
            # Keep the compatibility projection in the same transaction as
            # the formal pair so a fresh runtime can always recover both.
            conn.execute(
                """
                UPDATE planning_sessions
                SET execution_blueprint_json = ?, critique_report_json = ?,
                    business_status = 'execution_pending', runtime_status = 'running',
                    version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    _dump(execution_content),
                    _dump(critique_content),
                    now,
                    session_id,
                ),
            )
            execution_row = conn.execute(
                "SELECT * FROM planning_artifacts WHERE id = ?", (execution_id,)
            ).fetchone()
            critique_row = conn.execute(
                "SELECT * FROM planning_artifacts WHERE id = ?", (critique_id,)
            ).fetchone()
        return self._artifact_from_row(execution_row), self._artifact_from_row(critique_row)

    def finalize_execution_review(
        self,
        session_id: str,
        *,
        critique_artifact_id: str,
        execution_artifact_id: str,
        critique: Any,
        status: PlanningArtifactStatus,
    ) -> PlanningArtifact:
        """Finalize the unique Critique slot bound to an Execution version."""

        content = _jsonable(critique)
        now = _now()
        with get_conn() as conn:
            execution_row = conn.execute(
                """
                SELECT * FROM planning_artifacts
                WHERE id = ? AND session_id = ? AND artifact_type = 'execution_blueprint'
                """,
                (execution_artifact_id, session_id),
            ).fetchone()
            critique_row = conn.execute(
                """
                SELECT * FROM planning_artifacts
                WHERE id = ? AND session_id = ? AND artifact_type = 'critique_report'
                """,
                (critique_artifact_id, session_id),
            ).fetchone()
            if not execution_row or not critique_row:
                raise ValueError("execution review slot is missing or belongs to another session")
            existing = _json_dict(critique_row["content_json"])
            if (
                existing.get("evaluatedExecutionArtifactId") != execution_artifact_id
                or int(existing.get("evaluatedExecutionArtifactVersion") or 0)
                != int(execution_row["version"] or 0)
            ):
                raise ValueError("critique slot is not bound to the current Execution artifact")
            content.update(
                {
                    "evaluatedExecutionArtifactId": execution_artifact_id,
                    "evaluatedExecutionArtifactVersion": int(execution_row["version"]),
                }
            )
            conn.execute(
                """
                UPDATE planning_artifacts
                SET status = ?, content_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _dump(content), now, critique_artifact_id),
            )
            conn.execute(
                """
                UPDATE planning_sessions
                SET critique_report_json = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (_dump(content), now, session_id),
            )
            result = conn.execute(
                "SELECT * FROM planning_artifacts WHERE id = ?", (critique_artifact_id,)
            ).fetchone()
        return self._artifact_from_row(result)

    def ensure_execution_review_slot(
        self,
        session_id: str,
        *,
        execution_artifact_id: str,
        critique_owner: str,
        pending_critique: Any,
    ) -> PlanningArtifact:
        """Return or create the sole Critique slot for a legacy Execution.

        New executions use :meth:`stage_execution_review`; this compatibility
        path lets an older persisted Execution resume safely at Critic without
        first creating a second formal Execution version.
        """

        if critique_owner not in ARTIFACT_OWNER_ALIASES["critique_report"]:
            raise ValueError(f"{critique_owner} cannot modify critique_report")
        now = _now()
        with get_conn() as conn:
            execution_row = conn.execute(
                """
                SELECT * FROM planning_artifacts
                WHERE id = ? AND session_id = ? AND artifact_type = 'execution_blueprint'
                """,
                (execution_artifact_id, session_id),
            ).fetchone()
            if not execution_row:
                raise ValueError("current Execution artifact is missing")
            rows = conn.execute(
                """
                SELECT * FROM planning_artifacts
                WHERE session_id = ? AND artifact_type = 'critique_report'
                ORDER BY version DESC, created_at DESC, id DESC
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                content = _json_dict(row["content_json"])
                if content.get("evaluatedExecutionArtifactId") == execution_artifact_id:
                    return self._artifact_from_row(row)

            # Pre-lineage releases bound Critic output through immutable
            # AgentDecision input/output ids. Upgrade that existing artifact
            # in place rather than creating a duplicate review for the same
            # Execution version.
            critique_rows = {str(row["id"]): row for row in rows}
            decisions = conn.execute(
                """
                SELECT input_artifact_ids_json, output_artifact_ids_json
                FROM agent_decisions
                WHERE session_id = ?
                  AND agent IN ('Critic Agent', 'Independent Critic & Learning Agent')
                ORDER BY rowid DESC
                """,
                (session_id,),
            ).fetchall()
            for decision in decisions:
                if execution_artifact_id not in _json_list(
                    decision["input_artifact_ids_json"]
                ):
                    continue
                matched = next(
                    (
                        critique_rows[item_id]
                        for item_id in _json_list(decision["output_artifact_ids_json"])
                        if item_id in critique_rows
                    ),
                    None,
                )
                if matched is None:
                    continue
                content = _json_dict(matched["content_json"])
                content.update(
                    {
                        "evaluatedExecutionArtifactId": execution_artifact_id,
                        "evaluatedExecutionArtifactVersion": int(execution_row["version"]),
                    }
                )
                conn.execute(
                    "UPDATE planning_artifacts SET content_json = ?, updated_at = ? WHERE id = ?",
                    (_dump(content), now, matched["id"]),
                )
                conn.execute(
                    """
                    UPDATE planning_sessions
                    SET critique_report_json = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (_dump(content), now, session_id),
                )
                upgraded = conn.execute(
                    "SELECT * FROM planning_artifacts WHERE id = ?", (matched["id"],)
                ).fetchone()
                return self._artifact_from_row(upgraded)

            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM planning_artifacts WHERE session_id = ? AND artifact_type = 'critique_report'",
                (session_id,),
            ).fetchone()
            critique_id = str(uuid4())
            content = _jsonable(pending_critique)
            content.update(
                {
                    "evaluatedExecutionArtifactId": execution_artifact_id,
                    "evaluatedExecutionArtifactVersion": int(execution_row["version"]),
                }
            )
            conn.execute(
                """
                INSERT INTO planning_artifacts(
                  id, session_id, owner_agent, artifact_type, version, status,
                  content_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'critique_report', ?, 'blocked', ?, ?, ?)
                """,
                (
                    critique_id,
                    session_id,
                    critique_owner,
                    int(version_row["next_version"] or 1),
                    _dump(content),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE planning_sessions
                SET critique_report_json = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (_dump(content), now, session_id),
            )
            result = conn.execute(
                "SELECT * FROM planning_artifacts WHERE id = ?", (critique_id,)
            ).fetchone()
        return self._artifact_from_row(result)

    def record_artifact(
        self,
        session_id: str,
        *,
        owner_agent: str,
        artifact_type: str,
        content: Any,
        status: str = "draft",
    ) -> PlanningArtifact:
        expected_owner = ARTIFACT_OWNER.get(artifact_type)
        allowed_owners = ARTIFACT_OWNER_ALIASES.get(artifact_type, {expected_owner} if expected_owner else set())
        if expected_owner and owner_agent not in allowed_owners:
            raise ValueError(f"{owner_agent} cannot modify {artifact_type}; owner is {expected_owner}")
        now = _now()
        artifact_id = str(uuid4())
        with get_conn() as conn:
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM planning_artifacts WHERE session_id = ? AND artifact_type = ?",
                (session_id, artifact_type),
            ).fetchone()
            version = int(version_row["next_version"] or 1)
            conn.execute(
                """
                INSERT INTO planning_artifacts(
                  id, session_id, owner_agent, artifact_type, version, status,
                  content_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, session_id, owner_agent, artifact_type, version, status, _dump(_jsonable(content)), now, now),
            )
            row = conn.execute("SELECT * FROM planning_artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._artifact_from_row(row)

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
        decision_id = str(uuid4())
        usage_payload: dict[str, Any] = {}
        if isinstance(model_usage, ModelUsage):
            usage_payload = model_usage.model_dump(by_alias=True)
        elif isinstance(model_usage, dict):
            usage_payload = model_usage
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_decisions(
                  id, session_id, agent, decision, reason, confidence,
                  input_artifact_ids_json, output_artifact_ids_json,
                  user_visible_summary, model_usage_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    session_id,
                    agent,
                    decision,
                    reason,
                    confidence,
                    _dump(input_artifact_ids or []),
                    _dump(output_artifact_ids or []),
                    summary,
                    _dump(usage_payload),
                ),
            )
            row = conn.execute("SELECT * FROM agent_decisions WHERE id = ?", (decision_id,)).fetchone()
        return self._decision_from_row(row)

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
                  id, session_id, from_agent, to_agent, message_type, reason,
                  payload_json, resolved
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, from_agent, to_agent, message_type, reason, _dump(payload or {}), 1 if resolved else 0),
            )
            row = conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone()
        return self._message_from_row(row)

    def list_artifacts(self, session_id: str) -> list[PlanningArtifact]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM planning_artifacts WHERE session_id = ? ORDER BY created_at, version",
                (session_id,),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def list_decisions(self, session_id: str) -> list[AgentDecision]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_decisions WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [self._decision_from_row(row) for row in rows]

    def list_messages(self, session_id: str) -> list[AgentMessage]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def blackboard(self, session_id: str, *, status: str = "", user_input_history: list[str] | None = None) -> PlanningBlackboard:
        return PlanningBlackboard(
            sessionId=session_id,
            status=status or "needs_goal_clarification",
            userInputHistory=user_input_history or [],
            artifacts=self.list_artifacts(session_id),
            decisions=self.list_decisions(session_id),
            messages=self.list_messages(session_id),
        )

    def _artifact_from_row(self, row) -> PlanningArtifact:
        return PlanningArtifact(
            id=row["id"],
            sessionId=row["session_id"],
            ownerAgent=row["owner_agent"],
            artifactType=row["artifact_type"],
            version=int(row["version"] or 1),
            status=row["status"],
            contentJson=_json_dict(row["content_json"]),
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )

    def _decision_from_row(self, row) -> AgentDecision:
        usage = _json_dict(row["model_usage_json"])
        return AgentDecision(
            id=row["id"],
            sessionId=row["session_id"],
            agent=row["agent"],
            decision=row["decision"],
            reason=row["reason"],
            confidence=float(row["confidence"] or 0),
            inputArtifactIds=_json_list(row["input_artifact_ids_json"]),
            outputArtifactIds=_json_list(row["output_artifact_ids_json"]),
            userVisibleSummary=row["user_visible_summary"],
            modelUsage=ModelUsage.model_validate(usage) if usage else None,
            createdAt=row["created_at"],
        )

    def _message_from_row(self, row) -> AgentMessage:
        return AgentMessage(
            id=row["id"],
            sessionId=row["session_id"],
            fromAgent=row["from_agent"],
            toAgent=row["to_agent"],
            messageType=row["message_type"],
            reason=row["reason"],
            payloadJson=_json_dict(row["payload_json"]),
            resolved=bool(row["resolved"]),
            createdAt=row["created_at"],
        )
