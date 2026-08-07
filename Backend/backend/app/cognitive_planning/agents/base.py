from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ...harness.recovery import recover_json_object
from ...services.llm import LlmClient, LlmError, LlmResult
from ..contracts import SafePlanningError


ContractT = TypeVar("ContractT", bound=BaseModel)

TOKEN_ENV_BY_TASK = {
    "planning_understanding": ("PLANIX_GOAL_MODEL_MAX_TOKENS", 5400),
    "planning_plan": ("PLANIX_EXECUTION_MAX_TOKENS", 12000),
    "planning_review": ("PLANIX_CRITIQUE_MAX_TOKENS", 6600),
    "planning_learning": ("PLANIX_LEARNING_MAX_TOKENS", 5400),
}
TOKEN_CAP_BY_TASK = {
    "planning_understanding": 10800,
    "planning_plan": 24000,
    "planning_review": 13200,
    "planning_learning": 10800,
}


class PlanningModelUnavailable(RuntimeError):
    def __init__(self, stage: str, error: SafePlanningError):
        super().__init__(error.message)
        self.stage = stage
        self.error = error


@dataclass(frozen=True)
class AgentResult(Generic[ContractT]):
    artifact: ContractT
    model_usage: dict[str, Any]


def _extract_json(value: str) -> dict[str, Any]:
    return recover_json_object(value) or {}


def _safe_error(stage: str, error: LlmError | None, message: str) -> SafePlanningError:
    return SafePlanningError(
        stage=stage,
        errorType=error.error_type if error else "model_unavailable",
        message=message,
        retryable=(error.error_type if error else "") not in {"auth_error", "invalid_key_format"},
        attempts=error.attempts or [] if error else [],
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _usage(result: LlmResult, task_type: str, trace: dict[str, Any]) -> dict[str, Any]:
    raw = result.usage or {}
    return {
        "provider": result.provider,
        "model": result.model,
        "promptTokens": raw.get("promptTokens") or raw.get("prompt_tokens"),
        "completionTokens": raw.get("completionTokens") or raw.get("completion_tokens"),
        "totalTokens": raw.get("totalTokens") or raw.get("total_tokens"),
        "latencyMs": result.latency_ms,
        "mode": "llm",
        "taskType": task_type,
        "fallbackUsed": result.fallback_used,
        "localFallbackAllowed": False,
        "attempts": result.attempts or [],
        "trace": trace,
    }


def _retry_attempts(attempts: list[dict[str, Any]] | None, *, provider: str, model: str, status: str, error_type: str | None = None) -> list[dict[str, Any]]:
    source = attempts or [{"provider": provider, "model": model, "status": status, "errorType": error_type}]
    return [{**attempt, "automaticRetry": True, "retryReason": "contract_validation"} for attempt in source]


def _merge_results(first: LlmResult, repaired: LlmResult) -> LlmResult:
    first_usage = first.usage or {}
    repaired_usage = repaired.usage or {}
    usage: dict[str, int] = {}
    for key in ("promptTokens", "completionTokens", "totalTokens", "prompt_tokens", "completion_tokens", "total_tokens"):
        if key in first_usage or key in repaired_usage:
            usage[key] = int(first_usage.get(key) or 0) + int(repaired_usage.get(key) or 0)
    return replace(
        repaired,
        usage=usage or None,
        latency_ms=int(first.latency_ms or 0) + int(repaired.latency_ms or 0),
        attempts=[
            *(first.attempts or []),
            *_retry_attempts(repaired.attempts, provider=repaired.provider, model=repaired.model, status="success"),
        ],
        fallback_used=bool(first.fallback_used or repaired.fallback_used),
        local_fallback_allowed=False,
    )


def _validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "location": ".".join(str(part) for part in item.get("loc", [])) or "root",
            "message": str(item.get("msg") or "schema validation failed"),
        }
        for item in exc.errors()[:6]
    ]


