from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import uuid4

from .contracts import (
    ApprovalGate,
    ApprovalRecord,
    ArtifactKind,
    ArtifactRef,
    MemoryCandidate,
    MemoryControllerResult,
    MemoryEvaluation,
)
from .policy import PolicyEngine


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_APPROVAL_ARTIFACTS: dict[ApprovalGate, ArtifactKind] = {"calendar": "final_approval_bundle"}


class HumanApprovalController:
    """Version-bound Calendar permission over the current FinalApprovalBundle."""

    def __init__(self, records: Sequence[ApprovalRecord] = ()):
        self._records = list(records)

    @property
    def records(self) -> list[ApprovalRecord]:
        return list(self._records)

    def request(self, *, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> ApprovalRecord:
        self._validate_binding(session_id=session_id, gate=gate, artifact=artifact)
        existing = next(
            (
                record for record in reversed(self._records)
                if record.session_id == session_id
                and record.gate == gate
                and record.artifact.same_version(artifact)
                and record.status in {"pending", "approved"}
            ),
            None,
        )
        if existing:
            return existing
        record = ApprovalRecord(
            id=str(uuid4()),
            sessionId=session_id,
            gate=gate,
            artifact=artifact,
            status="pending",
            createdAt=_now(),
        )
        self._records.append(record)
        return record

    def decide(self, approval_id: str, *, approved: bool) -> ApprovalRecord:
        index = self._index(approval_id)
        current = self._records[index]
        if current.status != "pending":
            raise ValueError(f"approval {approval_id} is not pending")
        updated = current.model_copy(
            update={"status": "approved" if approved else "rejected", "decided_at": _now()}
        )
        self._records[index] = updated
        return updated

    def is_approved(self, *, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> bool:
        return any(record.approves(session_id=session_id, gate=gate, artifact=artifact) for record in self._records)

    def consume(self, approval_id: str) -> ApprovalRecord:
        index = self._index(approval_id)
        current = self._records[index]
        if current.status != "approved":
            raise ValueError(f"approval {approval_id} is not approved")
        updated = current.model_copy(update={"status": "consumed"})
        self._records[index] = updated
        return updated

    def invalidate_after_repair(
        self,
        *,
        session_id: str,
        repaired_artifact: ArtifactKind,
        reason: str = "Artifact changed after approval.",
    ) -> list[ApprovalRecord]:
        if repaired_artifact not in {
            "understanding_snapshot",
            "constraint_set",
            "context_pack",
            "plan_blueprint",
            "plan_quality_report",
            "schedule_blueprint",
            "schedule_quality_report",
            "calendar_proposal",
            "final_approval_bundle",
        }:
            return []
        invalidated: list[ApprovalRecord] = []
        for index, record in enumerate(self._records):
            if record.session_id != session_id or record.gate != "calendar" or record.status not in {"pending", "approved"}:
                continue
            updated = record.model_copy(update={"status": "invalidated", "invalidation_reason": reason})
            self._records[index] = updated
            invalidated.append(updated)
        return invalidated

    @staticmethod
    def _validate_binding(*, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> None:
        if artifact.session_id != session_id:
            raise ValueError("approval artifact belongs to another session")
        expected = _APPROVAL_ARTIFACTS[gate]
        if artifact.kind != expected:
            raise ValueError(f"{gate} approval must bind to {expected}, not {artifact.kind}")

    def _index(self, approval_id: str) -> int:
        for index, record in enumerate(self._records):
            if record.id == approval_id:
                return index
        raise KeyError(f"approval not found: {approval_id}")


class MemoryEvaluator(Protocol):
    def evaluate(self, candidate: MemoryCandidate) -> MemoryEvaluation: ...


class MemoryRepository(Protocol):
    def upsert(self, draft: Any, *, positive: bool | None = None) -> Any: ...


class ConservativeMemoryEvaluator:
    minimum_confidence = 0.60

    def evaluate(self, candidate: MemoryCandidate) -> MemoryEvaluation:
        statement = candidate.statement.strip()
        evidence = candidate.evidence.strip()
        failures: list[str] = []
        if len(statement) < 8:
            failures.append("candidate is not a meaningful durable rule")
        if len(evidence) < 4:
            failures.append("candidate has no concrete evidence")
        if candidate.confidence < self.minimum_confidence:
            failures.append("candidate confidence is below the durable-memory threshold")
        allowed = not failures
        return MemoryEvaluation(
            id=str(uuid4()),
            sessionId=candidate.session_id,
            candidateId=candidate.id,
            sourceArtifact=candidate.source_artifact,
            evaluatorAgentId="memory_evaluator",
            allowed=allowed,
            reason="The versioned observation contains an evidence-backed durable rule." if allowed else "; ".join(failures),
            durableRule=statement if allowed else None,
            evidence=evidence if allowed else None,
            confidence=candidate.confidence if allowed else 0,
        )


class MemoryController:
    """The only automatic long-term-memory writer in the Harness path."""

    def __init__(self, *, evaluator: MemoryEvaluator | None = None, repository: MemoryRepository | None = None, policy: PolicyEngine | None = None):
        if repository is None:
            from ..cognitive_planning.memory.user_model import UserModelMemoryRepository

            repository = UserModelMemoryRepository()
        self.evaluator = evaluator or ConservativeMemoryEvaluator()
        self.repository = repository
        self.policy = policy or PolicyEngine()

    def evaluate_and_persist(self, candidate: MemoryCandidate) -> MemoryControllerResult:
        evaluation, error = self.evaluate(candidate)
        if evaluation is None:
            decision = self.policy.authorize_memory_persistence(candidate=candidate, evaluation=None)
            return MemoryControllerResult(persisted=False, evaluation=None, policyDecision=decision, error=error)
        return self.persist_evaluated(candidate, evaluation)

    def evaluate(self, candidate: MemoryCandidate) -> tuple[MemoryEvaluation | None, str]:
        try:
            raw = self.evaluator.evaluate(candidate)
            evaluation = raw if isinstance(raw, MemoryEvaluation) else MemoryEvaluation.model_validate(raw)
        except Exception as exc:
            return None, str(exc)
        return evaluation, ""

    def persist_evaluated(self, candidate: MemoryCandidate, evaluation: MemoryEvaluation) -> MemoryControllerResult:
        decision = self.policy.authorize_memory_persistence(candidate=candidate, evaluation=evaluation)
        if not decision.allowed:
            return MemoryControllerResult(persisted=False, evaluation=evaluation, policyDecision=decision)
        draft = SimpleNamespace(
            category=candidate.category,
            statement=str(evaluation.durable_rule or "").strip(),
            rule=str(evaluation.durable_rule or "").strip(),
            domain_scope=candidate.domain_scope,
            evidence=str(evaluation.evidence or "").strip(),
            confidence=evaluation.confidence,
            evidence_polarity=candidate.evidence_polarity,
            expires_at=candidate.expires_at,
        )
        try:
            saved = self.repository.upsert(draft, positive=candidate.evidence_polarity == "positive")
        except Exception as exc:
            return MemoryControllerResult(persisted=False, evaluation=evaluation, policyDecision=decision, error=str(exc))
        return MemoryControllerResult(
            persisted=True,
            evaluation=evaluation,
            policyDecision=decision,
            memoryId=str(getattr(saved, "id", "") or "") or None,
        )


__all__ = [
    "ConservativeMemoryEvaluator",
    "HumanApprovalController",
    "MemoryController",
    "MemoryEvaluator",
    "MemoryRepository",
]
