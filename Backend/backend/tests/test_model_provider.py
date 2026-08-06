import json

import httpx
import pytest

from backend.app.db import get_conn
from backend.app.services.ai_settings import EffectiveAiSettings
from backend.app.services.ai_settings import ModelRoutingRuleConfig
from backend.app.services import llm as llm_module
from backend.app.services import model_provider
from backend.app.services.model_provider import (
    ModelCallError,
    ModelCallRequest,
    ModelCallResult,
    ModelRouter,
    ModelUsage,
    classify_http_error,
    normalize_chat_completions_url,
    provider_default_base_url,
    provider_default_model,
    usage_from_response,
)


@pytest.fixture(autouse=True)
def isolate_model_provider_database(tmp_path, monkeypatch):
    """Never let router unit tests overwrite the user's saved provider keys."""

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'model-provider-test.db'}")


def _settings(
    *,
    provider: str = "deepseek",
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-flash",
    api_key: str = "sk-test-local",
    key_status: str = "unchecked",
    key_error_type: str = "",
) -> EffectiveAiSettings:
    return EffectiveAiSettings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=0.2,
        timeout_seconds=10,
        updated_at="",
        key_status=key_status,
        key_error_type=key_error_type,
    )


def test_model_router_skips_a_saved_key_marked_invalid(monkeypatch):
    invalid = _settings(key_status="invalid", key_error_type="auth_error")

    monkeypatch.setattr(
        model_provider,
        "get_model_routing_rule",
        lambda *_: ModelRoutingRuleConfig(
            task_type="chat",
            primary_provider="deepseek",
            fallback_providers=(),
            local_fallback_enabled=False,
        ),
    )
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", lambda *_: invalid)
    monkeypatch.setattr(
        ModelRouter,
        "_complete_direct",
        lambda *_: (_ for _ in ()).throw(AssertionError("invalid key must not be called")),
    )

    result, error = ModelRouter(invalid).complete(_request(task_type="chat"))

    assert result is None
    assert error is not None
    assert [(attempt.status, attempt.error_type) for attempt in error.attempts] == [("skipped", "auth_error")]


def _request(**overrides):
    data = {
        "task_type": "settings_test",
        "feature": "settings_test",
        "system": "system",
        "user": "user",
        "max_tokens": 128,
    }
    data.update(overrides)
    return ModelCallRequest(**data)


def test_normalize_chat_completions_url_for_supported_providers():
    assert normalize_chat_completions_url("deepseek", "https://api.deepseek.com") == "https://api.deepseek.com/chat/completions"
    assert normalize_chat_completions_url("deepseek", "https://api.deepseek.com/v1") == "https://api.deepseek.com/chat/completions"
    assert normalize_chat_completions_url("deepseek", "https://api.deepseek.com/chat/completions") == "https://api.deepseek.com/chat/completions"
    assert normalize_chat_completions_url("openai", "https://api.openai.com") == "https://api.openai.com/v1/chat/completions"
    assert normalize_chat_completions_url("openai", "https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.cn") == "https://api.moonshot.cn/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.cn/v1") == "https://api.moonshot.cn/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.cn/v1/chat/completions") == "https://api.moonshot.cn/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.ai") == "https://api.moonshot.ai/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.ai/v1") == "https://api.moonshot.ai/v1/chat/completions"
    assert normalize_chat_completions_url("kimi", "https://api.moonshot.ai/v1/chat/completions") == "https://api.moonshot.ai/v1/chat/completions"
    assert normalize_chat_completions_url("zhipu_glm", "https://open.bigmodel.cn") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert normalize_chat_completions_url("zhipu_glm", "https://open.bigmodel.cn/api/paas/v4") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert normalize_chat_completions_url("custom", "https://example.test/v1") == "https://example.test/v1/chat/completions"
    assert normalize_chat_completions_url("custom", "https://example.test/chat/completions") == "https://example.test/chat/completions"


