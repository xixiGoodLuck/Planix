from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...services.cognitive_planning.contracts import (
    EvidencePack,
    ExecutionBlueprint,
    GoalModelingInput,
    GoalQuestion,
    PlanCritiqueReport,
    PlanningLearningUpdate,
    RealityAssessment,
    RealityAssessmentInput,
    StrategyPortfolio,
    UserGoalModel,
)
from ...services.cognitive_planning.contracts.base import CognitiveContract


GoalUnderstandingArtifact = UserGoalModel
StrategyProposal = StrategyPortfolio
ExecutionPlanArtifact = ExecutionBlueprint
CriticReport = PlanCritiqueReport

UserModelCategory = Literal[
    "fact",
    "habit",
    "preference",
    "constraint",
    "failure_pattern",
    "planning_hypothesis",
]


class UserModelMemoryDraft(CognitiveContract):
    category: UserModelCategory
    statement: str
    domain_scope: list[str] = Field(default_factory=list)
    evidence: str
    confidence: float = Field(ge=0, le=1)
    evidence_polarity: Literal["positive", "negative"] = "positive"
    expires_at: str | None = None


class UserModelMemory(CognitiveContract):
    id: str
    category: UserModelCategory
    statement: str
    domain_scope: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    observation_count: int = Field(default=1, ge=1)
    confidence: float = Field(ge=0, le=1)
    status: Literal["tentative", "confirmed", "conflicted", "expired"] = "tentative"
    source: str = "ai_inference"
    first_observed_at: str
    last_validated_at: str
    expires_at: str | None = None


__all__ = [
    "CriticReport",
    "EvidencePack",
    "ExecutionPlanArtifact",
    "GoalModelingInput",
    "GoalQuestion",
    "GoalUnderstandingArtifact",
    "PlanningLearningUpdate",
    "RealityAssessment",
    "RealityAssessmentInput",
    "StrategyProposal",
    "UserModelMemory",
    "UserModelMemoryDraft",
]
