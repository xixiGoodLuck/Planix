from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from ....db import get_conn
from ..contracts import UserModelHypothesisDraft, UserPlanningHypothesis


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key(statement: str) -> str:
    return re.sub(r"\s+", " ", statement.strip().lower())[:500]


def _load_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


class PlanningHypothesisRepository:
    def upsert(self, draft: UserModelHypothesisDraft, *, positive: bool = True) -> UserPlanningHypothesis:
        now = _now()
        statement_key = _key(draft.rule)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_planning_hypotheses WHERE statement_key = ?",
                (statement_key,),
            ).fetchone()
            if row:
                positive_evidence = _load_list(row["positive_evidence_json"])
                negative_evidence = _load_list(row["negative_evidence_json"])
                target = positive_evidence if positive else negative_evidence
                added = bool(draft.evidence and draft.evidence not in target)
                if added:
                    target.append(draft.evidence)
                count = int(row["evidence_count"] or 1) + (1 if added else 0)
                confidence = float(row["confidence"] or 0.5)
                if added:
                    confidence = min(0.98, max(0.05, confidence + (0.08 if positive else -0.12)))
                status = "conflicted" if negative_evidence else "confirmed" if count >= 2 and confidence >= 0.7 else "tentative"
                domain_scope = list(dict.fromkeys([*_load_list(row["domain_scope_json"]), *draft.domain_scope]))
                conn.execute(
                    """
                    UPDATE user_planning_hypotheses
                    SET domain_scope_json = ?, evidence_count = ?, positive_evidence_json = ?,
                        negative_evidence_json = ?, confidence = ?, status = ?,
                        last_validated_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(domain_scope, ensure_ascii=False),
                        count,
                        json.dumps(positive_evidence, ensure_ascii=False),
                        json.dumps(negative_evidence, ensure_ascii=False),
                        confidence,
                        status,
                        now,
                        draft.expires_at or row["expires_at"],
                        row["id"],
                    ),
                )
                hypothesis_id = row["id"]
            else:
                hypothesis_id = str(uuid4())
                initial_status = "tentative" if positive else "conflicted"
                conn.execute(
                    """
                    INSERT INTO user_planning_hypotheses(
                      id, statement, statement_key, domain_scope_json, evidence_count,
                      positive_evidence_json, negative_evidence_json, confidence, status,
                      first_observed_at, last_validated_at, expires_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hypothesis_id,
                        draft.rule,
                        statement_key,
                        json.dumps(draft.domain_scope, ensure_ascii=False),
                        json.dumps([draft.evidence] if positive and draft.evidence else [], ensure_ascii=False),
                        json.dumps([draft.evidence] if not positive and draft.evidence else [], ensure_ascii=False),
                        draft.confidence,
                        initial_status,
                        now,
                        now,
                        draft.expires_at or "",
                    ),
                )
            current = conn.execute("SELECT * FROM user_planning_hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
        return self._from_row(current)

    def relevant(self, domain: str, *, limit: int = 12) -> list[UserPlanningHypothesis]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM user_planning_hypotheses
                WHERE status IN ('tentative', 'confirmed')
                ORDER BY confidence DESC, last_validated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit * 3, 100)),),
            ).fetchall()
        result: list[UserPlanningHypothesis] = []
        now = datetime.now(UTC)
        for row in rows:
            item = self._from_row(row)
            if not item.is_active(now):
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE user_planning_hypotheses SET status = 'expired', last_validated_at = ? WHERE id = ?",
                        (_now(), item.id),
                    )
                continue
            if domain and item.domain_scope and domain not in item.domain_scope and "all" not in item.domain_scope:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def _from_row(self, row) -> UserPlanningHypothesis:
        return UserPlanningHypothesis(
            id=row["id"],
            statement=row["statement"],
            domainScope=_load_list(row["domain_scope_json"]),
            evidenceCount=int(row["evidence_count"] or 1),
            positiveEvidence=_load_list(row["positive_evidence_json"]),
            negativeEvidence=_load_list(row["negative_evidence_json"]),
            confidence=float(row["confidence"] or 0),
            status=row["status"],
            firstObservedAt=row["first_observed_at"],
            lastValidatedAt=row["last_validated_at"],
            expiresAt=row["expires_at"] or None,
        )