def test_kimi_defaults_use_current_official_endpoint_and_model():
    assert provider_default_base_url("kimi") == "https://api.moonshot.ai/v1"
    assert provider_default_model("kimi") == "kimi-k2.7-code"


def test_usage_parser_accepts_openai_and_compatible_token_names():
    usage = usage_from_response({"usage": {"prompt_tokens": "10", "completion_tokens": 5, "total_tokens": 15}})
    assert usage is not None
    assert usage.to_legacy_dict() == {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}

    compatible = usage_from_response({"usage": {"input_tokens": 7, "output_tokens": "3"}})
    assert compatible is not None
    assert compatible.to_legacy_dict() == {"promptTokens": 7, "completionTokens": 3}


def test_http_errors_map_to_standard_error_types():
    assert classify_http_error(401, "").error_type == "auth_error"
    assert classify_http_error(403, "").error_type == "auth_error"
    assert classify_http_error(404, "model does not exist").error_type == "bad_model"
    assert classify_http_error(404, "endpoint not found").error_type == "bad_base_url"
    assert classify_http_error(429, "too many requests").error_type == "rate_limit"
    assert classify_http_error(402, "").error_type == "insufficient_balance"
    assert classify_http_error(400, "response_format is not supported").error_type == "invalid_model_output"
    assert classify_http_error(400, "invalid_request_error: temperature does not support this model").error_type == "bad_request"
    assert classify_http_error(400, "invalid_request_error: top_p is not allowed").error_type == "bad_request"
    assert classify_http_error(400, "invalid_request_error: model does not exist").error_type == "bad_model"


def test_auth_and_model_errors_are_not_mixed():
    assert classify_http_error(403, "model does not exist").error_type == "auth_error"
    assert classify_http_error(404, "model does not exist").error_type == "bad_model"
    assert classify_http_error(400, "model temperature invalid").error_type == "bad_request"


def test_provider_selection_and_mock_vs_missing_key(monkeypatch):
    def fail_client(*args, **kwargs):
        raise AssertionError("mock provider must not create an HTTP client")

    monkeypatch.setattr(model_provider.httpx, "Client", fail_client)
    result, error = ModelRouter(_settings(provider="mock", api_key="")).complete(_request())
    assert error is None
    assert result is not None
    assert result.mode == "local_fallback"

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code", api_key="")).complete(_request())
    assert result is None
    assert error is not None
    assert error.error_type == "auth_error"
    assert error.provider == "kimi"


def test_settings_save_validation_does_not_write_credential_status(monkeypatch):
    writes = []

    monkeypatch.setattr(model_provider, "mark_provider_key_valid", lambda *args: writes.append(("valid", args)))
    monkeypatch.setattr(model_provider, "mark_provider_key_invalid", lambda *args: writes.append(("invalid", args)))
    monkeypatch.setattr(
        model_provider.OpenAICompatibleProvider,
        "complete",
        lambda self, request: (
            ModelCallResult(
                text="OK",
                provider=self.settings.provider,
                model=self.settings.model,
                usage=None,
                latency_ms=1,
                mode="llm",
            ),
            None,
        ),
    )

    result, error = ModelRouter(_settings(), routing_enabled=False).complete(
        _request(feature="settings_save_validation")
    )

    assert error is None
    assert result is not None
    assert writes == []


def test_openai_compatible_provider_posts_expected_payload(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}],"usage":{"input_tokens":4,"output_tokens":3,"total_tokens":7}}'

        def json(self):
            return {
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)
    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")).complete(
        _request(max_tokens=2000, max_token_cap=256, response_format_json=True)
    )

    assert error is None
    assert result is not None
    assert result.provider == "kimi"
    assert result.model == "kimi-k2.7-code"
    assert result.usage and result.usage.to_legacy_dict() == {"promptTokens": 4, "completionTokens": 3, "totalTokens": 7}
    assert calls[0]["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert calls[0]["json"]["max_completion_tokens"] == 256
    assert "max_tokens" not in calls[0]["json"]
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}

    calls.clear()
    result, error = ModelRouter(_settings(provider="openai", base_url="https://api.openai.com/v1", model="gpt-4o-mini")).complete(
        _request(max_tokens=2000, max_token_cap=256)
    )

    assert error is None
    assert result is not None
    assert calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert calls[0]["json"]["max_tokens"] == 256
    assert "max_completion_tokens" not in calls[0]["json"]


