from __future__ import annotations

from typing import Literal

from pydantic import Field

from .contracts import (
    ApprovalRecord,
    ArtifactKind,
    ArtifactRef,
    HarnessDecision,
    HarnessModel,
    HarnessWaitState,
    PolicyDecision,
    RecoveryAction,
)


HarnessLifecycle = Literal["active", "waiting", "blocked", "completed", "cancelled"]
HarnessWaitingState = HarnessWaitState


class HarnessError(HarnessModel):
    stage: str
    error_type: str = Field(alias="errorType")
    message: str
    retryable: bool = True
    attempts: list[dict[str, str | int | bool | None]] = Field(default_factory=list)


class HarnessCheckpoint(HarnessModel):
    """A resumable pointer set. Artifact bodies stay in the artifact store."""

    artifact_refs: dict[ArtifactKind, ArtifactRef] = Field(default_factory=dict, alias="artifactRefs")
    artifact_versions: dict[ArtifactKind, int] = Field(default_factory=dict, alias="artifactVersions")


class PersistentCognitiveState(HarnessModel):
    session_id: str = Field(alias="sessionId")
    lifecycle: HarnessLifecycle = "active"
    current_stage: str = Field(default="session_guard", alias="currentStage")
    completed_agents: list[str] = Field(default_factory=list, alias="completedAgents")
    pending_agent: str | None = Field(default=None, alias="pendingAgent")
    artifact_versions: dict[ArtifactKind, int] = Field(default_factory=dict, alias="artifactVersions")
    waiting_state: HarnessWaitingState = Field(default="none", alias="waitingState")
    errors: list[HarnessError] = Field(default_factory=list)
    recovery_actions: list[RecoveryAction] = Field(default_factory=list, alias="recoveryActions")
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    repair_target: str | None = Field(default=None, alias="repairTarget")
    checkpoint_version: int = Field(default=1, ge=1, alias="checkpointVersion")
    checkpoint: HarnessCheckpoint = Field(default_factory=HarnessCheckpoint)
    last_decision: HarnessDecision | None = Field(default=None, alias="lastDecision")
    last_policy_decision: PolicyDecision | None = Field(default=None, alias="lastPolicyDecision")
    created_at: str = Field(default="", alias="createdAt")
    updated_at: str = Field(default="", alias="updatedAt")


__all__ = [
    "HarnessError",
    "HarnessCheckpoint",
    "HarnessLifecycle",
    "HarnessWaitingState",
    "PersistentCognitiveState",
]
