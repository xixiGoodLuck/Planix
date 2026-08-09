from __future__ import annotations

from ..contracts import ConversationTurn, UnderstandingPatch, UnderstandingSnapshot
from ..planning_services import SemanticMergeService, UnderstandingContextCompactor
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

UNDERSTANDING_PATCH_SYSTEM = """
You are Planix Understanding Agent. Return only an UnderstandingPatch bound to the supplied current snapshot.
Use the latest user message explicitly. Preserve confirmed facts and immutable constraints unless the user clearly
corrects them; use stable semantic keys and patch operations instead of regenerating the whole snapshot. Include at
most one nextQuestion and never mark safety, feasibility, core-goal, or hard-constraint unknowns as assumptions.
Return JSON only.
""".strip()


class UnderstandingAgent:
    name = "Understanding Agent"
    artifact_type = "understanding_snapshot"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()
        self.merge = SemanticMergeService()
        self.compactor = UnderstandingContextCompactor()

    def run(
        self,
        history: list[ConversationTurn],
        *,
        previous: UnderstandingSnapshot | None = None,
    ) -> AgentResult[UnderstandingSnapshot]:
        if previous:
            latest_user = next((turn.content for turn in reversed(history) if turn.role == "user"), "")
            compact = self.compactor.compact(
                previous,
                latest_user_message=latest_user,
                recent_messages=[turn.content for turn in history[-4:]],
            )
            result = self.model.complete_contract(
                stage="understanding",
                task_type="planning_understanding",
                feature="planning_understanding_patch",
                system=UNDERSTANDING_PATCH_SYSTEM,
                payload={"understandingContext": compact.model_dump(by_alias=True), "currentTurnRef": f"turn:{sum(1 for turn in history if turn.role == 'user')}"},
                contract_type=UnderstandingPatch,
                temperature=0.1,
            )
            patch = result.artifact
            current, _ = self.merge.apply(previous, patch)
            readiness = current.readiness.model_copy(
                update={
                    "ready_for_confirmation": patch.ready_for_confirmation if patch.ready_for_confirmation is not None else False,
                    "confirmed": False,
                    "question_rounds_used": previous.readiness.question_rounds_used + 1,
                }
            )
            return AgentResult(current.model_copy(update={"next_question": patch.next_question, "readiness": readiness}), result.model_usage)
        result = self.model.complete_contract(
            stage="understanding",
            task_type="planning_understanding",
            feature="planning_understanding",
            system=UNDERSTANDING_SYSTEM,
            payload={
                "conversation": [turn.model_dump(by_alias=True) for turn in history],
                "previousSnapshot": None,
                "currentTurnRef": f"turn:{sum(1 for turn in history if turn.role == 'user')}",
            },
            contract_type=UnderstandingSnapshot,
            temperature=0.15,
        )
        current = result.artifact
        rounds = 0
        budget = {"quick": 1, "standard": 2, "complex": 3}[current.readiness.complexity]
        current = current.model_copy(
            update={
                "artifact_id": current.artifact_id,
                "version": 1,
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