def test_deepseek_v4_execution_generation_requests_disable_thinking(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}'

        def json(self):
            return {
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)
    router = ModelRouter(_settings(provider="deepseek", model="deepseek-v4-flash"), routing_enabled=False)

    for feature in (
        "cognitive_execution_narrative",
        "cognitive_execution_narrative_fallback",
        "cognitive_execution_blueprint_fallback",
        "cognitive_execution_single_pass",
        "cognitive_os_execution_single_pass",
        "cognitive_execution_preflight_repair",
    ):
        result, error = router.complete(
            _request(
                response_format_json=True,
                task_type="planning_execution",
                feature=feature,
            )
        )
        assert error is None
        assert result is not None
        assert calls[-1]["response_format"] == {"type": "json_object"}
        assert calls[-1]["thinking"] == {"type": "disabled"}

    result, error = router.complete(
        _request(
            response_format_json=True,
            task_type="planning_execution",
            feature="cognitive_execution_blueprint",
        )
    )
    assert error is None
    assert result is not None
    assert "thinking" not in calls[-1]

    result, error = router.complete(
        _request(
            response_format_json=True,
            task_type="planning_strategy",
            feature="cognitive_execution_single_pass",
        )
    )
    assert error is None
    assert result is not None
    assert "thinking" not in calls[-1]


def test_kimi_temperature_policy_omits_k2_parameters_and_clamps_moonshot_models(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'

        def json(self):
            return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)

    for model in ("kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6", "kimi-k2.5"):
        result, error = ModelRouter(
            _settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model=model, api_key="moonshot-test-local")
        ).complete(_request(temperature=0, task_type="settings_test"))
        assert error is None
        assert result is not None
        assert "temperature" not in calls[-1]
        assert "top_p" not in calls[-1]
        assert "n" not in calls[-1]
        assert "presence_penalty" not in calls[-1]
        assert "frequency_penalty" not in calls[-1]

    result, error = ModelRouter(
        _settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="moonshot-v1-8k", api_key="moonshot-test-local")
    ).complete(_request(temperature=1.8, task_type="settings_test"))
    assert error is None
    assert result is not None
    assert calls[-1]["temperature"] == 1

    result, error = ModelRouter(
        _settings(provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-v4-flash", api_key="sk-test-local")
    ).complete(_request(temperature=1.8, task_type="settings_test"))
    assert error is None
    assert result is not None
    assert calls[-1]["temperature"] == 1.8


def test_invalid_json_missing_content_and_length_are_stable(monkeypatch):
    class LengthResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}

    class MissingContentResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}

    class BadJsonResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("bad json")

    responses = [LengthResponse(), MissingContentResponse(), BadJsonResponse()]

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            return responses.pop(0)

    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)
    router = ModelRouter(_settings())

    _, length_error = router.complete(_request())
    assert length_error and length_error.error_type == "model_output_truncated"

    _, content_error = router.complete(_request())
    assert content_error and content_error.error_type == "invalid_model_output"

    _, json_error = router.complete(_request())
    assert json_error and json_error.error_type == "invalid_model_output"


def test_transport_errors_map_to_standard_error_types(monkeypatch):
    errors = [httpx.TimeoutException("slow"), httpx.ConnectError("offline"), httpx.InvalidURL("bad")]

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            raise errors.pop(0)

    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)
    router = ModelRouter(_settings())

    _, timeout_error = router.complete(_request())
    assert timeout_error and timeout_error.error_type == "timeout"

    _, network_error = router.complete(_request())
    assert network_error and network_error.error_type == "network_error"

    _, url_error = router.complete(_request())
    assert url_error and url_error.error_type == "bad_base_url"


