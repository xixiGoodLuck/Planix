from contextlib import contextmanager

from app.db import get_conn
from app.schemas import normalize_local_base_url
from app.services.ai_settings import ROUTABLE_TASK_TYPES, get_effective_ai_settings
from app.services.model_provider import ModelCallError, ModelCallResult, ModelRouter, OpenAICompatibleProvider
from app.services.secret_store import get_secret_store, provider_secret_key


EXPECTED_TASKS = [
    "planning_understanding",
    "planning_plan",
    "planning_review",
    "planning_learning",
]


def test_public_settings_returns_exact_v2_routes(client):
    response = client.get("/api/ai/settings")
    assert response.status_code == 200
    assert [rule["taskType"] for rule in response.json()["routingRules"]] == EXPECTED_TASKS
    assert list(ROUTABLE_TASK_TYPES) == EXPECTED_TASKS


def test_v2_routing_save_is_idempotent(client):
    rules = client.get("/api/ai/settings").json()["routingRules"]
    rules[1]["primaryProvider"] = "auto"
    rules[1]["fallbackProviders"] = ["deepseek"]
    first = client.put("/api/ai/settings/routing", json={"routingRules": rules})
    second = client.put("/api/ai/settings/routing", json={"routingRules": rules})
    assert first.status_code == second.status_code == 200
    assert [rule["taskType"] for rule in second.json()["routingRules"]] == EXPECTED_TASKS


