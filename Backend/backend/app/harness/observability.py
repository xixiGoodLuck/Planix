from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from .contracts import HarnessDecision, HarnessEventType, PolicyDecision, RecoveryAction
from .persistence import HarnessCheckpointResult, HarnessStateRepository
from .state import PersistentCognitiveState


InvocationStatus = Literal["scheduled", "running", "succeeded", "failed", "cancelled"]
ModelRouteStatus = Literal["selected", "success", "error", "skipped", "exhausted"]

_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "headers",
    "password",
    "secret",
    "clientsecret",
    "accesskey",
}


def _safe_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized = "".join(char for char in text_key.casefold() if char.isalnum())
            result[text_key] = "[REDACTED]" if normalized in _SENSITIVE_KEYS else _safe_payload(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str) and value.casefold().startswith("bearer "):
        return "[REDACTED]"
    return value


class HarnessObservability:
    """Typed, secret-safe writer for the durable harness event stream."""

    def __init__(self, repository: HarnessStateRepository | None = None):
        self.repository = repository or HarnessStateRepository()

    def record(
        self,
        state: PersistentCognitiveState,
        *,
        event_type: HarnessEventType,
        agent_id: str | None = None,
        decision: str = "",
        payload: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        return self.repository.checkpoint(
            state,
            event_type=event_type,
            agent_id=agent_id,
            decision=decision,
            payload=_safe_payload(payload or {}),
            expected_version=expected_version,
        )

    def harness_decision(
        self,
        state: PersistentCognitiveState,
        decision: HarnessDecision,
        *,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        updates: dict[str, Any] = {"last_decision": decision}
        if decision.policy_decision is not None:
            updates["last_policy_decision"] = decision.policy_decision
        updated = state.model_copy(update=updates)
        return self.record(
            updated,
            event_type="harness_decision",
            agent_id=decision.next_agent,
            decision=decision.directive,
            payload=decision.model_dump(by_alias=True, exclude_none=True),
            expected_version=expected_version,
        )

    def agent_invocation(
        self,
        state: PersistentCognitiveState,
        *,
        agent_id: str,
        status: InvocationStatus,
        invocation_id: str,
        stage: str,
        attempt: int = 1,
        input_artifacts: dict[str, int] | None = None,
        output_artifact: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        updates: dict[str, Any] = {}
        if status in {"scheduled", "running", "failed"}:
            updates["pending_agent"] = agent_id
        elif status == "succeeded":
            updates["pending_agent"] = None
            updates["completed_agents"] = list(dict.fromkeys([*state.completed_agents, agent_id]))
        updated = state.model_copy(update=updates) if updates else state
        return self.record(
            updated,
            event_type="agent_invocation",
            agent_id=agent_id,
            decision=status,
            payload={
                "invocationId": invocation_id,
                "stage": stage,
                "status": status,
                "attempt": max(1, int(attempt)),
                "inputArtifacts": input_artifacts or {},
                "outputArtifact": output_artifact or {},
                "error": error or {},
            },
            expected_version=expected_version,
        )

    def artifact_changed(
        self,
        state: PersistentCognitiveState,
        *,
        agent_id: str,
        artifact_type: str,
        artifact_id: str,
        version: int,
        previous_version: int | None = None,
        status: str = "draft",
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        versions = dict(state.artifact_versions)
        versions[artifact_type] = int(version)
        updated = state.model_copy(update={"artifact_versions": versions})
        return self.record(
            updated,
            event_type="artifact_changed",
            agent_id=agent_id,
            decision="committed",
            payload={
                "artifactType": artifact_type,
                "artifactId": artifact_id,
                "previousVersion": previous_version,
                "version": int(version),
                "status": status,
            },
            expected_version=expected_version,
        )

    def recovery_action(
        self,
        state: PersistentCognitiveState,
        action: RecoveryAction,
        *,
        agent_id: str | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        updated = state.model_copy(
            update={"recovery_actions": [*state.recovery_actions, action]}
        )
        return self.record(
            updated,
            event_type="recovery_action",
            agent_id=agent_id,
            decision=action.action,
            payload=action.model_dump(by_alias=True, exclude_none=True),
            expected_version=expected_version,
        )

    def model_routing(
        self,
        state: PersistentCognitiveState,
        *,
        agent_id: str,
        invocation_id: str,
        provider: str,
        model: str,
        status: ModelRouteStatus,
        attempt: int,
        error_type: str | None = None,
        latency_ms: int | None = None,
        fallback_used: bool = False,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        return self.record(
            state,
            event_type="model_routing",
            agent_id=agent_id,
            decision=status,
            payload={
                "invocationId": invocation_id,
                "provider": provider,
                "model": model,
                "status": status,
                "attempt": max(1, int(attempt)),
                "errorType": error_type,
                "latencyMs": latency_ms,
                "fallbackUsed": bool(fallback_used),
            },
            expected_version=expected_version,
        )

    def policy_decision(
        self,
        state: PersistentCognitiveState,
        *,
        policy: str,
        allowed: bool,
        reason: str,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        return self.record(
            state,
            event_type="policy_decision",
            agent_id=agent_id,
            decision="allow" if allowed else "deny",
            payload={
                "policy": policy,
                "allowed": bool(allowed),
                "reason": reason,
                "context": context or {},
            },
            expected_version=expected_version,
        )

    def typed_policy_decision(
        self,
        state: PersistentCognitiveState,
        decision: PolicyDecision,
        *,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> HarnessCheckpointResult:
        updated = state.model_copy(update={"last_policy_decision": decision})
        payload = decision.model_dump(by_alias=True, exclude_none=True)
        if context:
            payload["context"] = context
        return self.record(
            updated,
            event_type="policy_decision",
            agent_id=agent_id or decision.next_agent,
            decision=decision.action,
            payload=payload,
            expected_version=expected_version,
        )


__all__ = [
    "HarnessObservability",
    "InvocationStatus",
    "ModelRouteStatus",
]