def test_llm_client_facade_keeps_legacy_result_shape_and_sanitized_ai_run(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}'

        def json(self):
            return {
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(
        llm_module,
        "get_effective_ai_settings",
        lambda: _settings(base_url="https://api.deepseek.com/v1?token=secret", api_key="sk-secret-token"),
    )
    monkeypatch.setattr(model_provider.httpx, "Client", FakeHttpClient)

    result, error = llm_module.LlmClient().complete("settings_test", "system", "hello", response_format_json=True, task_type="settings_test")

    assert error is None
    assert result is not None
    assert result.content == "OK"
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-flash"
    assert result.usage == {"promptTokens": 2, "completionTokens": 1, "totalTokens": 3}
    assert isinstance(result.latency_ms, int)
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT provider, model, input_summary, output_summary, error FROM ai_runs WHERE feature = 'settings_test'"
        ).fetchall()
    combined = "\n".join(" ".join(str(row[key] or "") for key in row.keys()) for row in rows)
    assert "sk-secret-token" not in combined
    assert "Bearer" not in combined
    assert "Authorization" not in combined
    assert "token=secret" not in combined


def test_model_router_falls_back_after_primary_error(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    def fake_complete(self, request):
        if self.settings.provider == "kimi":
            return (None, ModelCallError("slow", "timeout", provider="kimi", model=self.settings.model))
        return (
            ModelCallResult(
                text="OK",
                provider=self.settings.provider,
                model=self.settings.model,
                usage=ModelUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                latency_ms=12,
            ),
            None,
        )

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")).complete(
        _request(task_type="plan_generation")
    )

    assert error is None
    assert result is not None
    assert result.provider == "deepseek"
    assert result.fallback_used is True
    assert [attempt.status for attempt in result.attempts] == ["error", "success"]
    assert result.attempts[0].error_type == "timeout"


def test_model_router_primary_success_does_not_use_fallback(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append(self.settings.provider)
        return (
            ModelCallResult(
                text="OK",
                provider=self.settings.provider,
                model=self.settings.model,
                usage=ModelUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                latency_ms=12,
            ),
            None,
        )

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")).complete(
        _request(task_type="memory_query")
    )

    assert error is None
    assert result is not None
    assert result.provider == "kimi"
    assert result.fallback_used is False
    assert [attempt.status for attempt in result.attempts] == ["success"]
    assert calls == ["kimi"]


