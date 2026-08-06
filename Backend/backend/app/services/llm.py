from dataclasses import dataclass, replace
import json
from typing import Any, Iterator
from uuid import uuid4

import httpx

from ..api_key import INVALID_API_KEY_MESSAGE, validate_api_key_format
from ..db import get_conn
from .ai_settings import EffectiveAiSettings, get_effective_ai_settings
from .model_provider import (
    ModelCallRequest,
    ModelRouter,
    classify_http_error,
    effective_max_tokens,
    merge_openai_compatible_extra_body,
    message_content,
    needs_reasoning_budget,
    normalize_chat_completions_url,
    usage_from_response,
)


@dataclass(frozen=True)
class LlmResult:
    content: str
    provider: str
    model: str
    usage: dict[str, int] | None = None
    latency_ms: int | None = None
    attempts: list[dict[str, Any]] | None = None
    fallback_used: bool | None = None
    local_fallback_allowed: bool | None = None


@dataclass(frozen=True)
class LlmError:
    message: str
    error_type: str
    status_code: int = 0
    detail: str = ""
    attempts: list[dict[str, Any]] | None = None
    fallback_used: bool | None = None
    local_fallback_allowed: bool | None = None

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "error_type": self.error_type,
            "status_code": self.status_code,
            "detail": self.detail,
            "attempts": self.attempts or [],
            "fallback_used": self.fallback_used,
            "local_fallback_allowed": self.local_fallback_allowed,
        }


def _chat_completions_url(base_url: str, provider: str) -> str:
    return normalize_chat_completions_url(provider, base_url)


def _classify_http_error(status_code: int, body: str) -> LlmError:
    error = classify_http_error(status_code, body)
    return LlmError(error.message, error.error_type, error.status_code, error.detail)


def _needs_reasoning_budget(settings: EffectiveAiSettings) -> bool:
    return needs_reasoning_budget(settings)


def _effective_max_tokens(settings: EffectiveAiSettings, max_tokens: int, *, max_token_cap: int = 4000) -> int:
    return effective_max_tokens(settings, max_tokens, max_token_cap=max_token_cap)


def _message_content(message: dict) -> str:
    return message_content(message)


def _usage_from_response(data: dict) -> dict[str, int] | None:
    usage = usage_from_response(data)
    return usage.to_legacy_dict() if usage else None


def record_ai_run(
    feature: str,
    settings: EffectiveAiSettings,
    input_summary: str,
    output_summary: str = "",
    success: bool = True,
    error: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_runs(
              id, feature, provider, model, input_summary, output_summary, success, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                feature,
                settings.provider,
                settings.model,
                input_summary[:4000],
                output_summary[:4000],
                int(success),
                error[:1000],
            ),
        )


def _settings_for_run(settings: EffectiveAiSettings, provider: str | None, model: str | None) -> EffectiveAiSettings:
    return replace(
        settings,
        provider=provider or settings.provider,
        model=model or settings.model,
    )


class LlmClient:
    def __init__(self):
        self.settings = get_effective_ai_settings()

    def is_enabled(self) -> bool:
        return (
            self.settings.provider != "mock"
            and self.settings.can_call
        )

    def complete(
        self,
        feature: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
        response_format_json: bool = False,
        max_token_cap: int = 4000,
        task_type: str | None = None,
        record_run: bool = True,
        routing_enabled: bool | None = None,
    ) -> tuple[LlmResult | None, LlmError | None]:
        """Returns (result, error). On success error is None; on failure result is None."""
        request = ModelCallRequest(
            task_type=task_type or feature,
            feature=feature,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_format_json=response_format_json,
            max_token_cap=max_token_cap,
        )
        result, error = ModelRouter(
            self.settings,
            routing_enabled=True if routing_enabled is None else routing_enabled,
            track_credentials=record_run,
        ).complete(request)
        if result and result.mode == "local_fallback":
            if record_run:
                record_ai_run(feature, self.settings, user, success=True, output_summary="local fallback")
            return (None, None)
        if error:
            llm_err = LlmError(
                error.message,
                error.error_type,
                error.status_code,
                error.detail,
                attempts=[attempt.to_dict() for attempt in error.attempts],
                fallback_used=error.fallback_used,
                local_fallback_allowed=error.local_fallback_allowed,
            )
            if record_run:
                record_ai_run(
                    feature,
                    _settings_for_run(self.settings, error.provider, error.model),
                    user,
                    success=False,
                    error="model output was truncated" if error.error_type == "model_output_truncated" else llm_err.message,
                )
            return (None, llm_err)
        if not result:
            llm_err = LlmError("Model call failed without a result.", "unknown", 0)
            if record_run:
                record_ai_run(feature, self.settings, user, success=False, error=llm_err.message)
            return (None, llm_err)

        if record_run:
            record_ai_run(
                feature,
                _settings_for_run(self.settings, result.provider, result.model),
                user,
                output_summary=result.text,
                success=True,
            )
        return (
            LlmResult(
                content=result.text,
                provider=result.provider,
                model=result.model,
                usage=result.usage.to_legacy_dict() if result.usage else None,
                latency_ms=result.latency_ms,
                attempts=[attempt.to_dict() for attempt in result.attempts],
                fallback_used=result.fallback_used,
                local_fallback_allowed=result.local_fallback_allowed,
            ),
            None,
        )

    def stream_tokens(
        self,
        feature: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
    ) -> Iterator[str]:
        """Yield OpenAI-compatible streaming tokens.

        This method intentionally does not call complete() and split a full
        response afterward. If real streaming is unavailable, callers should
        fall back at the runtime level.
        """
        if not self.is_enabled():
            return

        api_key_error = None if self.settings.provider == "local" else validate_api_key_format(self.settings.api_key)
        if api_key_error:
            record_ai_run(feature, self.settings, user, success=False, error=api_key_error)
            raise RuntimeError(api_key_error)

        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": _effective_max_tokens(self.settings, max_tokens),
            "stream": True,
        }
        merge_openai_compatible_extra_body(payload, self.settings)
        request_timeout = timeout_seconds if timeout_seconds is not None else self.settings.timeout_seconds
        output_parts: list[str] = []
        try:
            with httpx.Client(timeout=max(1, request_timeout), trust_env=False) as client:
                with client.stream(
                    "POST",
                    _chat_completions_url(self.settings.base_url, self.settings.provider),
                    headers={
                        "Authorization": "Bearer" + " " + self.settings.api_key.strip(),
                        "Accept-Encoding": "gzip, deflate",
                    },
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", errors="replace")
                        llm_err = _classify_http_error(response.status_code, body)
                        record_ai_run(feature, self.settings, user, success=False, error=llm_err.message)
                        raise RuntimeError(llm_err.message)

                    for line in response.iter_lines():
                        if not line:
                            continue
                        data = line.removeprefix("data:").strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            break
                        try:
                            payload_line = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choice = (payload_line.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        content = _message_content(delta)
                        if content:
                            output_parts.append(content)
                            yield content
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            record_ai_run(feature, self.settings, user, success=False, error=str(exc))
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            if not isinstance(exc, RuntimeError):
                record_ai_run(feature, self.settings, user, success=False, error=str(exc))
            raise

        record_ai_run(feature, self.settings, user, output_summary="".join(output_parts), success=True)