class CognitiveModelClient:
    def __init__(self, llm: LlmClient | None = None):
        self.llm = llm or LlmClient()

    def complete_contract(
        self,
        *,
        stage: str,
        task_type: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        contract_type: type[ContractT],
        temperature: float = 0.2,
        validation_context: dict[str, Any] | None = None,
    ) -> AgentResult[ContractT]:
        agent_clock = time.perf_counter()
        prompt_start = _timestamp()
        prompt_clock = time.perf_counter()
        env_name, default_tokens = TOKEN_ENV_BY_TASK[task_type]
        token_cap = TOKEN_CAP_BY_TASK[task_type]
        try:
            max_tokens = max(256, min(int(os.getenv(env_name, default_tokens)), token_cap))
        except ValueError:
            max_tokens = default_tokens
        schema = contract_type.model_json_schema(by_alias=True)
        user = json.dumps({"input": payload, "requiredOutputSchema": schema}, ensure_ascii=False, separators=(",", ":"))
        prompt_end = _timestamp()
        prompt_latency = max(0, int((time.perf_counter() - prompt_clock) * 1000))
        request_start = _timestamp()
        request_clock = time.perf_counter()
        result, error = self.llm.complete(
            feature,
            system,
            user,
            max_tokens=max_tokens,
            max_token_cap=token_cap,
            temperature=temperature,
            response_format_json=True,
            task_type=task_type,
        )
        request_end = _timestamp()
        provider_latency = max(0, int((time.perf_counter() - request_clock) * 1000))
        if not result:
            raise PlanningModelUnavailable(stage, _safe_error(stage, error, error.message if error else "No configured model completed this planning stage."))

        parse_start = _timestamp()
        parse_clock = time.perf_counter()
        raw = _extract_json(result.content)
        if not raw:
            raise PlanningModelUnavailable(
                stage,
                SafePlanningError(
                    stage=stage,
                    errorType="invalid_model_output",
                    message="The model did not return a JSON object for this planning stage.",
                    retryable=True,
                    attempts=result.attempts or [],
                ),
            )
        retry_start = retry_end = None
        retry_latency = 0
        try:
            artifact = contract_type.model_validate(raw, context=validation_context)
        except ValidationError as exc:
            repair_user = json.dumps(
                {
                    "input": payload,
                    "requiredOutputSchema": schema,
                    "invalidOutput": raw,
                    "validationErrors": _validation_errors(exc),
                    "repairRules": [
                        "Return one corrected JSON object only.",
                        "Preserve all user facts and immutable constraints.",
                        "Do not invent evidence, resources, dates, or approvals.",
                        "Correct every listed validation error.",
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            retry_start = _timestamp()
            retry_clock = time.perf_counter()
            repaired, repair_error = self.llm.complete(
                feature,
                system + "\nThe previous object failed schema validation. Repair only the contract errors.",
                repair_user,
                max_tokens=max_tokens,
                max_token_cap=token_cap,
                temperature=temperature,
                response_format_json=True,
                task_type=task_type,
            )
            retry_end = _timestamp()
            retry_latency = max(0, int((time.perf_counter() - retry_clock) * 1000))
            combined_attempts = list(result.attempts or [])
            if repaired:
                combined_attempts.extend(_retry_attempts(repaired.attempts, provider=repaired.provider, model=repaired.model, status="success"))
            elif repair_error:
                combined_attempts.extend(_retry_attempts(repair_error.attempts, provider=result.provider, model=result.model, status="error", error_type=repair_error.error_type))
            if not repaired:
                safe = _safe_error(stage, repair_error, repair_error.message if repair_error else "The contract repair model did not return a result.")
                raise PlanningModelUnavailable(stage, safe.model_copy(update={"attempts": combined_attempts})) from exc
            try:
                artifact = contract_type.model_validate(_extract_json(repaired.content), context=validation_context)
            except ValidationError as repaired_exc:
                raise PlanningModelUnavailable(
                    stage,
                    SafePlanningError(
                        stage=stage,
                        errorType="invalid_model_output",
                        message=f"The model output failed the {stage} contract after automatic repair.",
                        retryable=True,
                        attempts=combined_attempts,
                    ),
                ) from repaired_exc
            result = _merge_results(result, repaired)
        parse_end = _timestamp()
        parse_latency = max(0, int((time.perf_counter() - parse_clock) * 1000))
        attempts = [item for item in (result.attempts or []) if item.get("status") != "skipped"]
        retry_latency = max(
            retry_latency,
            sum(max(0, int(item.get("latencyMs") or 0)) for item in attempts if item.get("automaticRetry") or item.get("retryReason")),
        )
        trace = {
            "promptBuildStart": prompt_start,
            "promptBuildEnd": prompt_end,
            "httpRequestStart": request_start,
            "httpRequestEnd": request_end,
            "schemaParseStart": parse_start,
            "schemaParseEnd": parse_end,
            "retryStart": retry_start,
            "retryEnd": retry_end,
            "agentEnd": _timestamp(),
            "promptBuildLatencyMs": prompt_latency,
            "providerLatencyMs": provider_latency,
            "parseLatencyMs": parse_latency,
            "retryLatencyMs": retry_latency,
            "totalLatencyMs": max(0, int((time.perf_counter() - agent_clock) * 1000)),
            "modelCalls": max(1, len(attempts)),
            "modelRetries": sum(1 for item in attempts if item.get("automaticRetry") or item.get("retryReason")),
            "retryReasons": [str(item.get("retryReason")) for item in attempts if item.get("retryReason")],
        }
        return AgentResult(artifact=artifact, model_usage=_usage(result, task_type, trace))


__all__ = ["AgentResult", "CognitiveModelClient", "PlanningModelUnavailable"]