@pytest.mark.parametrize(
    ("task_type", "initial_budget", "hard_cap"),
    [
        ("planning_goal_model", 5400, 10800),
        ("planning_reality", 5400, 10800),
        ("planning_evidence", 6600, 13200),
        ("planning_strategy", 7200, 14400),
        ("planning_execution", 12000, 24000),
        ("planning_critique", 6600, 13200),
        ("planning_learning", 5400, 10800),
    ],
)
def test_planning_truncation_retries_same_provider_once_with_larger_budget(
    monkeypatch,
    task_type,
    initial_budget,
    hard_cap,
):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append((self.settings.provider, request.max_tokens, request.max_token_cap))
        if len(calls) == 1:
            return (
                None,
                ModelCallError(
                    "truncated",
                    "model_output_truncated",
                    detail="raw provider detail must not enter attempts",
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")).complete(
        _request(
            task_type=task_type,
            response_format_json=True,
            max_tokens=initial_budget,
            max_token_cap=hard_cap,
        )
    )

    assert error is None
    assert result is not None
    assert result.provider == "kimi"
    assert result.fallback_used is False
    assert calls == [("kimi", initial_budget, hard_cap), ("kimi", hard_cap, hard_cap)]
    assert [(attempt.status, attempt.error_type, attempt.automatic_retry) for attempt in result.attempts] == [
        ("error", "model_output_truncated", False),
        ("success", None, True),
    ]
    assert result.attempts[0].latency_ms is not None
    assert result.attempts[0].latency_ms >= 1
    assert result.attempts[1].to_dict()["automaticRetry"] is True
    assert "detail" not in result.attempts[0].to_dict()


def test_direct_planning_truncation_retries_same_provider_once(monkeypatch):
    calls = []

    def fake_complete(self, request):
        calls.append((self.settings.provider, request.max_tokens, request.max_token_cap))
        if len(calls) == 1:
            return (
                None,
                ModelCallError(
                    "truncated",
                    "model_output_truncated",
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (
            ModelCallResult(
                text="OK",
                provider=self.settings.provider,
                model=self.settings.model,
                latency_ms=4,
            ),
            None,
        )

    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(
        _settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code"),
        routing_enabled=False,
    ).complete(
        _request(
            task_type="planning_evidence",
            response_format_json=True,
            max_tokens=6600,
            max_token_cap=13200,
        )
    )

    assert error is None
    assert result is not None and result.provider == "kimi"
    assert calls == [("kimi", 6600, 13200), ("kimi", 13200, 13200)]
    assert [(attempt.status, attempt.automatic_retry) for attempt in result.attempts] == [
        ("error", False),
        ("success", True),
    ]
    assert result.attempts[0].latency_ms is not None
    assert result.attempts[0].latency_ms >= 1


def test_planning_stage_uses_only_one_automatic_retry_then_continues_fallback(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek", "zhipu_glm"), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append((self.settings.provider, request.max_tokens))
        if self.settings.provider != "zhipu_glm":
            return (
                None,
                ModelCallError(
                    "truncated",
                    "model_output_truncated",
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")).complete(
        _request(
            task_type="planning_evidence",
            response_format_json=True,
            max_tokens=6600,
            max_token_cap=13200,
        )
    )

    assert error is None
    assert result is not None
    assert result.provider == "zhipu_glm"
    assert result.fallback_used is True
    assert calls == [("kimi", 6600), ("kimi", 13200), ("deepseek", 6600), ("zhipu_glm", 6600)]
    assert [(attempt.provider, attempt.status, attempt.automatic_retry) for attempt in result.attempts] == [
        ("kimi", "error", False),
        ("kimi", "error", True),
        ("deepseek", "error", False),
        ("zhipu_glm", "success", False),
    ]


def test_truncated_non_planning_task_does_not_use_planning_retry_budget(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append((self.settings.provider, request.max_tokens))
        if self.settings.provider == "kimi":
            return (
                None,
                ModelCallError(
                    "truncated",
                    "model_output_truncated",
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")).complete(
        _request(
            task_type="memory_query",
            response_format_json=True,
            max_tokens=2200,
            max_token_cap=4400,
        )
    )

    assert error is None
    assert result is not None and result.provider == "deepseek"
    assert calls == [("kimi", 2200), ("deepseek", 2200)]
    assert not any(attempt.automatic_retry for attempt in result.attempts)


@pytest.mark.parametrize(
    ("response_format_json", "error_type"),
    [(False, "model_output_truncated"), (True, "timeout")],
)
def test_planning_retry_requires_json_truncation(monkeypatch, response_format_json, error_type):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append((self.settings.provider, request.max_tokens))
        if self.settings.provider == "kimi":
            return (
                None,
                ModelCallError(
                    "safe error",
                    error_type,
                    provider=self.settings.provider,
                    model=self.settings.model,
                ),
            )
        return (
            ModelCallResult(
                text="OK",
                provider=self.settings.provider,
                model=self.settings.model,
                latency_ms=4,
            ),
            None,
        )

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(
        _settings(provider="kimi", base_url="https://api.moonshot.ai/v1", model="kimi-k2.7-code")
    ).complete(
        _request(
            task_type="planning_evidence",
            response_format_json=response_format_json,
            max_tokens=6600,
            max_token_cap=13200,
        )
    )

    assert error is None
    assert result is not None and result.provider == "deepseek"
    assert calls == [("kimi", 6600), ("deepseek", 6600)]
    assert not any(attempt.automatic_retry for attempt in result.attempts)


def test_model_router_skips_missing_key_and_records_attempt(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key="" if provider == "kimi" else "deepseek-key",
        )

    def fake_complete(self, request):
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=3), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")).complete(
        _request(task_type="chat")
    )

    assert error is None
    assert result is not None
    assert result.provider == "deepseek"
    assert result.attempts[0].status == "skipped"
    assert result.attempts[0].error_type == "missing_api_key"


def test_model_router_auto_primary_resolves_to_active_provider(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "auto", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    calls = []

    def fake_complete(self, request):
        calls.append(self.settings.provider)
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="zhipu_glm", model="glm-4-flash", api_key="active-key")).complete(
        _request(task_type="command_decision")
    )

    assert error is None
    assert result is not None
    assert result.provider == "zhipu_glm"
    assert result.fallback_used is False
    assert [attempt.provider for attempt in result.attempts] == ["zhipu_glm"]
    assert calls == ["zhipu_glm"]


def test_model_router_auto_primary_missing_key_falls_back(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "auto", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key="" if provider == "zhipu_glm" else "deepseek-key",
        )

    def fake_complete(self, request):
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="zhipu_glm", model="glm-4-flash", api_key="")).complete(
        _request(task_type="command_decision")
    )

    assert error is None
    assert result is not None
    assert result.provider == "deepseek"
    assert result.fallback_used is True
    assert [(attempt.provider, attempt.status, attempt.error_type) for attempt in result.attempts] == [
        ("zhipu_glm", "skipped", "missing_api_key"),
        ("deepseek", "success", None),
    ]


def test_model_router_auto_policy_selects_by_task_strategy(monkeypatch):
    with get_conn() as conn:
        for provider in ("zhipu_glm", "deepseek", "kimi"):
            conn.execute(
                """
                INSERT INTO ai_provider_configs(
                  provider, base_url, model, api_key_encrypted, api_key_source, updated_at
                )
                VALUES (?, ?, ?, ?, 'user', CURRENT_TIMESTAMP)
                ON CONFLICT(provider)
                DO UPDATE SET
                  base_url = excluded.base_url,
                  model = excluded.model,
                  api_key_encrypted = excluded.api_key_encrypted,
                  api_key_source = excluded.api_key_source,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (provider, provider_default_base_url(provider), provider_default_model(provider), f"{provider}-key"),
            )

    calls = []

    def fake_complete(self, request):
        calls.append((request.task_type, self.settings.provider))
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    decision_result, decision_error = ModelRouter(_settings(provider="deepseek")).complete(_request(task_type="command_decision"))
    plan_result, plan_error = ModelRouter(_settings(provider="deepseek")).complete(_request(task_type="plan_generation"))

    assert decision_error is None
    assert plan_error is None
    assert decision_result is not None and decision_result.provider == "zhipu_glm"
    assert plan_result is not None and plan_result.provider == "deepseek"
    assert calls == [("command_decision", "zhipu_glm"), ("plan_generation", "deepseek")]


def test_model_router_auto_policy_user_order_can_change_balanced_choice(monkeypatch):
    with get_conn() as conn:
        for provider in ("kimi", "deepseek"):
            conn.execute(
                """
                INSERT INTO ai_provider_configs(
                  provider, base_url, model, api_key_encrypted, api_key_source, updated_at
                )
                VALUES (?, ?, ?, ?, 'user', CURRENT_TIMESTAMP)
                ON CONFLICT(provider)
                DO UPDATE SET
                  base_url = excluded.base_url,
                  model = excluded.model,
                  api_key_encrypted = excluded.api_key_encrypted,
                  api_key_source = excluded.api_key_source,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (provider, provider_default_base_url(provider), provider_default_model(provider), f"{provider}-key"),
            )
        conn.execute(
            """
            INSERT INTO user_preferences(key, value, updated_at)
            VALUES ('ai.autoModelPolicy', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (
                json.dumps(
                    {
                        "autoProviderOrder": ["kimi", "deepseek", "zhipu_glm", "openai", "custom"],
                        "taskStrategy": {"chat": "balanced"},
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO ai_model_routing_rules(
              task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
            )
            VALUES ('chat', 'auto', '["deepseek"]', 1, CURRENT_TIMESTAMP)
            ON CONFLICT(task_type)
            DO UPDATE SET
              primary_provider = excluded.primary_provider,
              fallback_providers_json = excluded.fallback_providers_json,
              local_fallback_enabled = excluded.local_fallback_enabled,
              updated_at = CURRENT_TIMESTAMP
            """
        )

    def fake_complete(self, request):
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=4), None)

    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="deepseek")).complete(_request(task_type="chat"))

    assert error is None
    assert result is not None
    assert result.provider == "kimi"


