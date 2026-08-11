from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

from ..contracts import Importance, LearningContract, LearningOutcome, LearningScope
from .base import LearningSemanticModel, RouterLearningModel, generated_id


OUTCOME_SYSTEM = """
You generate learning outcomes from one approved LearningScope. Decompose only the explicit userGoal and
targetResult. Do not introduce deployment, distributed systems, advanced engineering, certification, or another
goal unless the scope explicitly asks for it; a genuinely useful future extension may only be optional. Every
outcome needs observable acceptance criteria. Use required for the direct goal, important for a useful enhancement,
and optional for a future extension. Every required or important outcome must be directly traceable to an explicit
phrase in userGoal or targetResult. Do not turn typical framework prerequisites, setup steps, examples, or adjacent
features into additional outcomes unless the scope names them. When a goal enumerates a bounded set of concepts,
treat that list as the boundary. For an ambiguous goal, stay conservative and use only assumptions already present
in the scope. Return semantic fields only. Never return ids, artifact references, versions, timestamps, or source
references. Return JSON only and do not reveal hidden reasoning.
""".strip()


class LearningOutcomeDraft(LearningContract):
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=6)
    importance: Importance


class LearningOutcomeDrafts(LearningContract):
    outcomes: list[LearningOutcomeDraft] = Field(min_length=1, max_length=5)


@dataclass(frozen=True)
class OutcomeGenerationResult:
    outcomes: list[LearningOutcome]
    model_usage: dict[str, Any]


class LearningOutcomeGenerator:
    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model or RouterLearningModel()

    def generate(self, scope: LearningScope) -> OutcomeGenerationResult:
        response = self.model.complete(
            stage="learning_outcomes",
            feature="learning_outcome_generation",
            system=OUTCOME_SYSTEM,
            payload={
                "userGoal": scope.user_goal,
                "targetResult": scope.target_result,
                "currentLevel": scope.current_level.model_dump(by_alias=True),
                "assumptions": [item.model_dump(by_alias=True) for item in scope.assumptions],
                "unknowns": [item.model_dump(by_alias=True) for item in scope.unknowns],
                "confirmed": scope.confirmed,
            },
            response_type=LearningOutcomeDrafts,
            max_tokens=1800,
        )
        outcomes = [
            LearningOutcome(
                id=generated_id("outcome", scope.artifact_id, index, draft.statement),
                statement=draft.statement,
                acceptanceCriteria=draft.acceptance_criteria,
                importance=draft.importance,
                sourceGoalRefs=[scope.artifact_id],
            )
            for index, draft in enumerate(response.value.outcomes)
        ]
        return OutcomeGenerationResult(outcomes=outcomes, model_usage=response.model_usage)


__all__ = [
    "LearningOutcomeDraft",
    "LearningOutcomeDrafts",
    "LearningOutcomeGenerator",
    "OutcomeGenerationResult",
]
