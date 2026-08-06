from __future__ import annotations

from typing import Any

from ..contracts import (
    ExecutionBlueprint,
    ExecutionNarrative,
    RealityAssessment,
    SafePlanningError,
    UserGoalModel,
)
from ..evaluation.deterministic_guards import (
    DeterministicGuardError,
    execution_preflight_context,
    validate_execution_preflight,
)
from .base import AgentResult, CognitiveModelClient, PlanningModelUnavailable


FALLBACK_ERROR_TYPES = frozenset({"model_output_truncated", "invalid_model_output"})
_ATTEMPT_KEYS = {
    "provider",
    "model",
    "status",
    "errorType",
    "latencyMs",
    "automaticRetry",
    "retryReason",
}


def _safe_attempts(
    attempts: list[dict[str, Any]] | None,
    *,
    retry_reason: str | None = None,
) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for raw in attempts or []:
        if not isinstance(raw, dict):
            continue
        item = {key: raw[key] for key in _ATTEMPT_KEYS if key in raw and raw[key] is not None}
        if not item.get("provider") or item.get("status") not in {"success", "error", "skipped"}:
            continue
        if retry_reason:
            item["retryReason"] = retry_reason
        safe.append(item)
    return safe


def _usage_with_mode(
    result: AgentResult[ExecutionBlueprint],
    *,
    generation_mode: str,
) -> dict[str, Any]:
    usage = dict(result.model_usage)
    usage["attempts"] = _safe_attempts(usage.get("attempts"))
    usage["generationMode"] = generation_mode
    usage["localFallbackAllowed"] = False
    return usage


def _merge_usage(
    usages: list[dict[str, Any]],
    *,
    generation_mode: str,
    prefix_attempts: list[dict[str, Any]] | None = None,
    retry_reason_by_index: dict[int, str] | None = None,
) -> dict[str, Any]:
    last = next((item for item in reversed(usages) if item), {})
    merged = dict(last)
    for key in ("promptTokens", "completionTokens", "totalTokens", "latencyMs"):
        values = [item.get(key) for item in usages]
        if any(value is not None for value in values):
            merged[key] = sum(int(value or 0) for value in values)
    attempts = _safe_attempts(prefix_attempts)
    for index, usage in enumerate(usages):
        attempts.extend(
            _safe_attempts(
                usage.get("attempts"),
                retry_reason=(retry_reason_by_index or {}).get(index),
            )
        )
    merged["attempts"] = attempts
    merged["fallbackUsed"] = any(bool(item.get("fallbackUsed")) for item in usages)
    merged["localFallbackAllowed"] = False
    merged["generationMode"] = generation_mode
    return merged


def _failure_with_attempts(
    exc: PlanningModelUnavailable,
    *,
    prefix_attempts: list[dict[str, Any]],
    retry_reason: str | None = None,
) -> PlanningModelUnavailable:
    attempts = [
        *_safe_attempts(prefix_attempts),
        *_safe_attempts(exc.error.attempts, retry_reason=retry_reason),
    ]
    error = exc.error.model_copy(update={"attempts": attempts})
    return PlanningModelUnavailable(exc.stage, error)


def _preflight_failure(
    issues: list[str],
    *,
    attempts: list[dict[str, Any]],
) -> PlanningModelUnavailable:
    return PlanningModelUnavailable(
        "execution_design",
        SafePlanningError(
            stage="execution_design",
            errorType="invalid_model_output",
            message=(
                "The Execution blueprint failed deterministic preflight after one model repair: "
                + "; ".join(issues[:6])
                + "."
            ),
            retryable=True,
            attempts=_safe_attempts(attempts),
        ),
    )