def test_model_router_returns_error_with_local_fallback_allowed_when_all_fail(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), True)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    def fake_complete(self, request):
        return (None, ModelCallError("nope", "bad_model", provider=self.settings.provider, model=self.settings.model))

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")).complete(
        _request(task_type="plan_generation")
    )

    assert result is None
    assert error is not None
    assert error.error_type == "bad_model"
    assert error.local_fallback_allowed is True
    assert error.fallback_used is True
    assert [attempt.status for attempt in error.attempts] == ["error", "error"]


def test_model_router_primary_only_failure_is_not_reported_as_fallback(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", (), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    def fake_complete(self, request):
        return (None, ModelCallError("nope", "timeout", provider=self.settings.provider, model=self.settings.model))

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(
        _settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")
    ).complete(_request(task_type="goal_understanding"))

    assert result is None
    assert error is not None
    assert error.fallback_used is False
    assert [(attempt.provider, attempt.status) for attempt in error.attempts] == [("kimi", "error")]


def test_model_router_returns_final_error_when_local_fallback_disabled(monkeypatch):
    def fake_rule(task_type, active_provider):
        return ModelRoutingRuleConfig(task_type, "kimi", ("deepseek",), False)

    def fake_settings(provider, active_settings=None):
        return _settings(
            provider=provider,
            base_url=provider_default_base_url(provider),
            model=provider_default_model(provider),
            api_key=f"{provider}-key",
        )

    def fake_complete(self, request):
        return (None, ModelCallError("nope", "network_error", provider=self.settings.provider, model=self.settings.model))

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fake_rule)
    monkeypatch.setattr(model_provider, "get_effective_ai_settings_for_provider", fake_settings)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")).complete(
        _request(task_type="plan_generation")
    )

    assert result is None
    assert error is not None
    assert error.error_type == "network_error"
    assert error.local_fallback_allowed is False
    assert error.fallback_used is True


def test_model_router_settings_test_bypasses_routing(monkeypatch):
    def fail_rule(*args, **kwargs):
        raise AssertionError("settings_test must not use routing rules")

    def fake_complete(self, request):
        return (ModelCallResult(text="OK", provider=self.settings.provider, model=self.settings.model, latency_ms=1), None)

    monkeypatch.setattr(model_provider, "get_model_routing_rule", fail_rule)
    monkeypatch.setattr(model_provider.OpenAICompatibleProvider, "complete", fake_complete)

    result, error = ModelRouter(_settings(provider="kimi", base_url="https://api.moonshot.cn/v1", model="kimi-k2.7-code")).complete(_request())

    assert error is None
    assert result is not None
    assert result.provider == "kimi"
    assert result.attempts == ()
