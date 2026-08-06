from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import uuid4

from .contracts import (
    ApprovalGate,
    ApprovalRecord,
    ArtifactKind,
    ArtifactRef,
    CriticGateResult,
    MemoryCandidate,
    MemoryControllerResult,
    MemoryEvaluation,
    PolicyDecision,
)
from .policy import PolicyEngine
from .quality import MIN_CRITIC_PASS_SCORE, critic_score, meets_critic_score_gate


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_APPROVAL_ARTIFACTS: dict[ApprovalGate, ArtifactKind] = {
    "strategy": "strategy_portfolio",
    "execution": "execution_blueprint",
    "calendar": "execution_blueprint",
}


class HumanApprovalController:
    """Version-bound human approvals. Repaired artifacts invalidate downstream consent."""

    def __init__(self, records: Sequence[ApprovalRecord] = ()):
        self._records = list(records)

    @property
    def records(self) -> list[ApprovalRecord]:
        return list(self._records)

    def request(self, *, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> ApprovalRecord:
        self._validate_binding(session_id=session_id, gate=gate, artifact=artifact)
        existing = next(
            (
                record
                for record in reversed(self._records)
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
            update={
                "status": "approved" if approved else "rejected",
                "decided_at": _now(),
            }
        )
        self._records[index] = updated
        return updated

    def is_approved(self, *, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> bool:
        return any(
            record.approves(session_id=session_id, gate=gate, artifact=artifact)
            for record in self._records
        )

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
        affected = self._affected_gates(repaired_artifact)
        invalidated: list[ApprovalRecord] = []
        for index, record in enumerate(self._records):
            if (
                record.session_id != session_id
                or record.gate not in affected
                or record.status not in {"pending", "approved"}
            ):
                continue
            updated = record.model_copy(
                update={
                    "status": "invalidated",
                    "invalidation_reason": reason,
                }
            )
            self._records[index] = updated
            invalidated.append(updated)
        return invalidated

    @staticmethod
    def _affected_gates(repaired_artifact: ArtifactKind) -> set[ApprovalGate]:
        if repaired_artifact in {
            "user_goal_model",
            "goal_completion",
            "reality_assessment",
            "evidence_pack",
            "strategy_portfolio",
        }:
            return {"strategy", "execution", "calendar"}
        if repaired_artifact == "execution_blueprint":
            return {"execution", "calendar"}
        if repaired_artifact == "critique_report":
            return {"calendar"}
        return set()

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


_REPAIR_TARGETS: dict[str, ArtifactKind] = {
    "goal_modeling": "user_goal_model",
    "goal_intelligence": "user_goal_model",
    "user_goal_model": "user_goal_model",
    "reality": "reality_assessment",
    "reality_assessment": "reality_assessment",
    "context_evidence": "evidence_pack",
    "evidence": "evidence_pack",
    "evidence_pack": "evidence_pack",
    "strategy_architect": "strategy_portfolio",
    "strategy": "strategy_portfolio",
    "strategy_portfolio": "strategy_portfolio",
    "execution_designer": "execution_blueprint",
    "execution": "execution_blueprint",
    "execution_blueprint": "execution_blueprint",
    "resource": "execution_blueprint",
    "schedule": "execution_blueprint",
}


def _value(record: Any, *names: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


class CriticController:
    """Validates that an independent Critic reviewed the current Execution version."""

    def assess(
        self,
        *,
        report: Any,
        critique_artifact: ArtifactRef,
        execution_artifact: ArtifactRef,
        evaluated_execution_artifact: ArtifactRef,
    ) -> CriticGateResult:
        version_matches = (
            execution_artifact.kind == "execution_blueprint"
            and evaluated_execution_artifact.kind == "execution_blueprint"
            and critique_artifact.kind == "critique_report"
            and execution_artifact.session_id == critique_artifact.session_id
            and execution_artifact.same_version(evaluated_execution_artifact)
        )
        status = str(_value(report, "status", default=""))
        score = critic_score(report)
        writable = bool(_value(report, "calendar_writable", "calendarWritable", default=False))
        issues = list(_value(report, "issues", default=[]) or [])
        requests = list(_value(report, "repair_requests", "repairRequests", default=[]) or [])
        has_high_severity_issue = any(
            str(_value(item, "severity", default="")) in {"major", "blocker"}
            for item in issues
        )
        passed = bool(
            version_matches
            and status == "passed"
            and meets_critic_score_gate(report)
            and writable
            and not has_high_severity_issue
            and not requests
        )

        repair_target: ArtifactKind | None = None
        if version_matches and requests:
            raw_target = str(_value(requests[0], "target_agent", "targetAgent", default=""))
            repair_target = _REPAIR_TARGETS.get(raw_target)

        if not version_matches:
            reason = "Critic result is stale because it did not evaluate the current Execution artifact version."
        elif passed:
            reason = "The independent Critic passed the current Execution artifact for Calendar gating."
        elif status == "passed" and score < MIN_CRITIC_PASS_SCORE:
            reason = (
                f"The independent Critic score {score} is below the required "
                f"{MIN_CRITIC_PASS_SCORE} quality threshold."
            )
        elif repair_target:
            reason = f"The independent Critic requested repair of {repair_target}."
        elif status == "passed" and not writable:
            reason = "Critic output is internally inconsistent: passed but not Calendar writable."
        elif status == "passed" and has_high_severity_issue:
            reason = "Critic output is internally inconsistent: passed with a major or blocking issue."
        elif has_high_severity_issue:
            reason = "The independent Critic found a major or blocking issue without an actionable repair target."
        else:
            reason = "The independent Critic did not pass the current Execution artifact."

        return CriticGateResult(
            passed=passed,
            reason=reason,
            critiqueArtifact=critique_artifact,
            executionArtifact=execution_artifact,
            evaluatedExecutionArtifact=evaluated_execution_artifact,
            repairTarget=repair_target,
        )

    def policy_decision(self, gate: CriticGateResult) -> PolicyDecision:
        if gate.passed:
            return PolicyDecision(
                subject="critic_review",
                action="allow",
                allowed=True,
                reason=gate.reason,
                sessionId=gate.execution_artifact.session_id,
                requiredGates=("critic",),
            )
        if gate.repair_target:
            return PolicyDecision(
                subject="critic_review",
                action="repair_artifact",
                allowed=False,
                reason=gate.reason,
                sessionId=gate.execution_artifact.session_id,
                requiredGates=("critic",),
                failedGates=("critic",),
                repairTarget=gate.repair_target,
            )
        return PolicyDecision(
            subject="critic_review",
            action="deny",
            allowed=False,
            reason=gate.reason,
            sessionId=gate.execution_artifact.session_id,
            requiredGates=("critic",),
            failedGates=("critic",),
        )


class MemoryEvaluator(Protocol):
    def evaluate(self, candidate: MemoryCandidate) -> MemoryEvaluation: ...


class MemoryRepository(Protocol):
    def upsert(self, draft: Any, *, positive: bool | None = None) -> Any: ...


class ConservativeMemoryEvaluator:
    """Approve only evidence-backed rules already proposed by a learning artifact.

    The evaluator never derives a rule from raw chat and cannot write memory.
    """

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
            reason=(
                "The versioned learning artifact contains an evidence-backed durable rule."
                if allowed
                else "; ".join(failures)
            ),
            durableRule=statement if allowed else None,
            evidence=evidence if allowed else None,
            confidence=candidate.confidence if allowed else 0,
        )


class MemoryController:
    """The only automatic long-term-memory writer in the Harness path."""

    def __init__(
        self,
        *,
        evaluator: MemoryEvaluator | None = None,
        repository: MemoryRepository | None = None,
        policy: PolicyEngine | None = None,
    ):
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
            return MemoryControllerResult(
                persisted=False,
                evaluation=None,
                policyDecision=decision,
                error=error,
            )
        return self.persist_evaluated(candidate, evaluation)

    def evaluate(self, candidate: MemoryCandidate) -> tuple[MemoryEvaluation | None, str]:
        try:
            raw_evaluation = self.evaluator.evaluate(candidate)
            evaluation = (
                raw_evaluation
                if isinstance(raw_evaluation, MemoryEvaluation)
                else MemoryEvaluation.model_validate(raw_evaluation)
            )
        except Exception as exc:
            return None, str(exc)
        return evaluation, ""

    def persist_evaluated(
        self,
        candidate: MemoryCandidate,
        evaluation: MemoryEvaluation,
    ) -> MemoryControllerResult:
        decision = self.policy.authorize_memory_persistence(candidate=candidate, evaluation=evaluation)
        if not decision.allowed:
            return MemoryControllerResult(
                persisted=False,
                evaluation=evaluation,
                policyDecision=decision,
            )

        # Both canonical and legacy repositories are storage adapters. The
        # evaluated payload carries both field names so neither can bypass the
        # Memory Controller to reinterpret the candidate.
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
            saved = self.repository.upsert(
                draft,
                positive=candidate.evidence_polarity == "positive",
            )
        except Exception as exc:
            return MemoryControllerResult(
                persisted=False,
                evaluation=evaluation,
                policyDecision=decision,
                error=str(exc),
            )
        memory_id = str(_value(saved, "id", default="") or "") or None
        return MemoryControllerResult(
            persisted=True,
            evaluation=evaluation,
            policyDecision=decision,
            memoryId=memory_id,
        )


__all__ = [
    "ConservativeMemoryEvaluator",
    "CriticController",
    "HumanApprovalController",
    "MemoryController",
    "MemoryEvaluator",
    "MemoryRepository",
]