def test_retired_routes_are_removed_without_touching_provider_config(client):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_provider_configs(provider, base_url, model, updated_at)
               VALUES ('custom', 'http://example.invalid/v1', 'custom-model', CURRENT_TIMESTAMP)"""
        )
        before = dict(conn.execute("SELECT * FROM ai_provider_configs WHERE provider = 'custom'").fetchone())

    first = client.get("/api/ai/settings")
    second = client.get("/api/ai/settings")
    assert first.status_code == second.status_code == 200
    with get_conn() as conn:
        after = dict(conn.execute("SELECT * FROM ai_provider_configs WHERE provider = 'custom'").fetchone())
        stored = [row["task_type"] for row in conn.execute("SELECT task_type FROM ai_model_routing_rules ORDER BY task_type")]
    assert before == after
    assert stored == sorted(EXPECTED_TASKS)


def test_routing_rejects_retired_task(client):
    response = client.put(
        "/api/ai/settings/routing",
        json={"routingRules": [{"taskType": "chat", "primaryProvider": "auto", "fallbackProviders": [], "localFallbackEnabled": False}]},
    )
    assert response.status_code == 422


def test_local_base_url_normalizes_v1_once():
    assert normalize_local_base_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    assert normalize_local_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"


def _settings_payload(**overrides):
    payload = {
        "provider": "deepseek",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "apiKey": "PLANIX_SETTINGS_TEST_SECRET_938201",
        "temperature": 0.3,
        "timeoutSeconds": 40,
    }
    payload.update(overrides)
    return payload


def test_save_persists_secret_without_model_call_or_plaintext(client, monkeypatch):
    calls = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("saving settings must not call a model")

    monkeypatch.setattr(ModelRouter, "complete", fail_if_called)
    secret = _settings_payload()["apiKey"]
    response = client.put("/api/ai/settings", json=_settings_payload())
    assert response.status_code == 200
    assert calls == 0
    assert response.json()["hasApiKey"] is True
    assert response.json()["keyStatus"] == "unchecked"
    assert get_secret_store().get(provider_secret_key("deepseek")) == secret
    with get_conn() as conn:
        rows = [dict(row) for table in ("ai_settings", "ai_provider_configs") for row in conn.execute(f"SELECT * FROM {table}")]
    assert secret not in repr(rows)

    deleted = client.delete("/api/ai/settings/key/deepseek")
    assert deleted.status_code == 200
    assert not get_secret_store().get(provider_secret_key("deepseek"))
    with get_conn() as conn:
        rows = [dict(row) for table in ("ai_settings", "ai_provider_configs") for row in conn.execute(f"SELECT * FROM {table}")]
    assert secret not in repr(rows)


def test_model_test_performs_one_provider_call_and_refreshes_key_status(client, monkeypatch):
    assert client.put("/api/ai/settings", json=_settings_payload()).status_code == 200
    calls = 0

    def complete_once(self, request):
        nonlocal calls
        calls += 1
        return ModelCallResult(text="OK", provider="deepseek", model="deepseek-chat"), None

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", complete_once)
    tested = client.post("/api/ai/test", json={"prompt": "Say OK."})
    assert tested.status_code == 200
    assert tested.json()["ok"] is True
    assert calls == 1
    assert client.get("/api/ai/settings").json()["keyStatus"] == "valid"


def test_provider_network_failure_does_not_prevent_save_or_mark_key_invalid(client, monkeypatch):
    calls = 0

    def network_failure(self, request):
        nonlocal calls
        calls += 1
        return None, ModelCallError("unreachable", "network_error", provider="deepseek", model="deepseek-chat")

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", network_failure)
    saved = client.put("/api/ai/settings", json=_settings_payload(baseUrl="https://unreachable.example/v1"))
    assert saved.status_code == 200
    assert calls == 0
    tested = client.post("/api/ai/test", json={"prompt": "Say OK."})
    assert tested.status_code == 200
    assert tested.json()["ok"] is False
    assert tested.json()["errorType"] == "network_error"
    assert calls == 1
    assert client.get("/api/ai/settings").json()["keyStatus"] == "unchecked"


def test_invalid_key_format_is_rejected_before_secret_persistence(client):
    response = client.put("/api/ai/settings", json=_settings_payload(apiKey="bad\nheader"))
    assert response.status_code == 422
    assert not get_secret_store().get(provider_secret_key("deepseek"))


def test_saved_provider_rows_mean_saved_cloud_key_or_configured_local_model(client):
    no_key = _settings_payload(apiKey="")
    assert client.put("/api/ai/settings", json=no_key).status_code == 200
    assert client.get("/api/ai/settings").json()["savedProviders"] == []

    local = _settings_payload(
        provider="local",
        baseUrl="http://127.0.0.1:1234",
        model="qwen3",
        apiKey="",
    )
    saved = client.put("/api/ai/settings", json=local)
    assert saved.status_code == 200
    assert saved.json()["savedProviders"] == [
        {
            "provider": "local",
            "baseUrl": "http://127.0.0.1:1234/v1",
            "model": "qwen3",
            "hasApiKey": False,
            "keyStatus": "unchecked",
            "keyErrorType": "",
            "lastValidatedAt": "",
            "updatedAt": saved.json()["savedProviders"][0]["updatedAt"],
        }
    ]


def test_force_non_thinking_can_be_enabled_and_disabled_globally(client):
    initial = client.get("/api/ai/settings")
    assert initial.status_code == 200
    assert initial.json()["forceNonThinking"] is False

    enabled = client.put(
        "/api/ai/settings",
        json=_settings_payload(forceNonThinking=True),
    )
    assert enabled.status_code == 200
    assert enabled.json()["forceNonThinking"] is True
    assert get_effective_ai_settings().force_non_thinking is True

    disabled = client.put(
        "/api/ai/settings",
        json=_settings_payload(forceNonThinking=False),
    )
    assert disabled.status_code == 200
    assert disabled.json()["forceNonThinking"] is False
    assert get_effective_ai_settings().force_non_thinking is False


def test_changed_credentials_reset_validation_but_unchanged_settings_preserve_it(client, monkeypatch):
    assert client.put("/api/ai/settings", json=_settings_payload()).status_code == 200
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "complete",
        lambda self, request: (ModelCallResult(text="OK", provider="deepseek", model="deepseek-chat"), None),
    )
    assert client.post("/api/ai/test", json={"prompt": "Say OK."}).json()["ok"] is True
    unchanged = client.put("/api/ai/settings", json=_settings_payload())
    assert unchanged.json()["keyStatus"] == "valid"
    changed = client.put("/api/ai/settings", json=_settings_payload(model="deepseek-reasoner"))
    assert changed.json()["keyStatus"] == "unchecked"
    assert changed.json()["keyErrorType"] == ""
    with get_conn() as conn:
        assert conn.execute(
            "SELECT last_validated_at FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone()["last_validated_at"] is None


def test_save_restores_secret_when_transaction_exit_fails(client, monkeypatch):
    old_secret = "PLANIX_OLD_SECRET_938201"
    assert client.put("/api/ai/settings", json=_settings_payload(apiKey=old_secret)).status_code == 200
    from app.services import ai_settings as service

    real_get_conn = service.get_conn

    @contextmanager
    def failing_transaction():
        with real_get_conn() as conn:
            yield conn
            raise RuntimeError("injected commit failure")

    monkeypatch.setattr(service, "get_conn", failing_transaction)
    response = client.put(
        "/api/ai/settings",
        json=_settings_payload(apiKey="PLANIX_NEW_SECRET_938201", model="deepseek-reasoner"),
    )
    assert response.status_code == 500
    assert get_secret_store().get(provider_secret_key("deepseek")) == old_secret
    with real_get_conn() as conn:
        assert conn.execute(
            "SELECT model FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone()["model"] == "deepseek-chat"


def test_delete_restores_secret_when_database_update_fails(client, monkeypatch):
    secret = _settings_payload()["apiKey"]
    assert client.put("/api/ai/settings", json=_settings_payload()).status_code == 200

    def fail_after_secret_delete(*_args, **_kwargs):
        raise RuntimeError("injected database failure")

    monkeypatch.setattr("app.services.ai_settings._config_for_provider", fail_after_secret_delete)
    response = client.delete("/api/ai/settings/key/deepseek")
    assert response.status_code == 500
    assert get_secret_store().get(provider_secret_key("deepseek")) == secret
    with get_conn() as conn:
        assert conn.execute(
            "SELECT api_key_source FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone()["api_key_source"] == "secret_store"
