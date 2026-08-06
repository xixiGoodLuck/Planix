import json

import pytest

from app.db import get_conn
from app.schemas import AiSettingsUpdate, CommandChatRequest
from app.services.ai_settings import EffectiveAiSettings, ModelRoutingRuleConfig
from app.services.command_agent import CommandAgentService
from app.services.llm import LlmError, LlmResult
from app.services.model_provider import ModelCallError, ModelCallRequest, ModelCallResult, ModelRouter


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_local_settings_normalize_openai_compatible_v1_once():
    without_v1 = AiSettingsUpdate(provider="local", baseUrl="http://127.0.0.1:1234", model="qwen")
    with_v1 = AiSettingsUpdate(provider="local", baseUrl="http://127.0.0.1:1234/v1", model="qwen")

    assert without_v1.base_url == "http://127.0.0.1:1234/v1"
    assert with_v1.base_url == "http://127.0.0.1:1234/v1"


def test_local_provider_calls_openai_compatible_endpoint_without_optional_key(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return Response()

    monkeypatch.setattr("app.services.model_provider.httpx.Client", Client)
    settings = EffectiveAiSettings("local", "http://127.0.0.1:1234/v1", "qwen", "", 0.3, 30, "")
    result, error = ModelRouter(settings, routing_enabled=False, track_credentials=False).complete(
        ModelCallRequest("chat", "test", "system", "你好")
    )

    assert error is None
    assert result and result.text == "你好"
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert "Authorization" not in captured["headers"]


def test_local_settings_save_and_test_model_reuse_existing_endpoints(client, monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append((url, headers))
            return Response()

    monkeypatch.setattr("app.services.model_provider.httpx.Client", Client)
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "local",
            "baseUrl": "http://127.0.0.1:1234",
            "model": "qwen",
            "temperature": 0.3,
            "timeoutSeconds": 30,
        },
    )
    tested = client.post("/api/ai/test", json={"prompt": "Say OK"})

    assert saved.status_code == 200
    assert saved.json()["baseUrl"] == "http://127.0.0.1:1234/v1"
    assert saved.json()["hasApiKey"] is False
    assert any(item["provider"] == "local" for item in saved.json()["savedProviders"])
    assert tested.status_code == 200 and tested.json()["ok"] is True
    assert [url for url, _headers in calls] == ["http://127.0.0.1:1234/v1/chat/completions"]
    assert all("Authorization" not in headers for _url, headers in calls)


def test_local_route_failure_does_not_fall_back_to_cloud(monkeypatch):
    local = EffectiveAiSettings("local", "http://127.0.0.1:1234/v1", "qwen", "", 0.3, 30, "")
    cloud = EffectiveAiSettings("deepseek", "https://api.deepseek.com", "deepseek-chat", "sk-valid-test-key", 0.3, 30, "")
    calls = []
    monkeypatch.setattr(
        "app.services.model_provider.get_model_routing_rule",
        lambda *_: ModelRoutingRuleConfig("chat", "local", ("deepseek",), False),
    )
    monkeypatch.setattr(
        "app.services.model_provider.get_effective_ai_settings_for_provider",
        lambda provider, *_: local if provider == "local" else cloud,
    )

    def complete_direct(self, request, settings=None):
        calls.append(settings.provider)
        if settings.provider == "local":
            return None, ModelCallError("local offline", "network_error", provider="local", model="qwen")
        return ModelCallResult("cloud reply", "deepseek", "deepseek-chat"), None

    monkeypatch.setattr(ModelRouter, "_complete_direct", complete_direct)
    result, error = ModelRouter(local).complete(ModelCallRequest("chat", "test", "system", "hello"))

    assert result is None
    assert error and error.provider == "local"
    assert calls == ["local"]


def test_pure_chat_calls_model_once_and_skips_command_persistence(client, monkeypatch):
    calls = []

    def complete(self, *args, **kwargs):
        calls.append((args, kwargs))
        return LlmResult("你好，我是 Planix。", "deepseek", "deepseek-chat"), None

    monkeypatch.setattr("app.services.command_agent.LlmClient.complete", complete)
    monkeypatch.setattr(CommandAgentService, "ensure_thread", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("thread persistence ran")))
    monkeypatch.setattr(CommandAgentService, "add_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("message persistence ran")))
    monkeypatch.setattr(CommandAgentService, "_resolve_auto_decision", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning router ran")))
    with get_conn() as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in ("command_threads", "command_messages", "command_drafts", "ai_runs", "memories", "plans")
        }

    response = client.post(
        "/api/command/chat",
        json={"mode": "chat", "message": "你好", "history": [{"role": "user", "content": "上一句"}]},
    )
    events = _events(response)
    with get_conn() as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in before
        }

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1]["record_run"] is False
    assert calls[0][1]["routing_enabled"] is False
    assert "上一句" in calls[0][0][2]
    assert [event["type"] for event in events] == ["thread", "assistant_delta", "done"]
    assert events[1]["text"] == "你好，我是 Planix。"
    assert after == before


def test_pure_chat_surfaces_readable_model_service_error(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.command_agent.LlmClient.complete",
        lambda *args, **kwargs: (
            None,
            LlmError(
                "The model service cannot be reached. Check the network or service availability.",
                "network_error",
                detail="[WinError 10061] Connection refused",
            ),
        ),
    )

    events = _events(client.post("/api/command/chat", json={"mode": "chat", "message": "你好"}))

    assert events[-1]["type"] == "error"
    assert "model service cannot be reached" in events[-1]["error"]
    assert "Connection refused" in events[-1]["error"]


@pytest.mark.parametrize(
    ("provider", "base_url", "api_key"),
    [
        ("deepseek", "https://api.deepseek.com", "sk-valid-cloud-key"),
        ("local", "http://127.0.0.1:1234/v1", ""),
    ],
)
def test_pure_chat_uses_selected_cloud_or_local_provider_once(client, monkeypatch, provider, base_url, api_key):
    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": f"{provider} reply"}, "finish_reason": "stop"}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            calls.append(url)
            return Response()

    settings = EffectiveAiSettings(provider, base_url, "test-model", api_key, 0.3, 30, "")
    monkeypatch.setattr("app.services.llm.get_effective_ai_settings", lambda: settings)
    monkeypatch.setattr("app.services.model_provider.httpx.Client", Client)

    response = client.post("/api/command/chat", json={"mode": "chat", "message": "你好"})
    events = _events(response)

    assert calls == [
        "https://api.deepseek.com/chat/completions"
        if provider == "deepseek"
        else "http://127.0.0.1:1234/v1/chat/completions"
    ]
    assert next(event["text"] for event in events if event["type"] == "assistant_delta") == f"{provider} reply"


def test_deep_planning_mode_still_uses_existing_agent_path(monkeypatch):
    service = CommandAgentService()
    called = []
    monkeypatch.setattr(service, "_stream_chat_with_model", lambda payload: iter((called.append(payload.mode) or "agent-event",)))

    assert list(service.stream_chat(CommandChatRequest(mode="auto", message="开始深度规划"))) == ["agent-event"]
    assert called == ["auto"]
