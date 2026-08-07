from __future__ import annotations

from ..contracts import ConversationTurn, UnderstandingSnapshot
from .base import AgentResult, CognitiveModelClient


UNDERSTANDING_SYSTEM = """
You are Planix Understanding Agent. Convert only the current planning conversation into one native
UnderstandingSnapshot. Preserve literal user facts, constraints, preferences, success signals, and conflicts.
Every semantic item needs a stable key, a truthful sourceRef pointing to a user turn, and an appropriate mutation
policy. Never infer a domain or purpose from a city, skill, date, or duration alone. Never invent user facts.

Unknown discovery is semantic, but blocking authority is code-owned. Mark a question blocking only when the core
goal is unknowable, safety is at stake, or missing information makes any realistic plan mathematically impossible.
Other useful unknowns are important or optional and may become explicit assumptions after the question budget.
Use questionBudget 1 for quick, 2 for standard, and 3 for complex goals. Ask at most one nextQuestion per turn.
Do not mark the snapshot confirmed; only the user confirmation endpoint may do that. Return JSON only and do not
reveal hidden reasoning.
""".strip()


class UnderstandingAgent:
    name = "Understanding Agent"
    artifact_type = "understanding_snapshot"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        history: list[ConversationTurn],
        *,
        previous: UnderstandingSnapshot | None = None,
    ) -> AgentResult[UnderstandingSnapshot]:
        result = self.model.complete_contract(
            stage="understanding",
            task_type="planning_understanding",
            feature="planning_understanding",
            system=UNDERSTANDING_SYSTEM,
            payload={
                "conversation": [turn.model_dump(by_alias=True) for turn in history],
                "previousSnapshot": previous.model_dump(by_alias=True) if previous else None,
                "currentTurnRef": f"turn:{sum(1 for turn in history if turn.role == 'user')}",
            },
            contract_type=UnderstandingSnapshot,
            temperature=0.15,
        )
        current = result.artifact
        rounds = (previous.readiness.question_rounds_used + 1) if previous else 0
        budget = {"quick": 1, "standard": 2, "complex": 3}[current.readiness.complexity]
        current = current.model_copy(
            update={
                "artifact_id": previous.artifact_id if previous else current.artifact_id,
                "version": previous.version + 1 if previous else 1,
                "readiness": current.readiness.model_copy(
                    update={
                        "confirmed": False,
                        "question_rounds_used": rounds,
                        "question_budget": budget,
                    }
                ),
            }
        )
        return AgentResult(current, result.model_usage)


__all__ = ["UnderstandingAgent"]
