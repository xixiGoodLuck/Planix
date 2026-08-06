from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import CognitiveContract


class LearningDiagnosis(CognitiveContract):
    failed_assumption: str
    failure_stage: Literal[
        "goal_modeling",
        "context_evidence",
        "strategy",
        "execution",
        "resource",
        "schedule",
        "ui",
    ]
    root_cause: str


class CurrentPlanPatch(CognitiveContract):
    target_artifact: str
    instruction: str


class UserModelHypothesisDraft(CognitiveContract):
    category: Literal[
        "fact",
        "habit",
        "preference",
        "constraint",
        "failure_pattern",
        "planning_hypothesis",
    ] = "planning_hypothesis"
    rule: str
    domain_scope: list[str] = Field(default_factory=list)
    evidence: str
    confidence: float = Field(ge=0, le=1)
    evidence_polarity: Literal["positive", "negative"] = "positive"
    expires_at: str | None = None


class PlanningLearningUpdate(CognitiveContract):
    original_feedback: str
    diagnosis: LearningDiagnosis
    current_plan_patch: CurrentPlanPatch | None = None
    user_model_hypothesis: UserModelHypothesisDraft | None = None
    should_persist: bool


class UserPlanningHypothesis(CognitiveContract):
    id: str
    statement: str
    domain_scope: list[str] = Field(default_factory=list)
    evidence_count: int = Field(default=1, ge=1)
    positive_evidence: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    status: Literal["tentative", "confirmed", "conflicted", "expired"] = "tentative"
    first_observed_at: str
    last_validated_at: str
    expires_at: str | None = None

    def is_active(self, now: datetime) -> bool:
        if self.status == "expired":
            return False
        if not self.expires_at:
            return True
        try:
            return datetime.fromisoformat(self.expires_at.replace("Z", "+00:00")) > now
        except ValueError:
            return True
