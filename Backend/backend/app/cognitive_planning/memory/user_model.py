from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from ...db import get_conn
from ..contracts import UserModelMemory, UserModelMemoryDraft


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key(category: str, statement: str) -> str:
    normalized = re.sub(r"\s+", " ", statement.strip().lower())
    return f"{category}:{normalized}"[:700]


def _list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


class UserModelMemoryRepository:
    """Evidence-backed user understanding. Raw notes are not planning rules."""

    def upsert(self, draft, *, positive: bool | None = None) -> UserModelMemory:
        if not isinstance(draft, UserModelMemoryDraft):
            polarity = getattr(draft, "evidence_polarity", "positive")
            if positive is not None:
                polarity = "positive" if positive else "negative"
            draft = UserModelMemoryDraft(
                category=getattr(draft, "category", "planning_hypothesis"),
                statement=getattr(draft, "rule", getattr(draft, "statement", "")),
                domainScope=list(getattr(draft, "domain_scope", [])),
                evidence=getattr(draft, "evidence", ""),
                confidence=float(getattr(draft, "confidence", 0.5)),
                evidencePolarity=polarity,
                expiresAt=getattr(draft, "expires_at", None),
            )
        now = _now()
        statement_key = _key(draft.category, draft.statement)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_model_memories WHERE statement_key = ?",
                (statement_key,),
            ).fetchone()
            if row:
                evidence = _list(row["evidence_json"])
                contradictions = _list(row["contradiction_json"])
                target = evidence if draft.evidence_polarity == "positive" else contradictions
                added = bool(draft.evidence.strip() and draft.evidence not in target)
                if added:
                    target.append(draft.evidence)
                count = int(row["observation_count"] or 1) + int(added)
                confidence = float(row["confidence"] or 0.5)
                if added:
                    confidence = min(0.98, max(0.05, confidence + (0.08 if draft.evidence_polarity == "positive" else -0.14)))
                status = "conflicted" if contradictions else "confirmed" if count >= 2 and confidence >= 0.7 else "tentative"
                domains = list(dict.fromkeys([*_list(row["domain_scope_json"]), *draft.domain_scope]))
                conn.execute(
                    """
                    UPDATE user_model_memories
                    SET domain_scope_json = ?, evidence_json = ?, contradiction_json = ?,
                        observation_count = ?, confidence = ?, status = ?,
                        last_validated_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(domains, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False),
                        json.dumps(contradictions, ensure_ascii=False),
                        count,
                        confidence,
                        status,
                        now,
                        draft.expires_at or row["expires_at"],
                        row["id"],
                    ),
                )
                memory_id = row["id"]
            else:
                memory_id = str(uuid4())
                positive = draft.evidence_polarity == "positive"
                conn.execute(
                    """
                    INSERT INTO user_model_memories(
                      id, category, statement, statement_key, domain_scope_json,
                      evidence_json, contradiction_json, observation_count, confidence,
                      status, source, first_observed_at, last_validated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'ai_inference', ?, ?, ?)
                    """,
                    (
                        memory_id,
                        draft.category,
                        draft.statement,
                        statement_key,
                        json.dumps(draft.domain_scope, ensure_ascii=False),
                        json.dumps([draft.evidence] if positive and draft.evidence else [], ensure_ascii=False),
                        json.dumps([draft.evidence] if not positive and draft.evidence else [], ensure_ascii=False),
                        draft.confidence,
                        "tentative" if positive else "conflicted",
                        now,
                        now,
                        draft.expires_at or "",
                    ),
                )
            current = conn.execute("SELECT * FROM user_model_memories WHERE id = ?", (memory_id,)).fetchone()
        return self._from_row(current)

    def relevant(self, domain: str = "", *, limit: int = 20) -> list[UserModelMemory]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM user_model_memories
                WHERE status IN ('tentative', 'confirmed')
                ORDER BY confidence DESC, last_validated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit * 3, 100)),),
            ).fetchall()
        result: list[UserModelMemory] = []
        now = datetime.now(UTC)
        for row in rows:
            item = self._from_row(row)
            if item.expires_at:
                try:
                    if datetime.fromisoformat(item.expires_at.replace("Z", "+00:00")) <= now:
                        with get_conn() as conn:
                            conn.execute("UPDATE user_model_memories SET status = 'expired' WHERE id = ?", (item.id,))
                        continue
                except ValueError:
                    pass
            if domain and item.domain_scope and domain not in item.domain_scope and "all" not in item.domain_scope:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _from_row(row) -> UserModelMemory:
        return UserModelMemory(
            id=row["id"],
            category=row["category"],
            statement=row["statement"],
            domainScope=_list(row["domain_scope_json"]),
            evidence=_list(row["evidence_json"]),
            contradictions=_list(row["contradiction_json"]),
            observationCount=int(row["observation_count"] or 1),
            confidence=float(row["confidence"] or 0),
            status=row["status"],
            source=row["source"],
            firstObservedAt=row["first_observed_at"],
            lastValidatedAt=row["last_validated_at"],
            expiresAt=row["expires_at"] or None,
        )