def generate_execution_blueprint(
    *,
    model: CognitiveModelClient,
    goal: UserGoalModel,
    reality: RealityAssessment | None,
    common_payload: dict[str, Any],
    single_pass_system: str,
    narrative_system: str,
    blueprint_system: str,
    feature_prefix: str,
) -> AgentResult[ExecutionBlueprint]:
    """Generate one complete blueprint, falling back only for structured-output failure."""

    initial_failure_attempts: list[dict[str, Any]] = []
    repair_generation = bool(
        common_payload.get("previousExecutionBlueprint")
        and common_payload.get("repairInstructions")
    )
    generation_mode = "two_pass_repair" if repair_generation else "single_pass"

    if repair_generation:
        narrative_result = None
        try:
            narrative_result = model.complete_contract(
                stage="execution_narrative",
                task_type="planning_execution",
                feature=f"{feature_prefix}_narrative_repair",
                system=narrative_system,
                payload=common_payload,
                contract_type=ExecutionNarrative,
                temperature=0.2,
            )
            blueprint_result = model.complete_contract(
                stage="execution_design",
                task_type="planning_execution",
                feature=f"{feature_prefix}_blueprint_repair",
                system=blueprint_system,
                payload={
                    **common_payload,
                    "executionNarrative": narrative_result.artifact.model_dump(by_alias=True),
                },
                contract_type=ExecutionBlueprint,
                temperature=0.2,
            )
        except PlanningModelUnavailable as fallback_exc:
            prefix = _safe_attempts(
                narrative_result.model_usage.get("attempts")
                if narrative_result is not None
                else [],
                retry_reason="execution_critic_repair",
            )
            raise _failure_with_attempts(
                fallback_exc,
                prefix_attempts=prefix,
                retry_reason="execution_critic_repair",
            ) from fallback_exc
        usage = _merge_usage(
            [narrative_result.model_usage, blueprint_result.model_usage],
            generation_mode=generation_mode,
            retry_reason_by_index={
                0: "execution_critic_repair",
                1: "execution_critic_repair",
            },
        )
    else:
        try:
            blueprint_result = model.complete_contract(
                stage="execution_design",
                task_type="planning_execution",
                feature=f"{feature_prefix}_single_pass",
                system=single_pass_system,
                payload=common_payload,
                contract_type=ExecutionBlueprint,
                temperature=0.2,
            )
            usage = _usage_with_mode(blueprint_result, generation_mode=generation_mode)
        except PlanningModelUnavailable as exc:
            if exc.error.error_type not in FALLBACK_ERROR_TYPES:
                raise
            generation_mode = "two_pass_fallback"
            initial_failure_attempts = _safe_attempts(exc.error.attempts)
            narrative_result = None
            try:
                narrative_result = model.complete_contract(
                    stage="execution_narrative",
                    task_type="planning_execution",
                    feature=f"{feature_prefix}_narrative_fallback",
                    system=narrative_system,
                    payload=common_payload,
                    contract_type=ExecutionNarrative,
                    temperature=0.2,
                )
                blueprint_result = model.complete_contract(
                    stage="execution_design",
                    task_type="planning_execution",
                    feature=f"{feature_prefix}_blueprint_fallback",
                    system=blueprint_system,
                    payload={
                        **common_payload,
                        "executionNarrative": narrative_result.artifact.model_dump(by_alias=True),
                    },
                    contract_type=ExecutionBlueprint,
                    temperature=0.2,
                )
            except PlanningModelUnavailable as fallback_exc:
                prefix = [
                    *initial_failure_attempts,
                    *_safe_attempts(
                        narrative_result.model_usage.get("attempts")
                        if narrative_result is not None
                        else [],
                        retry_reason="execution_two_pass_fallback",
                    ),
                ]
                raise _failure_with_attempts(
                    fallback_exc,
                    prefix_attempts=prefix,
                    retry_reason="execution_two_pass_fallback",
                ) from fallback_exc
            usage = _merge_usage(
                [narrative_result.model_usage, blueprint_result.model_usage],
                generation_mode=generation_mode,
                prefix_attempts=initial_failure_attempts,
                retry_reason_by_index={
                    0: "execution_two_pass_fallback",
                    1: "execution_two_pass_fallback",
                },
            )

    blueprint = blueprint_result.artifact
    try:
        validate_execution_preflight(blueprint, goal=goal, reality=reality)
    except DeterministicGuardError as first_preflight:
        try:
            repaired = model.complete_contract(
                stage="execution_design",
                task_type="planning_execution",
                feature=f"{feature_prefix}_preflight_repair",
                system=(
                    single_pass_system
                    + "\nThe previous complete blueprint failed deterministic preflight. Return one complete "
                    "replacement blueprint that corrects every supplied preflight issue without changing "
                    "unrelated user facts, approved strategy choices, or hard constraints."
                ),
                payload={
                    **common_payload,
                    "invalidExecutionBlueprint": blueprint.model_dump(by_alias=True),
                    "preflightIssues": first_preflight.issues,
                    "preflightContext": execution_preflight_context(
                        blueprint,
                        goal=goal,
                    ),
                },
                contract_type=ExecutionBlueprint,
                temperature=0.2,
            )
        except PlanningModelUnavailable as repair_exc:
            raise _failure_with_attempts(
                repair_exc,
                prefix_attempts=usage.get("attempts") or [],
                retry_reason="execution_preflight",
            ) from repair_exc
        usage = _merge_usage(
            [usage, repaired.model_usage],
            generation_mode=generation_mode,
            retry_reason_by_index={1: "execution_preflight"},
        )
        blueprint = repaired.artifact
        try:
            validate_execution_preflight(blueprint, goal=goal, reality=reality)
        except DeterministicGuardError as final_preflight:
            raise _preflight_failure(
                final_preflight.issues,
                attempts=usage.get("attempts") or [],
            ) from final_preflight

    return AgentResult(artifact=blueprint, model_usage=usage)


__all__ = ["FALLBACK_ERROR_TYPES", "generate_execution_blueprint"]
