from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol
import time

import httpx

from ..api_key import INVALID_API_KEY_MESSAGE, validate_api_key_format
from .ai_settings import (
    EffectiveAiSettings,
    KEYED_PROVIDERS,
    get_auto_model_provider_chain,
    get_effective_ai_settings_for_provider,
    get_model_routing_rule,
    mark_provider_key_invalid,
    mark_provider_key_valid,
)


PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "kimi": "https://api.moonshot.ai/v1",
    "zhipu_glm": "https://open.bigmodel.cn/api/paas/v4",
    "openai": "https://api.openai.com/v1",
    "custom": "",
    "mock": "https://api.deepseek.com",
}


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-k2.7-code",
    "zhipu_glm": "glm-4-flash",
    "openai": "gpt-4o-mini",
    "custom": "gpt-4o-mini",
    "mock": "deepseek-v4-flash",
}


STANDARD_ERROR_TYPES = {
    "auth_error",
    "bad_model",
    "bad_base_url",
    "bad_request",
    "network_error",
    "timeout",
    "rate_limit",
    "insufficient_balance",
    "invalid_key_format",
    "invalid_model_output",
    "model_output_truncated",
    "unknown",
}
DIRECT_ROUTING_TASK_TYPES = {"settings_test"}
TRUNCATION_RETRY_TASK_TYPES = {
    "planning_goal_model",
    "planning_reality",
    "planning_evidence",
    "planning_strategy",
    "planning_execution",
    "planning_critique",
    "planning_learning",
}


@dataclass(frozen=True)
class ModelRouteAttempt:
    provider: str
    model: str | None = None
    status: str = "error"
    error_type: str | None = None
    latency_ms: int | None = None
    automatic_retry: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "errorType": self.error_type,
            "latencyMs": self.latency_ms,
        }
        if self.automatic_retry:
            data["automaticRetry"] = True
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class ModelCallRequest:
    task_type: str
    feature: str
    system: str
    user: str
    max_tokens: int = 800
    temperature: float | None = None
    timeout_seconds: int | None = None
    response_format_json: bool = False
    max_token_cap: int = 4000


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def to_legacy_dict(self) -> dict[str, int] | None:
        data = {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
        }
        cleaned = {key: value for key, value in data.items() if value is not None}
        return cleaned or None


@dataclass(frozen=True)
class ModelCallResult:
    text: str
    provider: str
    model: str
    usage: ModelUsage | None = None
    latency_ms: int | None = None
    mode: str = "llm"
    error_type: str | None = None
    attempts: tuple[ModelRouteAttempt, ...] = ()
    fallback_used: bool = False
    local_fallback_allowed: bool = False


@dataclass(frozen=True)
class ModelCallError:
    message: str
    error_type: str
    status_code: int = 0
    detail: str = ""
    provider: str | None = None
    model: str | None = None
    attempts: tuple[ModelRouteAttempt, ...] = ()
    fallback_used: bool = False
    local_fallback_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "error_type": self.error_type,
            "status_code": self.status_code,
            "detail": self.detail,
            "provider": self.provider,
            "model": self.model,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "fallback_used": self.fallback_used,
            "local_fallback_allowed": self.local_fallback_allowed,
        }


class ModelProvider(Protocol):
    def complete(self, request: ModelCallRequest) -> tuple[ModelCallResult | None, ModelCallError | None]:
        ...

    def health_check(self) -> tuple[bool, ModelCallError | None]:
        ...


def provider_default_base_url(provider: str) -> str:
    return PROVIDER_DEFAULT_BASE_URLS.get(provider, "")


def provider_default_model(provider: str) -> str:
    return PROVIDER_DEFAULT_MODELS.get(provider, PROVIDER_DEFAULT_MODELS["custom"])


def merge_openai_compatible_extra_body(payload: dict[str, Any], settings: EffectiveAiSettings) -> dict[str, Any]:
    if settings.provider not in {"local", "custom"} or "qwen" not in settings.model.casefold():
        return payload
    existing = payload.get("chat_template_kwargs")
    payload["chat_template_kwargs"] = (
        {"enable_thinking": False, **existing}
        if isinstance(existing, dict)
        else {"enable_thinking": False}
    )
    if settings.provider == "local":
        payload.setdefault("reasoning_effort", "none")
    return payload


