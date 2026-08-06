from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ArtifactKind = Literal[
    "user_goal_model",
    "goal_completion",
    "reality_assessment",
    "evidence_pack",
    "strategy_portfolio",
    "execution_blueprint",
    "critique_report",
    "planning_learning_update",
    "memory_evaluation",
]

ArtifactStatus = Literal[
    "draft",
    "approved",
    "blocked",
    "needs_revision",
    "superseded",
    "rejected",
]

AgentPermission = Literal[
    "read_artifact",
    "write_artifact",
    "request_user_input",
    "propose_strategy",
    "propose_execution",
    "evaluate_execution",
    "propose_memory",
    "evaluate_memory",
    "request_calendar_write",
]

HarnessDirective = Literal[
    "invoke_agent",
    "wait_user",
    "wait_approval",
    "repair_artifact",
    "block_runtime",
    "finish",
]

HarnessEventType = Literal[
    "harness_decision",
    "agent_invocation",
    "artifact_changed",
    "recovery_action",
    "model_routing",
    "policy_decision",
]

RecoveryActionType = Literal[
    "model_switch",
    "retry",
    "json_repair",
    "checkpoint_resume",
    "graceful_degradation",
]

HarnessWaitState = Literal[
    "none",
    "user_input",
    "model_recovery",
    "strategy_approval",
    "execution_approval",
    "calendar_approval",
]

ApprovalGate = Literal["strategy", "execution", "calendar"]
ApprovalStatus = Literal["pending", "approved", "rejected", "invalidated", "consumed"]
PolicySubject = Literal[
    "planning_progress",
    "user_question",
    "calendar_write",
    "memory_persistence",
    "critic_review",
]
PolicyAction = Literal[
    "allow",
    "deny",
    "invoke_agent",
    "wait_user",
    "wait_approval",
    "repair_artifact",
    "block_runtime",
    "finish",
]
PolicyGate = Literal[
    "runtime",
    "goal_completion",
    "strategy_approval",
    "execution_approval",
    "critic",
    "calendar_approval",
    "memory_evaluation",
]
MemoryCategory = Literal[
    "fact",
    "habit",
    "preference",
    "constraint",
    "failure_pattern",
    "planning_hypothesis",
]


class HarnessModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class FailureCondition(HarnessModel):
    code: str
    description: str
    recoverable: bool = True


class AgentContract(HarnessModel):
    agent_id: str = Field(alias="agentId")
    name: str
    responsibility: str
    input_artifacts: tuple[ArtifactKind, ...] = Field(default_factory=tuple, alias="inputArtifacts")
    optional_input_artifacts: tuple[ArtifactKind, ...] = Field(
        default_factory=tuple,
        alias="optionalInputArtifacts",
    )
    output_artifact: ArtifactKind | None = Field(default=None, alias="outputArtifact")
    permissions: tuple[AgentPermission, ...] = Field(default_factory=tuple)
    failure_conditions: tuple[FailureCondition, ...] = Field(default_factory=tuple, alias="failureConditions")
    max_retries: int = Field(default=1, ge=0, le=3, alias="maxRetries")


class ArtifactRef(HarnessModel):
    id: str
    session_id: str = Field(alias="sessionId")
    kind: ArtifactKind
    version: int = Field(ge=1)
    owner: str
    status: ArtifactStatus = "draft"

    def same_version(self, other: "ArtifactRef | None") -> bool:
        return bool(
            other
            and self.id == other.id
            and self.session_id == other.session_id
            and self.kind == other.kind
            and self.version == other.version
        )


class ApprovalRecord(HarnessModel):
    id: str
    session_id: str = Field(alias="sessionId")
    gate: ApprovalGate
    artifact: ArtifactRef
    status: ApprovalStatus = "pending"
    created_at: str = Field(default="", alias="createdAt")
    decided_at: str = Field(default="", alias="decidedAt")
    invalidation_reason: str = Field(default="", alias="invalidationReason")

    def approves(self, *, session_id: str, gate: ApprovalGate, artifact: ArtifactRef) -> bool:
        return bool(
            self.status == "approved"
            and self.session_id == session_id
            and self.gate == gate
            and self.artifact.same_version(artifact)
        )


