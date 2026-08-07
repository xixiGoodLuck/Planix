from __future__ import annotations

"""Typed contracts for the canonical Planix planning flow."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ...services.cognitive_planning.contracts.base import CognitiveContract


def new_artifact_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SourceType = Literal[
    "user_confirmed",
    "tool_verified",
    "uploaded_material",
    "execution_observation",
    "memory_confirmed",
    "historical_context",
    "model_assumption",
]
MutationPolicy = Literal[
    "immutable",
    "auto_adjust",
    "auto_replace",
    "reduce_with_disclosure",
    "user_confirmation_required",
]
SemanticStatus = Literal["active", "superseded", "removed"]


class SemanticItem(CognitiveContract):
    id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    source_type: SourceType
    source_ref: str = Field(min_length=1)
    confidence: float = Field(default=1, ge=0, le=1)
    mutation_policy: MutationPolicy = "user_confirmation_required"
    supersedes: str | None = None
    status: SemanticStatus = "active"


class UnderstandingQuestion(CognitiveContract):
    question: str = Field(min_length=1)
    why_this_question_matters: str = Field(min_length=1)
    expected_decision_impact: str = Field(min_length=1)
    priority: Literal["blocking", "important", "optional"]
    answer_options: list[str] = Field(default_factory=list, max_length=4)


class UnderstandingReadiness(CognitiveContract):
    ready_for_confirmation: bool = False
    confirmed: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    question_rounds_used: int = Field(default=0, ge=0)
    question_budget: int = Field(default=2, ge=1, le=4)
    complexity: Literal["quick", "standard", "complex"] = "standard"


class UnderstandingSnapshot(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("understanding"))
    version: int = Field(default=1, ge=1)
    goal_summary: str = Field(min_length=1)
    facts: list[SemanticItem] = Field(default_factory=list)
    constraints: list[SemanticItem] = Field(default_factory=list)
    preferences: list[SemanticItem] = Field(default_factory=list)
    success_signals: list[SemanticItem] = Field(default_factory=list)
    assumptions: list[SemanticItem] = Field(default_factory=list)
    unknowns: list[SemanticItem] = Field(default_factory=list)
    conflicts: list[SemanticItem] = Field(default_factory=list)
    next_question: UnderstandingQuestion | None = None
    readiness: UnderstandingReadiness = Field(default_factory=UnderstandingReadiness)
    source_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def stable_keys_are_unique_per_section(self) -> "UnderstandingSnapshot":
        for name in (
            "facts",
            "constraints",
            "preferences",
            "success_signals",
            "assumptions",
            "unknowns",
            "conflicts",
        ):
            active = [item.key for item in getattr(self, name) if item.status == "active"]
            if len(active) != len(set(active)):
                raise ValueError(f"{name} contains duplicate active semantic keys")
        return self


SemanticOperationType = Literal[
    "add_item",
    "replace_item",
    "remove_item",
    "confirm_assumption",
    "reject_assumption",
    "replace_goal_summary",
    "replace_success_signal",
]


class SemanticOperation(CognitiveContract):
    operation: SemanticOperationType
    section: Literal[
        "facts",
        "constraints",
        "preferences",
        "success_signals",
        "assumptions",
        "unknowns",
        "conflicts",
    ] | None = None
    key: str | None = None
    item: SemanticItem | None = None
    value: str | None = None


class UnderstandingPatch(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("understanding-patch"))
    base_artifact_id: str
    base_version: int = Field(ge=1)
    operations: list[SemanticOperation] = Field(min_length=1)
    user_message_ref: str
    created_at: str = Field(default_factory=utc_now)


class UnderstandingContext(CognitiveContract):
    current_snapshot: UnderstandingSnapshot
    latest_user_message: str
    recent_messages: list[str] = Field(default_factory=list, max_length=4)
    unresolved_unknowns: list[SemanticItem] = Field(default_factory=list)
    memory_top_k: list[SemanticItem] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)


class CoreConstraints(CognitiveContract):
    planning_horizon: str | None = None
    deadline: str | None = None
    weekday_capacity_minutes: int | None = Field(default=None, ge=0)
    weekend_capacity_minutes: int | None = Field(default=None, ge=0)
    excluded_dates: list[str] = Field(default_factory=list)
    excluded_weekdays: list[int] = Field(default_factory=list)
    budget_limit: float | None = Field(default=None, ge=0)
    maximum_session_minutes: int | None = Field(default=None, ge=1)
    minimum_buffer_ratio: float = Field(default=0.1, ge=0, le=0.8)
    required_start_date: str | None = None
    required_deliverables: list[str] = Field(default_factory=list)


class SemanticConstraint(CognitiveContract):
    stable_id: str
    statement: str
    source_ref: str
    constraint_type: str
    mutation_policy: MutationPolicy
    priority: Literal["blocking", "important", "optional"] = "important"


class ConstraintSet(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("constraints"))
    version: int = Field(default=1, ge=1)
    understanding_ref: str
    understanding_version: int = Field(ge=1)
    core: CoreConstraints = Field(default_factory=CoreConstraints)
    semantic: list[SemanticConstraint] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


VerificationStatus = Literal["verified", "unverified", "inference", "expired"]


class ContextClaim(CognitiveContract):
    id: str
    claim: str
    source_type: SourceType
    source_ref: str
    retrieved_at: str = Field(default_factory=utc_now)
    effective_at: str | None = None
    expires_at: str | None = None
    verification_status: VerificationStatus
    credibility: float = Field(default=0.5, ge=0, le=1)


class ContextPack(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("context"))
    version: int = Field(default=1, ge=1)
    understanding_ref: str
    constraint_ref: str
    claims: list[ContextClaim] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    tool_run_refs: list[str] = Field(default_factory=list)
    calendar_snapshot_ref: str | None = None
    created_at: str = Field(default_factory=utc_now)


class EffortEstimate(CognitiveContract):
    min_minutes: int = Field(ge=1)
    expected_minutes: int = Field(ge=1)
    max_minutes: int = Field(ge=1)
    confidence: float = Field(default=0.6, ge=0, le=1)
    estimation_basis: str

    @model_validator(mode="after")
    def ordered_range(self) -> "EffortEstimate":
        if not self.min_minutes <= self.expected_minutes <= self.max_minutes:
            raise ValueError("effort estimate must satisfy min <= expected <= max")
        return self


class PlanTask(CognitiveContract):
    id: str
    milestone_id: str
    title: str
    purpose: str
    why_now: str
    action_steps: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    effort_estimate: EffortEstimate
    priority: Literal["low", "medium", "high"] = "medium"
    optionality: Literal["required", "important", "optional"] = "required"
    deliverable: str
    completion_evidence: list[str] = Field(min_length=1)
    resource_refs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    fallback: str
    source_goal_refs: list[str] = Field(default_factory=list)
    source_constraint_refs: list[str] = Field(default_factory=list)


class PlanMilestone(CognitiveContract):
    id: str
    title: str
    purpose: str
    success_signal_refs: list[str] = Field(default_factory=list)


class PlanBlueprint(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("plan"))
    version: int = Field(default=1, ge=1)
    goal_summary: str
    understanding_ref: str
    constraint_ref: str
    context_ref: str
    milestones: list[PlanMilestone] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


QualityCategory = Literal["content", "schedule", "evidence", "constraint"]
QualitySeverity = Literal["blocker", "major", "minor"]


class QualityIssue(CognitiveContract):
    issue_id: str
    category: QualityCategory
    severity: QualitySeverity
    rule_id: str
    target_type: str
    target_id: str
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    repair_basis: str


class QualityReport(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("quality"))
    version: int = Field(default=1, ge=1)
    target_artifact_id: str
    target_version: int = Field(ge=1)
    hard_rules_passed: bool
    semantic_review_required: bool = False
    semantic_review_completed: bool = False
    issues: list[QualityIssue] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=100)
    remaining_risks: list[str] = Field(default_factory=list)
    repair_round: int = Field(default=0, ge=0, le=2)

    @property
    def passed(self) -> bool:
        return bool(
            self.hard_rules_passed
            and not any(issue.severity in {"blocker", "major"} for issue in self.issues)
        )


RepairOperationType = Literal[
    "add_task",
    "update_task",
    "remove_optional_task",
    "split_task",
    "move_task",
    "add_dependency",
    "remove_dependency",
    "replace_resource",
    "update_effort",
    "add_success_coverage",
    "update_schedule_session",
    "move_schedule_session",
    "split_schedule_session",
]


class RepairOperation(CognitiveContract):
    operation: RepairOperationType
    target_id: str
    payload: dict = Field(default_factory=dict)


class RepairProposal(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("repair"))
    artifact_id: str
    artifact_version: int = Field(ge=1)
    issue_id: str
    operations: list[RepairOperation] = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


class RepairResult(CognitiveContract):
    accepted: bool
    reason: str
    new_artifact_id: str | None = None
    new_version: int | None = None
    invalidated_artifacts: list[str] = Field(default_factory=list)


class ScheduleSession(CognitiveContract):
    id: str
    task_id: str
    start: str
    end: str
    duration_minutes: int = Field(ge=1)
    sequence: int = Field(ge=0)
    status: Literal["planned", "moved", "split", "unscheduled"] = "planned"
    reason: str


class CapacitySummary(CognitiveContract):
    available_minutes: int = Field(ge=0)
    scheduled_minutes: int = Field(ge=0)
    buffer_minutes: int = Field(ge=0)


class ScheduleBlueprint(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("schedule"))
    version: int = Field(default=1, ge=1)
    plan_ref: str
    plan_version: int = Field(ge=1)
    calendar_snapshot_ref: str | None = None
    planning_timezone: str = "Asia/Shanghai"
    periods: list[str] = Field(default_factory=list)
    sessions: list[ScheduleSession] = Field(default_factory=list)
    capacity_summary: CapacitySummary
    buffer_summary: str
    unscheduled_task_ids: list[str] = Field(default_factory=list)
    scheduling_assumptions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class CalendarEventProposal(CognitiveContract):
    source_plan_id: str
    source_plan_version: int
    source_schedule_id: str
    source_schedule_version: int
    source_task_id: str
    source_session_id: str
    source_key: str
    title: str
    start: str
    end: str
    description: str
    completion_evidence: list[str] = Field(default_factory=list)


class CalendarProposal(CognitiveContract):
    artifact_id: str = Field(default_factory=lambda: new_artifact_id("calendar"))
    version: int = Field(default=1, ge=1)
    plan_ref: str
    schedule_ref: str
    calendar_snapshot_ref: str | None = None
    timezone: str = "Asia/Shanghai"
    events: list[CalendarEventProposal] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


FeedbackCategory = Literal[
    "understanding_change",
    "plan_change",
    "schedule_change",
    "resource_change",
    "presentation_change",
    "approve",
    "reject",
]


class FeedbackRoute(CognitiveContract):
    category: FeedbackCategory
    confidence: float = Field(ge=0, le=1)
    target_ids: list[str] = Field(default_factory=list)
    normalized_instruction: str
    reason: str


class FinalApprovalBundle(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("final-approval"))
    session_id: str
    understanding_version: int
    constraint_version: int
    context_version: int
    plan_version: int
    quality_report_version: int
    schedule_version: int
    calendar_proposal_version: int
    calendar_snapshot_version: int
    checkpoint_version: int
    approved_at: str = Field(default_factory=utc_now)
    consumed: bool = False


TaskExecutionStatus = Literal[
    "not_started",
    "in_progress",
    "blocked",
    "completed",
    "skipped",
    "rescheduled",
    "failed",
]


class ExecutionOutcome(CognitiveContract):
    task_id: str
    status: TaskExecutionStatus
    estimated_minutes: int = Field(ge=0)
    actual_minutes: int | None = Field(default=None, ge=0)
    started_at: str | None = None
    completed_at: str | None = None
    completion_evidence: list[str] = Field(default_factory=list)
    blocker_reason: str | None = None
    failure_reason: str | None = None


class ReplanProposal(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("replan"))
    session_id: str
    affected_task_ids: list[str]
    reason: str
    proposed_operations: list[RepairOperation] = Field(default_factory=list)
    requires_final_review: bool = True


class LearningObservation(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("observation"))
    session_id: str
    category: str
    statement: str
    source_refs: list[str]
    observed_at: str = Field(default_factory=utc_now)


class MemoryCandidateDraft(CognitiveContract):
    statement: str
    category: str
    source_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    proposed_scope: list[str] = Field(default_factory=list)
    evidence_count: int = Field(default=1, ge=1)


class UserAdaptation(CognitiveContract):
    version: int = Field(default=1, ge=1)
    duration_multiplier: float = Field(default=1, ge=0.5, le=3)
    preferred_session_minutes: int = Field(default=60, ge=15, le=240)
    preferred_time_windows: list[str] = Field(default_factory=list)
    buffer_ratio: float = Field(default=0.1, ge=0, le=0.8)
    repeated_blockers: list[str] = Field(default_factory=list)
    observation_refs: list[str] = Field(default_factory=list)


class PromotionAudit(CognitiveContract):
    id: str = Field(default_factory=lambda: new_artifact_id("promotion"))
    runtime_version: str
    change_type: str
    previous_value: str
    proposed_value: str
    observation_refs: list[str]
    allowed_for_automatic_promotion: bool
    requires_human_release: bool
    reason: str
    rollback_value: str
    created_at: str = Field(default_factory=utc_now)
