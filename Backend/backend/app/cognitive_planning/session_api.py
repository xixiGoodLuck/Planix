from __future__ import annotations

from typing import Any

from .contracts import UnderstandingSnapshot
from ..schemas import (
    CognitivePlanningMetadata,
    PendingPlanningInput,
    PendingPlanningQuestion,
    PlanningLocalizedText,
    PlanningModelFailure,
    PlanningModelFailureAttempt,
    PlanningSessionResponse,
)
from .artifact_audit import PlanningArtifactAuditStore
from .persistence import json_list, json_object


_V2_ARTIFACTS = {
    "understanding_snapshot",
    "constraint_set",
    "context_pack",
    "plan_blueprint",
    "plan_quality_report",
    "schedule_blueprint",
    "schedule_quality_report",
    "calendar_proposal",
    "final_approval_bundle",
    "execution_outcome",
    "replan_proposal",
    "learning_observation",
    "memory_evaluation",
}
_SAFE_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "zhipu_glm": "GLM",
    "kimi": "Kimi",
    "openai": "OpenAI",
    "custom": "Custom",
    "local": "Local",
    "mock": "Mock",
}
_SAFE_ERRORS = {
    "auth_error",
    "bad_base_url",
    "bad_model",
    "bad_request",
    "insufficient_balance",
    "invalid_key_format",
    "invalid_model_output",
    "missing_api_key",
    "model_output_truncated",
    "network_error",
    "rate_limit",
    "timeout",
    "unknown",
}


def _safe_attempts(raw_attempts: object) -> tuple[list[PlanningModelFailureAttempt], bool]:
    attempts: list[PlanningModelFailureAttempt] = []
    retried = False
    if not isinstance(raw_attempts, list):
        return attempts, retried
    for raw in raw_attempts:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        if provider not in _SAFE_PROVIDER_LABELS:
            continue
        status = str(raw.get("status") or "error").lower()
        if status not in {"success", "error", "skipped"}:
            status = "error"
        error_type = str(raw.get("errorType") or raw.get("error_type") or "").lower()
        if status != "success" and error_type not in _SAFE_ERRORS:
            error_type = "unknown"
        if status == "success":
            error_type = ""
        retried = retried or bool(raw.get("automaticRetry") or raw.get("retryReason"))
        attempts.append(PlanningModelFailureAttempt(provider=provider, status=status, errorType=error_type or None))
    return attempts, retried


def _model_failure(status: str, metadata: CognitivePlanningMetadata | None, messages: list[Any]) -> PlanningModelFailure | None:
    if status != "MODEL_UNAVAILABLE":
        return None
    block = next((item for item in reversed(messages) if item.message_type == "block" and not item.resolved), None)
    if block is None:
        return None
    payload = block.payload_json if isinstance(block.payload_json, dict) else {}
    attempts, retried = _safe_attempts(payload.get("attempts"))
    details = "; ".join(
        f"{_SAFE_PROVIDER_LABELS[item.provider]}: {item.error_type or item.status}"
        for item in attempts if item.status != "success"
    ) or str(payload.get("errorType") or "model_unavailable")
    stage = str((metadata.current_stage if metadata else "") or payload.get("resumeNode") or "understanding")
    return PlanningModelFailure(
        stage=stage,
        resumeNode=str(payload.get("resumeNode") or stage),
        retryable=True,
        automaticRetryAttempted=retried,
        attempts=attempts,
        summary=PlanningLocalizedText(
            zh=f"当前规划阶段未完成；已确认事实和有效产物已保留。{details}",
            en=f"The current planning stage did not complete; confirmed facts and valid artifacts were preserved. {details}",
        ),
        action=PlanningLocalizedText(
            zh="请检查模型设置后重试当前阶段。",
            en="Check model settings and retry the current stage.",
        ),
    )


