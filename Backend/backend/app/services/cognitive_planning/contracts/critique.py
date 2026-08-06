from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import CognitiveContract


class CritiqueDimensions(CognitiveContract):
    user_fit: int = Field(ge=0, le=100)
    goal_alignment: int = Field(ge=0, le=100)
    domain_correctness: int = Field(ge=0, le=100)
    feasibility: int = Field(ge=0, le=100)
    safety: int = Field(ge=0, le=100)
    task_specificity: int = Field(ge=0, le=100)
    resource_actionability: int = Field(ge=0, le=100)
    schedule_fit: int = Field(ge=0, le=100)
    adaptability: int = Field(ge=0, le=100)


class CritiqueIssue(CognitiveContract):
    severity: Literal["blocker", "major", "minor"]
    description: str
    evidence: str
    responsible_agent: Literal[
        "goal_modeling",
        "goal_intelligence",
        "reality",
        "context_evidence",
        "evidence",
        "strategy_architect",
        "strategy",
        "execution_designer",
        "execution",
    ]


class CriticRepairRequest(CognitiveContract):
    target_agent: Literal[
        "goal_modeling",
        "goal_intelligence",
        "reality",
        "context_evidence",
        "evidence",
        "strategy_architect",
        "strategy",
        "execution_designer",
        "execution",
    ]
    instruction: str
    expected_change: str


class PlanCritiqueReport(CognitiveContract):
    status: Literal["passed", "needs_repair", "blocked"]
    score: int = Field(ge=0, le=100)
    dimensions: CritiqueDimensions
    strengths: list[str] = Field(default_factory=list)
    issues: list[CritiqueIssue] = Field(default_factory=list)
    repair_requests: list[CriticRepairRequest] = Field(default_factory=list)
    simulation_summary: str = ""
    remaining_risks: list[str] = Field(default_factory=list)
    calendar_writable: bool = False
    confidence: float = Field(default=0.8, ge=0, le=1)
    # Runtime-owned lineage. Models are not trusted to bind their own review
    # to an Execution artifact; the orchestration layer overwrites these
    # values with the immutable artifact id/version it actually reviewed.
    evaluated_execution_artifact_id: str | None = None
    evaluated_execution_artifact_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def negative_status_requires_findings(self) -> "PlanCritiqueReport":
        if bool(self.evaluated_execution_artifact_id) != bool(
            self.evaluated_execution_artifact_version
        ):
            raise ValueError(
                "evaluated Execution artifact id and version must be supplied together"
            )
        blockers = [issue for issue in self.issues if issue.severity == "blocker"]
        repairable = [issue for issue in self.issues if issue.severity in {"blocker", "major"}]
        if self.status == "passed":
            if not self.calendar_writable:
                raise ValueError("a passed critique must be calendarWritable")
            if repairable:
                raise ValueError("a passed critique cannot include major/blocker issues")
            if self.repair_requests:
                raise ValueError("a passed critique cannot include repair requests")
        if self.status == "blocked" and not blockers:
            raise ValueError("a blocked critique must identify at least one blocker")
        if self.status == "needs_repair" and (not repairable or not self.repair_requests):
            raise ValueError("a repair critique must identify a major/blocker issue and a repair request")
        return self
