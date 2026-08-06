from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import CognitiveContract


GoalCompletionNextStage = Literal["goal_clarification", "evidence", "strategy"]


class GoalCompletionBlockingUnknown(CognitiveContract):
    question: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    answer_options: list[str] = Field(default_factory=list, max_length=4)


class GoalCompletionResult(CognitiveContract):
    complete: bool
    blocking_unknowns: list[GoalCompletionBlockingUnknown] = Field(default_factory=list)
    optional_unknowns: list[str] = Field(default_factory=list)
    next_stage: GoalCompletionNextStage

    @model_validator(mode="after")
    def completion_matches_blockers(self) -> "GoalCompletionResult":
        if self.complete and self.blocking_unknowns:
            raise ValueError("a complete goal cannot retain blocking unknowns")
        if not self.complete and not self.blocking_unknowns:
            raise ValueError("an incomplete goal must identify at least one blocking unknown")
        if self.complete and self.next_stage == "goal_clarification":
            raise ValueError("a complete goal must advance beyond clarification")
        if not self.complete and self.next_stage != "goal_clarification":
            raise ValueError("an incomplete goal must return to goal clarification")
        return self


__all__ = [
    "GoalCompletionBlockingUnknown",
    "GoalCompletionNextStage",
    "GoalCompletionResult",
]