def _pending_input(row, failure: PlanningModelFailure | None) -> PendingPlanningInput | None:
    if not failure or failure.resume_node != "understanding":
        return None
    for turn in reversed(json_list(row["conversation_history_json"])):
        if isinstance(turn, dict) and turn.get("role") == "user" and str(turn.get("content") or "").strip():
            return PendingPlanningInput(text=str(turn["content"]), applied=False)
    return None


class SessionApiAdapter:
    """Project lifecycle rows and native V2 artifacts into the public session response."""

    def __init__(self, agent_runtime: PlanningArtifactAuditStore | None = None):
        self.agent_runtime = agent_runtime or PlanningArtifactAuditStore()

    def from_row(self, row) -> PlanningSessionResponse:
        metadata_raw = json_object(row["cognitive_metadata_json"])
        metadata = CognitivePlanningMetadata.model_validate(metadata_raw) if metadata_raw else None
        all_artifacts = self.agent_runtime.list_artifacts(row["id"])
        artifacts = [item for item in all_artifacts if item.artifact_type in _V2_ARTIFACTS]
        latest = {
            kind: max((item for item in artifacts if item.artifact_type == kind), key=lambda item: item.version, default=None)
            for kind in _V2_ARTIFACTS
        }
        payload = {kind: item.content_json if item else None for kind, item in latest.items()}
        understanding = UnderstandingSnapshot.model_validate(payload["understanding_snapshot"]) if payload["understanding_snapshot"] else None
        pending_question = None
        if understanding and understanding.next_question and not understanding.readiness.ready_for_confirmation:
            question = understanding.next_question
            pending_question = PendingPlanningQuestion(
                askedFields=[item.key for item in understanding.unknowns[:3]] or ["goal"],
                expectedAnswerType="goal_clarification",
                questionText=question.question,
                questions=[question.question],
            )
        if row["status"] in {"needs_goal_clarification", "waiting_understanding_confirmation"}:
            phase, step = "UNDERSTANDING", "WAITING_CONFIRMATION"
        elif row["status"] in {"waiting_final_review", "final_revision"}:
            phase, step = "FINAL_REVIEW", "WAITING_CONFIRMATION"
        elif row["status"] == "waiting_calendar_write_approval":
            phase, step = "WRITING", "WAITING_PERMISSION"
        elif row["status"] == "written_to_calendar":
            phase, step = "ACTIVE", "CALENDAR_WRITTEN"
        elif row["status"] == "MODEL_UNAVAILABLE":
            phase, step = "BLOCKED", "MODEL_RECOVERY"
        elif payload["schedule_blueprint"]:
            phase, step = "SCHEDULING", "VALIDATING"
        else:
            phase, step = "PLANNING", "GENERATING"
        messages = self.agent_runtime.list_messages(row["id"])
        failure = _model_failure(row["status"], metadata, messages)
        return PlanningSessionResponse(
            sessionId=row["id"],
            threadId=row["thread_id"],
            entryPoint=row["entry_point"],
            status=row["status"],
            businessStatus=row["business_status"],
            runtimeStatus=row["runtime_status"],
            userInput=row["user_input"],
            pendingQuestion=pending_question,
            cognitiveMetadata=metadata,
            planningPhase=phase,
            planningStep=step,
            understandingSnapshot=payload["understanding_snapshot"],
            constraintSet=payload["constraint_set"],
            contextPack=payload["context_pack"],
            planBlueprint=payload["plan_blueprint"],
            planQualityReport=payload["plan_quality_report"],
            scheduleBlueprint=payload["schedule_blueprint"],
            scheduleQualityReport=payload["schedule_quality_report"],
            calendarProposal=payload["calendar_proposal"],
            finalApprovalBundle=payload["final_approval_bundle"],
            modelFailure=failure,
            pendingInput=_pending_input(row, failure),
            artifacts=artifacts,
            decisions=self.agent_runtime.list_decisions(row["id"]),
            messages=messages,
            version=int(row["version"] or 1),
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )


__all__ = ["SessionApiAdapter"]