class PolicyDecision(HarnessModel):
    subject: PolicySubject
    action: PolicyAction
    allowed: bool
    reason: str
    session_id: str = Field(default="", alias="sessionId")
    next_agent: str | None = Field(default=None, alias="nextAgent")
    required_approval: ApprovalGate | None = Field(default=None, alias="requiredApproval")
    required_gates: tuple[PolicyGate, ...] = Field(default_factory=tuple, alias="requiredGates")
    failed_gates: tuple[PolicyGate, ...] = Field(default_factory=tuple, alias="failedGates")
    repair_target: ArtifactKind | None = Field(default=None, alias="repairTarget")


class HarnessDecision(HarnessModel):
    directive: HarnessDirective
    next_agent: str | None = Field(default=None, alias="nextAgent")
    graph_node: str = Field(default="__end__", alias="graphNode")
    reason: str
    wait_state: HarnessWaitState = Field(default="none", alias="waitState")
    repair_target: str | None = Field(default=None, alias="repairTarget")
    policy_decision: PolicyDecision | None = Field(default=None, alias="policyDecision")


class RecoveryAction(HarnessModel):
    action: RecoveryActionType
    stage: str
    reason: str
    retryable: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class HarnessEvent(HarnessModel):
    id: str
    session_id: str = Field(alias="sessionId")
    sequence: int
    event_type: HarnessEventType = Field(alias="eventType")
    agent_id: str | None = Field(default=None, alias="agentId")
    decision: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(alias="createdAt")


class MemoryCandidate(HarnessModel):
    id: str
    session_id: str = Field(alias="sessionId")
    source_artifact: ArtifactRef = Field(alias="sourceArtifact")
    category: MemoryCategory = "planning_hypothesis"
    statement: str
    evidence: str
    domain_scope: list[str] = Field(default_factory=list, alias="domainScope")
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_polarity: Literal["positive", "negative"] = Field(default="positive", alias="evidencePolarity")
    expires_at: str | None = Field(default=None, alias="expiresAt")


class MemoryEvaluation(HarnessModel):
    id: str = ""
    session_id: str = Field(default="", alias="sessionId")
    candidate_id: str = Field(default="", alias="candidateId")
    source_artifact: ArtifactRef | None = Field(default=None, alias="sourceArtifact")
    evaluator_agent_id: str = Field(default="memory_evaluator", alias="evaluatorAgentId")
    allowed: bool
    reason: str
    durable_rule: str | None = Field(default=None, alias="durableRule")
    evidence: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)


class CriticGateResult(HarnessModel):
    passed: bool
    reason: str
    critique_artifact: ArtifactRef = Field(alias="critiqueArtifact")
    execution_artifact: ArtifactRef = Field(alias="executionArtifact")
    evaluated_execution_artifact: ArtifactRef = Field(alias="evaluatedExecutionArtifact")
    repair_target: ArtifactKind | None = Field(default=None, alias="repairTarget")


class MemoryControllerResult(HarnessModel):
    persisted: bool = False
    evaluation: MemoryEvaluation | None = None
    policy_decision: PolicyDecision = Field(alias="policyDecision")
    memory_id: str | None = Field(default=None, alias="memoryId")
    error: str = ""


__all__ = [
    "AgentContract",
    "AgentPermission",
    "ApprovalGate",
    "ApprovalRecord",
    "ApprovalStatus",
    "ArtifactRef",
    "ArtifactKind",
    "ArtifactStatus",
    "CriticGateResult",
    "FailureCondition",
    "HarnessDecision",
    "HarnessDirective",
    "HarnessEvent",
    "HarnessEventType",
    "HarnessWaitState",
    "MemoryCandidate",
    "MemoryCategory",
    "MemoryControllerResult",
    "MemoryEvaluation",
    "PolicyAction",
    "PolicyDecision",
    "PolicyGate",
    "PolicySubject",
    "RecoveryAction",
    "RecoveryActionType",
]