def normalize_chat_completions_url(provider: str, base_url: str) -> str:
    cleaned = (base_url or provider_default_base_url(provider)).strip().rstrip("/")
    if not cleaned:
        cleaned = "https://api.openai.com/v1"
    if cleaned.endswith("/chat/completions"):
        return cleaned

    if provider == "deepseek":
        if cleaned.endswith("/v1"):
            cleaned = cleaned.removesuffix("/v1")
        return f"{cleaned}/chat/completions"

    if provider == "openai":
        if cleaned == "https://api.openai.com":
            return f"{cleaned}/v1/chat/completions"
        if cleaned.endswith("/v1"):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/v1/chat/completions"

    if provider == "kimi":
        if cleaned in ("https://api.moonshot.cn", "https://api.moonshot.ai"):
            return f"{cleaned}/v1/chat/completions"
        if cleaned.endswith("/v1"):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/chat/completions"

    if provider == "zhipu_glm":
        if cleaned == "https://open.bigmodel.cn":
            return f"{cleaned}/api/paas/v4/chat/completions"
        if cleaned.endswith(("/v1", "/v4")):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/chat/completions"

    if cleaned.endswith(("/v1", "/v4")):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def classify_http_error(status_code: int, body: str, *, provider: str | None = None, model: str | None = None) -> ModelCallError:
    body_lower = body.lower() if body else ""
    detail = body[:200] if body else ""
    if status_code in (401, 403):
        return ModelCallError("API key is invalid or expired.", "auth_error", status_code, detail=detail, provider=provider, model=model)
    if status_code == 429:
        return ModelCallError("The model service rate limit was reached.", "rate_limit", status_code, detail=detail, provider=provider, model=model)
    if status_code == 402 or any(term in body_lower for term in ("insufficient", "quota", "balance", "credit")):
        return ModelCallError("The model account has insufficient balance.", "insufficient_balance", status_code, detail=detail, provider=provider, model=model)
    if status_code in (400, 422):
        if "response_format" in body_lower:
            return ModelCallError("The model did not accept the JSON response format option.", "invalid_model_output", status_code, detail=detail, provider=provider, model=model)
        if any(term in body_lower for term in ("temperature", "top_p", "presence_penalty", "frequency_penalty")):
            return ModelCallError("The model service rejected the request parameters.", "bad_request", status_code, detail=detail, provider=provider, model=model)
        if any(term in body_lower for term in ("model", "does not exist", "not found", "not supported")):
            return ModelCallError("The model name does not exist or is not supported.", "bad_model", status_code, detail=detail, provider=provider, model=model)
        if "invalid_request" in body_lower:
            return ModelCallError("The model service rejected the request parameters.", "bad_request", status_code, detail=detail, provider=provider, model=model)
        return ModelCallError("The request is invalid. Check the Base URL, model name, and request parameters.", "bad_request", status_code, detail=detail, provider=provider, model=model)
    if status_code == 404:
        if "model" in body_lower and any(term in body_lower for term in ("does not exist", "not found", "not supported")):
            return ModelCallError("The model name does not exist or is not supported.", "bad_model", status_code, detail=detail, provider=provider, model=model)
        return ModelCallError("The Base URL endpoint is unavailable. Check the API path.", "bad_base_url", status_code, detail=detail, provider=provider, model=model)
    if status_code >= 500:
        return ModelCallError("The model service returned a server error. Try again later.", "unknown", status_code, detail=detail, provider=provider, model=model)
    return ModelCallError(f"Request failed (HTTP {status_code}).", "unknown", status_code, detail=detail, provider=provider, model=model)


def needs_reasoning_budget(settings: EffectiveAiSettings) -> bool:
    model = settings.model.lower()
    return settings.provider == "deepseek" and ("v4" in model or "reasoner" in model)


def effective_max_tokens(settings: EffectiveAiSettings, max_tokens: int, *, max_token_cap: int = 4000) -> int:
    token_limit = max(1, min(max_tokens, max_token_cap))
    if needs_reasoning_budget(settings):
        token_limit = max(token_limit, 512)
    return token_limit


def kimi_payload_temperature(settings: EffectiveAiSettings, request: ModelCallRequest) -> float | None:
    model = settings.model.lower()
    if model.startswith(("kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5")):
        return None
    raw_temperature = settings.temperature if request.temperature is None else request.temperature
    return max(0, min(float(raw_temperature), 1))


