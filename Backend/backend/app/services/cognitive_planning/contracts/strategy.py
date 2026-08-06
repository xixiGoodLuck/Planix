from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import CognitiveContract
from .evidence import EvidencePack
from .goal_model import UserGoalModel


class StrategyRationale(CognitiveContract):
    why_it_fits_user: str
    evidence_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class StrategyPhase(CognitiveContract):
    title: str
    purpose: str
    outcome: str
    why_this_phase_exists: str


class StrategyOption(CognitiveContract):
    id: str
    name: str
    core_idea: str
    rationale: StrategyRationale
    phases: list[StrategyPhase]
    tradeoffs: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    estimated_effort: str


class StrategyUserDecision(CognitiveContract):
    question: str
    options: list[str] = Field(min_length=1)
    default_recommendation: str


class StrategyPortfolio(CognitiveContract):
    recommended_strategy_id: str
    strategies: list[StrategyOption] = Field(min_length=1)
    recommendation_reason: str
    user_decision: StrategyUserDecision
    approved_strategy_id: str | None = None
    status: Literal["waiting_user_approval", "approved"] = "waiting_user_approval"

    @model_validator(mode="after")
    def validate_strategy_ids(self) -> "StrategyPortfolio":
        strategy_ids = [item.id for item in self.strategies]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("strategy ids must be unique")
        if self.recommended_strategy_id not in strategy_ids:
            raise ValueError("recommendedStrategyId must reference a strategy")
        if self.status == "waiting_user_approval" and self.approved_strategy_id is not None:
            raise ValueError("a strategy waiting for approval cannot have approvedStrategyId")
        if self.status == "approved" and self.approved_strategy_id != self.recommended_strategy_id:
            raise ValueError(
                "an approved strategy must bind approvedStrategyId to recommendedStrategyId"
            )
        return self


class StrategyInput(CognitiveContract):
    goal_model: UserGoalModel
    evidence_pack: EvidencePack
    previous_strategy: StrategyPortfolio | None = None
    user_feedback: str | None = None
