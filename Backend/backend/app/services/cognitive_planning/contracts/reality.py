from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import CognitiveContract
from .goal_model import ConversationTurn, GoalQuestion, MemoryHint, UserGoalModel


class RealityRisk(CognitiveContract):
    risk: str
    consequence: str
    mitigation: str
    severity: Literal["blocker", "major", "minor"] = "major"


class RealityAssessment(CognitiveContract):
    goal_restatement: str
    feasibility_summary: str
    time_assessment: str
    resource_assessment: str
    hidden_risks: list[RealityRisk] = Field(default_factory=list)
    recommended_adjustments: list[str] = Field(default_factory=list)
    assumptions_to_validate: list[str] = Field(default_factory=list)
    important_questions: list[GoalQuestion] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)
    can_proceed_to_evidence: bool

    @model_validator(mode="after")
    def blocked_reality_requires_user_resolution(self) -> "RealityAssessment":
        if not self.can_proceed_to_evidence and not self.important_questions:
            raise ValueError("a blocked reality assessment must ask a decision-relevant question")
        return self


class RealityAssessmentInput(CognitiveContract):
    goal_model: UserGoalModel
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    user_model_memories: list[MemoryHint] = Field(default_factory=list)
    request_context: dict = Field(default_factory=dict)