def message_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


def usage_from_response(data: dict[str, Any]) -> ModelUsage | None:
    raw = data.get("usage")
    if not isinstance(raw, dict):
        return None

    def read_int(*keys: str) -> int | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, int):
                return value
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
        return None

    usage = ModelUsage(
        prompt_tokens=read_int("prompt_tokens", "promptTokens", "input_tokens", "inputTokens"),
        completion_tokens=read_int("completion_tokens", "completionTokens", "output_tokens", "outputTokens"),
        total_tokens=read_int("total_tokens", "totalTokens"),
    )
    return usage if usage.to_legacy_dict() else None


class MockProvider:
    def __init__(self, settings: EffectiveAiSettings):
        self.settings = settings

    def complete(self, request: ModelCallRequest) -> tuple[ModelCallResult | None, ModelCallError | None]:
        return (
            ModelCallResult(
                text="",
                provider=self.settings.provider,
                model=self.settings.model,
                usage=None,
                latency_ms=0,
                mode="local_fallback",
            ),
            None,
        )

    def health_check(self) -> tuple[bool, ModelCallError | None]:
        return (True, None)


class OpenAICompatibleProvider:
    def __init__(self, settings: EffectiveAiSettings):
        self.settings = settings

    @property
    def provider(self) -> str:
        return self.settings.provider

    def _headers(self) -> dict[str, str]:
        headers = {"Accept-Encoding": "gzip, deflate"}
        if self.settings.api_key.strip():
            headers["Authorization"] = "Bearer" + " " + self.settings.api_key.strip()
        return headers

    def _auth_error(self) -> ModelCallError | None:
        if self.provider == "local":
            return None
        if not self.settings.api_key.strip():
            return ModelCallError(
                f"{self.provider} API key is not configured.",
                "auth_error",
                provider=self.provider,
                model=self.settings.model,
            )
        api_key_error = validate_api_key_format(self.settings.api_key)
        if api_key_error:
            return ModelCallError(api_key_error, "invalid_key_format", provider=self.provider, model=self.settings.model)
        return None

    def _payload(self, request: ModelCallRequest) -> dict[str, Any]:
        token_limit = effective_max_tokens(self.settings, request.max_tokens, max_token_cap=request.max_token_cap)
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
        }
        if self.provider == "kimi":
            temperature = kimi_payload_temperature(self.settings, request)
            if temperature is not None:
                payload["temperature"] = temperature
            payload["max_completion_tokens"] = token_limit
        else:
            payload["temperature"] = self.settings.temperature if request.temperature is None else request.temperature
            payload["max_tokens"] = token_limit
        if request.response_format_json:
            payload["response_format"] = {"type": "json_object"}
            # DeepSeek V4 defaults to thinking mode.  Execution design is the
            # longest structured stage and already receives an
            # explicit JSON Schema plus local validation.  Disable hidden
            # reasoning only there so large blueprints finish within the
            # synchronous connection budget; Goal/Evidence/Strategy keep their
            # reasoning quality.
            if (
                self.provider == "deepseek"
                and self.settings.model.startswith("deepseek-v4-")
                and request.task_type == "planning_execution"
                and request.feature.endswith(
                    (
                        "execution_narrative",
                        "execution_narrative_fallback",
                        "execution_blueprint_fallback",
                        "execution_single_pass",
                        "execution_preflight_repair",
                    )
                )
            ):
                payload["thinking"] = {"type": "disabled"}
        return merge_openai_compatible_extra_body(payload, self.settings)

    def complete(self, request: ModelCallRequest) -> tuple[ModelCallResult | None, ModelCallError | None]:
        auth_error = self._auth_error()
        if auth_error:
            return (None, auth_error)

        payload = self._payload(request)
        request_timeout = request.timeout_seconds if request.timeout_seconds is not None else self.settings.timeout_seconds
        start = time.perf_counter()
        try:
            with httpx.Client(timeout=max(1, request_timeout), trust_env=False) as client:
                response = client.post(
                    normalize_chat_completions_url(self.provider, self.settings.base_url),
                    headers=self._headers(),
                    json=payload,
                )
                if response.status_code != 200:
                    body = response.text
                    if request.response_format_json and response.status_code in (400, 422) and "response_format" in body.lower():
                        payload.pop("response_format", None)
                        response = client.post(
                            normalize_chat_completions_url(self.provider, self.settings.base_url),
                            headers=self._headers(),
                            json=payload,
                        )
                        if response.status_code != 200:
                            body = response.text
                    if response.status_code != 200:
                        return (None, classify_http_error(response.status_code, body, provider=self.provider, model=self.settings.model))

                data = response.json()
                latency_ms = max(1, int((time.perf_counter() - start) * 1000))
        except httpx.TimeoutException:
            return (None, ModelCallError("The model request timed out. Check the network or increase timeout.", "timeout", provider=self.provider, model=self.settings.model))
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            return (None, ModelCallError("The Base URL is invalid. Check whether the address is correct.", "bad_base_url", provider=self.provider, model=self.settings.model))
        except httpx.ConnectError as exc:
            return (None, ModelCallError("The model service cannot be reached. Check the network or service availability.", "network_error", detail=str(exc), provider=self.provider, model=self.settings.model))
        except httpx.RemoteProtocolError as exc:
            return (None, ModelCallError("The model service returned a protocol error. Check the Base URL.", "network_error", detail=str(exc), provider=self.provider, model=self.settings.model))
        except UnicodeEncodeError:
            return (None, ModelCallError(INVALID_API_KEY_MESSAGE, "invalid_key_format", provider=self.provider, model=self.settings.model))
        except ValueError as exc:
            return (None, ModelCallError(f"The model response format is invalid: {exc}", "invalid_model_output", provider=self.provider, model=self.settings.model))
        except Exception as exc:
            return (None, ModelCallError(f"Request failed: {exc}", "unknown", provider=self.provider, model=self.settings.model))

        return self._parse_response(data, latency_ms, request)

    def _parse_response(
        self,
        data: dict[str, Any],
        latency_ms: int,
        request: ModelCallRequest,
    ) -> tuple[ModelCallResult | None, ModelCallError | None]:
        try:
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                return (None, ModelCallError("The model response did not include choices.", "invalid_model_output", provider=self.provider, model=self.settings.model))
            choice = choices[0]
            if not isinstance(choice, dict):
                return (None, ModelCallError("The model response choice is invalid.", "invalid_model_output", provider=self.provider, model=self.settings.model))
            finish_reason = str(choice.get("finish_reason") or "")
            message = choice.get("message")
            if not isinstance(message, dict):
                return (None, ModelCallError("The model response did not include a message.", "invalid_model_output", provider=self.provider, model=self.settings.model))
            content = message_content(message)
            if finish_reason == "length":
                if request.task_type == "chat":
                    return (
                        ModelCallResult(
                            text=f"{content}\n\n（回答达到长度上限）" if content else "回答达到长度上限。",
                            provider=self.provider,
                            model=self.settings.model,
                            usage=usage_from_response(data),
                            latency_ms=latency_ms,
                            mode="llm",
                        ),
                        None,
                    )
                return (
                    None,
                    ModelCallError(
                        "The model output was truncated before completion.",
                        "model_output_truncated",
                        detail="finish_reason=length",
                        provider=self.provider,
                        model=self.settings.model,
                    ),
                )
            if not content:
                reasoning_len = len(str(message.get("reasoning_content") or ""))
                return (
                    None,
                    ModelCallError(
                        "The model returned empty content. Increase max tokens or use a non-reasoning model.",
                        "invalid_model_output",
                        detail=f"finish_reason={finish_reason}; reasoning_tokens={reasoning_len}",
                        provider=self.provider,
                        model=self.settings.model,
                    ),
                )
            return (
                ModelCallResult(
                    text=content,
                    provider=self.provider,
                    model=self.settings.model,
                    usage=usage_from_response(data),
                    latency_ms=latency_ms,
                    mode="llm",
                ),
                None,
            )
        except Exception as exc:
            return (None, ModelCallError(f"The model response format is invalid: {exc}", "invalid_model_output", provider=self.provider, model=self.settings.model))

    def health_check(self) -> tuple[bool, ModelCallError | None]:
        auth_error = self._auth_error()
        if auth_error:
            return (False, auth_error)
        return (True, None)


