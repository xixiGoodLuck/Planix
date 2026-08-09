from __future__ import annotations

from ..contracts import ExecutionOutcome, LearningObservation
from .base import AgentResult, CognitiveModelClient


LEARNING_SYSTEM = """
You are Planix Learning Observer. Convert one real ExecutionOutcome into a tentative LearningObservation.
Preserve status, actual minutes, evidence, blocker/failure reason, source reference, and supplied domain scope.
Do not write durable memory or invent user traits. Return JSON only.
""".strip()


class LearningAgent:
    name = "Learning Observer"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(self, outcome: ExecutionOutcome, *, session_id: str, source_ref: str, domain_scope: list[str]) -> AgentResult[LearningObservation]:
        result = self.model.complete_contract(
            stage="record_learning", task_type="planning_learning", feature="planning_execution_learning",
            system=LEARNING_SYSTEM,
            payload={"executionOutcome": outcome.model_dump(by_alias=True), "sourceArtifactRef": source_ref, "domainScope": domain_scope},
            contract_type=LearningObservation, temperature=0.1,
        )
        observation = result.artifact.model_copy(update={
            "session_id": session_id, "execution_outcome_ref": source_ref, "source_refs": [source_ref],
            "status": outcome.status, "actual_minutes": outcome.actual_minutes,
            "completion_evidence": outcome.completion_evidence, "blocker_reason": outcome.blocker_reason,
            "failure_reason": outcome.failure_reason, "domain_scope": domain_scope,
        })
        return AgentResult(observation, result.model_usage)


__all__ = ["LearningAgent"]
