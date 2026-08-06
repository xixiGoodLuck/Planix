from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from ..services.cognitive_planning.contracts import SafePlanningError


RESUME_NODE_BY_STAGE: dict[str, str] = {
    "goal_modeling": "goal_intelligence",
    "goal_intelligence": "goal_intelligence",
    "goal_completion": "goal_completion",
    "reality_assessment": "reality",
    "context_evidence": "evidence",
    "evidence_synthesis": "evidence",
    "strategy_architecture": "strategy",
    "strategy_design": "strategy",
    "execution_narrative": "execution",
    "execution_design": "execution",
    "independent_critique": "critic",
    "critic_review": "critic",
    "plan_critique": "critic",
    "feedback_learning": "feedback_learning",
}


class RecoveryAction(StrEnum):
    JSON_REPAIR = "json_repair"
    RETRY_STAGE = "retry_stage"
    SWITCH_MODEL = "switch_model"
    RESUME_CHECKPOINT = "resume_checkpoint"
    GRACEFUL_DEGRADATION = "graceful_degradation"


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    resume_node: str
    business_status: str
    runtime_status: str = "blocked_model"
    planning_mode: str = "blocked_model_unavailable"
    compatibility_status: str = "MODEL_UNAVAILABLE"
    allow_read_only: bool = True


@dataclass(frozen=True)
class JsonSyntaxRepair:
    value: dict[str, Any] | None
    repaired: bool
    operations: tuple[str, ...] = ()


def _strip_code_fence(text: str) -> tuple[str, bool]:
    stripped = text.strip().lstrip("\ufeff")
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.I | re.S)
    return (match.group(1).strip(), True) if match else (stripped, False)


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            if depth < 0:
                return None
    return None


def repair_json_object(raw: str) -> JsonSyntaxRepair:
    """Repair JSON *syntax* only and return only an existing object.

    The helper may remove a Markdown fence/prefix/suffix or trailing commas.
    It never invents keys, closes missing structures, changes values, or fills
    contract fields. Pydantic schema validation remains the fail-closed gate.
    """

    text, fenced = _strip_code_fence(raw or "")
    operations: list[str] = ["remove_code_fence"] if fenced else []
    candidates: list[tuple[str, tuple[str, ...]]] = [(text, tuple(operations))]
    extracted = _first_balanced_object(text)
    if extracted is not None and extracted != text:
        candidates.append((extracted, (*operations, "extract_balanced_object")))

    for candidate, candidate_operations in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return JsonSyntaxRepair(
                value=parsed,
                repaired=bool(candidate_operations),
                operations=candidate_operations,
            )

        without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", candidate)
        if without_trailing_commas == candidate:
            continue
        try:
            parsed = json.loads(without_trailing_commas)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return JsonSyntaxRepair(
                value=parsed,
                repaired=True,
                operations=(*candidate_operations, "remove_trailing_commas"),
            )
    return JsonSyntaxRepair(value=None, repaired=False)


def recover_json_object(raw: str) -> dict[str, Any] | None:
    """Compatibility-shaped callable used by contract completion paths."""

    return repair_json_object(raw).value


class RecoveryManager:
    """Classify failures and preserve the exact failed lifecycle stage."""

    def business_status_for_stage(self, stage: str, state: Mapping[str, Any]) -> str:
        if stage in {"goal_intelligence", "goal_modeling", "goal_completion"}:
            completion = state.get("goal_completion")
            return "goal_understood" if completion and getattr(completion, "complete", False) else "goal_clarification"
        if stage in {"reality_assessment", "context_evidence", "evidence_synthesis"}:
            return "evidence_pending"
        if stage in {"strategy_architecture", "strategy_design"}:
            return "strategy_pending"
        return "execution_pending"

    def decide_model_failure(
        self,
        state: Mapping[str, Any],
        error: SafePlanningError,
    ) -> RecoveryDecision:
        error_type = str(error.error_type or "")
        if error_type == "invalid_model_output":
            action = RecoveryAction.JSON_REPAIR
        elif error_type in {"auth_error", "invalid_key_format", "bad_model", "bad_base_url"}:
            # ModelRouter has already recorded its provider attempts. The
            # failed agent remains pending until routing/settings change.
            action = RecoveryAction.SWITCH_MODEL
        elif error.retryable:
            action = RecoveryAction.RETRY_STAGE
        else:
            action = RecoveryAction.GRACEFUL_DEGRADATION
        degraded = action == RecoveryAction.GRACEFUL_DEGRADATION
        return RecoveryDecision(
            action=action,
            resume_node=RESUME_NODE_BY_STAGE.get(str(error.stage or ""), "goal_intelligence"),
            business_status=self.business_status_for_stage(str(error.stage or ""), state),
            runtime_status="retry_required" if degraded else "blocked_model",
            planning_mode="degraded_read_only" if degraded else "blocked_model_unavailable",
        )

    def resume_checkpoint(self, state: Mapping[str, Any]) -> RecoveryDecision:
        resume_node = str(state.get("resume_node") or "goal_intelligence")
        return RecoveryDecision(
            action=RecoveryAction.RESUME_CHECKPOINT,
            resume_node=resume_node,
            business_status=str(state.get("business_status") or "goal_clarification"),
            runtime_status="running",
            planning_mode="model_backed",
            compatibility_status=str(state.get("status") or "needs_goal_clarification"),
            allow_read_only=False,
        )


__all__ = [
    "RESUME_NODE_BY_STAGE",
    "JsonSyntaxRepair",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryManager",
    "recover_json_object",
    "repair_json_object",
]