class DeepSeekProvider(OpenAICompatibleProvider):
    pass


class LocalProvider(OpenAICompatibleProvider):
    pass


class ModelRouter:
    def __init__(self, settings: EffectiveAiSettings, *, routing_enabled: bool = True, track_credentials: bool = True):
        self.settings = settings
        self.routing_enabled = routing_enabled
        self.track_credentials = track_credentials

    def _provider(self, settings: EffectiveAiSettings | None = None) -> ModelProvider:
        selected = settings or self.settings
        provider = selected.provider
        if provider == "mock":
            return MockProvider(selected)
        if provider == "deepseek":
            return DeepSeekProvider(selected)
        if provider == "local":
            return LocalProvider(selected)
        return OpenAICompatibleProvider(selected)

    def _complete_direct(
        self,
        request: ModelCallRequest,
        settings: EffectiveAiSettings | None = None,
    ) -> tuple[ModelCallResult | None, ModelCallError | None]:
        selected = settings or self.settings
        result, error = self._provider(selected).complete(request)
        should_track_credential = self.track_credentials and request.feature != "settings_save_validation"
        if should_track_credential and result and result.mode == "llm":
            mark_provider_key_valid(selected.provider, selected.api_key)
        elif should_track_credential and error and error.error_type in {"auth_error", "invalid_key_format"}:
            mark_provider_key_invalid(selected.provider, selected.api_key, error.error_type)
        return result, error

    @staticmethod
    def _can_retry_planning_truncation(
        request: ModelCallRequest,
        error: ModelCallError | None,
        *,
        automatic_retry_attempted: bool,
    ) -> bool:
        return bool(
            not automatic_retry_attempted
            and request.task_type in TRUNCATION_RETRY_TASK_TYPES
            and request.response_format_json
            and error
            and error.error_type == "model_output_truncated"
        )

    @staticmethod
    def _planning_retry_request(request: ModelCallRequest) -> ModelCallRequest:
        retry_tokens = min(request.max_token_cap, max(request.max_tokens, request.max_tokens * 2))
        return replace(request, max_tokens=retry_tokens)

    @staticmethod
    def _route_attempt(
        settings: EffectiveAiSettings,
        result: ModelCallResult | None,
        error: ModelCallError | None,
        elapsed_ms: int,
        *,
        automatic_retry: bool = False,
    ) -> ModelRouteAttempt:
        if result and result.mode == "llm":
            return ModelRouteAttempt(
                provider=settings.provider,
                model=settings.model,
                status="success",
                latency_ms=result.latency_ms or elapsed_ms,
                automatic_retry=automatic_retry,
            )
        return ModelRouteAttempt(
            provider=settings.provider,
            model=settings.model,
            status="error",
            error_type=error.error_type if error else "unknown",
            latency_ms=elapsed_ms,
            automatic_retry=automatic_retry,
        )

    def _complete_direct_with_truncation_retry(
        self,
        request: ModelCallRequest,
    ) -> tuple[ModelCallResult | None, ModelCallError | None]:
        initial_start = time.perf_counter()
        result, error = self._complete_direct(request)
        initial_elapsed_ms = max(1, int((time.perf_counter() - initial_start) * 1000))
        if not self._can_retry_planning_truncation(request, error, automatic_retry_attempted=False):
            return result, error

        initial_attempt = self._route_attempt(self.settings, result, error, initial_elapsed_ms)
        retry_request = self._planning_retry_request(request)
        start = time.perf_counter()
        retry_result, retry_error = self._complete_direct(retry_request)
        elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
        retry_attempt = self._route_attempt(
            self.settings,
            retry_result,
            retry_error,
            elapsed_ms,
            automatic_retry=True,
        )
        attempts = (initial_attempt, retry_attempt)
        if retry_result and retry_result.mode == "llm":
            return replace(retry_result, attempts=attempts), None
        if retry_error:
            return None, replace(retry_error, attempts=attempts)
        return None, ModelCallError(
            "Model call failed without a result.",
            "unknown",
            provider=self.settings.provider,
            model=self.settings.model,
            attempts=attempts,
        )

    def complete(self, request: ModelCallRequest) -> tuple[ModelCallResult | None, ModelCallError | None]:
        if not self.routing_enabled or request.task_type in DIRECT_ROUTING_TASK_TYPES:
            return self._complete_direct_with_truncation_retry(request)

        rule = get_model_routing_rule(request.task_type, self.settings.provider)
        if rule.primary_provider == "auto":
            chain = list(get_auto_model_provider_chain(request.task_type, rule.fallback_providers))
            configured_primary = chain[0] if chain else "deepseek"
        else:
            configured_primary = rule.primary_provider
            chain: list[str] = []
            for provider in [configured_primary, *rule.fallback_providers]:
                if provider in KEYED_PROVIDERS and provider not in chain:
                    chain.append(provider)

        attempts: list[ModelRouteAttempt] = []
        last_error: ModelCallError | None = None
        automatic_retry_attempted = False
        primary_provider = configured_primary if configured_primary in KEYED_PROVIDERS else (chain[0] if chain else "deepseek")
        for provider in chain:
            routed_settings = get_effective_ai_settings_for_provider(provider, self.settings)
            if routed_settings.provider != "local" and routed_settings.has_api_key and not routed_settings.has_usable_api_key:
                attempts.append(
                    ModelRouteAttempt(
                        provider=provider,
                        model=routed_settings.model,
                        status="skipped",
                        error_type=routed_settings.key_error_type or "auth_error",
                        latency_ms=0,
                    )
                )
                continue
            if not routed_settings.can_call:
                attempts.append(
                    ModelRouteAttempt(
                        provider=provider,
                        model=routed_settings.model,
                        status="skipped",
                        error_type="missing_api_key",
                        latency_ms=0,
                    )
                )
                if provider == "local" and provider == primary_provider:
                    last_error = ModelCallError(
                        "Local model Base URL and Model ID are not configured.",
                        "missing_model_config",
                        provider=provider,
                        model=routed_settings.model,
                    )
                    break
                continue

            start = time.perf_counter()
            result, error = self._complete_direct(request, routed_settings)
            elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
            if result and result.mode == "llm":
                attempt = self._route_attempt(routed_settings, result, error, elapsed_ms)
                all_attempts = tuple([*attempts, attempt])
                return (
                    replace(
                        result,
                        attempts=all_attempts,
                        fallback_used=routed_settings.provider != primary_provider,
                        local_fallback_allowed=rule.local_fallback_enabled,
                    ),
                    None,
                )

            if error:
                attempts.append(self._route_attempt(routed_settings, result, error, elapsed_ms))
                last_error = error
                if provider == "local":
                    break
                if self._can_retry_planning_truncation(
                    request,
                    error,
                    automatic_retry_attempted=automatic_retry_attempted,
                ):
                    automatic_retry_attempted = True
                    retry_request = self._planning_retry_request(request)
                    retry_start = time.perf_counter()
                    retry_result, retry_error = self._complete_direct(retry_request, routed_settings)
                    retry_elapsed_ms = max(1, int((time.perf_counter() - retry_start) * 1000))
                    retry_attempt = self._route_attempt(
                        routed_settings,
                        retry_result,
                        retry_error,
                        retry_elapsed_ms,
                        automatic_retry=True,
                    )
                    attempts.append(retry_attempt)
                    if retry_result and retry_result.mode == "llm":
                        return (
                            replace(
                                retry_result,
                                attempts=tuple(attempts),
                                fallback_used=routed_settings.provider != primary_provider,
                                local_fallback_allowed=rule.local_fallback_enabled,
                            ),
                            None,
                        )
                    if retry_error:
                        last_error = retry_error
                continue

            attempts.append(
                ModelRouteAttempt(
                    provider=routed_settings.provider,
                    model=routed_settings.model,
                    status="error",
                    error_type="unknown",
                    latency_ms=elapsed_ms,
                )
            )

        if not last_error:
            last_error = ModelCallError(
                "No routed model provider has a saved API key.",
                "auth_error",
                provider=primary_provider,
                model=provider_default_model(primary_provider),
            )
        return (
            None,
            replace(
                last_error,
                attempts=tuple(attempts),
                fallback_used=any(attempt.provider != primary_provider for attempt in attempts),
                local_fallback_allowed=rule.local_fallback_enabled,
            ),
        )

    def health_check(self) -> tuple[bool, ModelCallError | None]:
        return self._provider().health_check()
