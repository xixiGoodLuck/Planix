from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import CognitiveContract


class ExecutionNarrative(CognitiveContract):
    execution_logic: str
    dependency_explanation: str
    weekly_or_stage_rhythm: str
    workload_reasoning: str
    risk_handling: str


class ExecutionResource(CognitiveContract):
    title: str = Field(min_length=1)
    type: str = Field(min_length=1)
    source_ref: str | None = None
    exact_usage: str = Field(min_length=1)
    expected_contribution: str = Field(min_length=1)
    fallback_resource: str | None = None


class ExecutionBlueprintTask(CognitiveContract):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    action_steps: list[str] = Field(min_length=1)
    estimated_minutes: int = Field(ge=1, le=10080)
    difficulty: Literal["low", "medium", "high"]
    scheduled_date: str | None = None
    schedule_window: str | None = None
    completion_evidence: list[str] = Field(min_length=1)
    deliverable: str = Field(min_length=1)
    resources: list[ExecutionResource] = Field(min_length=1)
    prerequisites: list[str] = Field(default_factory=list)
    risks: list[str] = Field(min_length=1)
    fallback_action: str = Field(min_length=1)
    domain_extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_schedule_representation(self) -> "ExecutionBlueprintTask":
        """Move relative model schedules out of the ISO calendar-date field."""

        if self.scheduled_date:
            try:
                date.fromisoformat(self.scheduled_date)
            except ValueError:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.scheduled_date):
                    raise ValueError("scheduledDate must be a valid ISO calendar date")
                if not self.schedule_window:
                    self.schedule_window = self.scheduled_date
                self.scheduled_date = None
        return self


class ExecutionCheckpoint(CognitiveContract):
    date_or_stage: str
    questions: list[str] = Field(default_factory=list)
    adjustment_rules: list[str] = Field(default_factory=list)


class ExecutionBudgetAllocation(CognitiveContract):
    category: str = Field(min_length=1)
    amount_cny: int = Field(ge=0)


class ExecutionBudgetSummary(CognitiveContract):
    spending_limit_cny: int = Field(ge=1)
    allocations: list[ExecutionBudgetAllocation] = Field(min_length=1)

    @model_validator(mode="after")
    def allocations_must_be_unique_and_within_limit(self) -> "ExecutionBudgetSummary":
        categories = [item.category.strip().casefold() for item in self.allocations]
        if any(not category for category in categories):
            raise ValueError("budget allocation categories cannot be blank")
        if len(categories) != len(set(categories)):
            raise ValueError("budget allocation categories must be unique")
        if sum(item.amount_cny for item in self.allocations) > self.spending_limit_cny:
            raise ValueError("budget allocations cannot exceed spendingLimitCny")
        return self


class ExecutionBlueprint(CognitiveContract):
    narrative: ExecutionNarrative
    tasks: list[ExecutionBlueprintTask] = Field(min_length=1, max_length=10)
    checkpoints: list[ExecutionCheckpoint] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    budget_summary: ExecutionBudgetSummary | None = None
    resource_coverage: Literal["strong", "partial", "weak"]
    status: Literal["draft"] = "draft"
