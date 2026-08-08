import pytest

from app.db import get_conn
from app.services import ai_settings as ai_settings_module
from app.services.ai_settings import EffectiveAiSettings, PendingProviderConfig, _validate_provider_config as real_validate_provider_config
from app.services import llm as llm_module
from app.services.llm import _chat_completions_url
from app.services.llm import _classify_http_error
from app.services.llm import _effective_max_tokens, _message_content


def _allow_settings_validation(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def allow_settings_validation(monkeypatch):
    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", _allow_settings_validation)


def test_ai_settings_endpoint_returns_json(client):
    response = client.get("/api/ai/settings")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["provider"] == "deepseek"
    assert body["baseUrl"] == "https://api.deepseek.com"
    assert body["routingRules"]
    task_types = {rule["taskType"] for rule in body["routingRules"]}
    assert task_types >= {"command_decision", "plan_generation", "chat"}
    assert {"planning_understanding", "planning_plan", "planning_review", "planning_learning"} <= task_types
    assert not task_types.intersection({
        "goal_understanding",
        "planning_goal_model",
        "planning_reality",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
    })
    assert {"memory_query", "memory_write"} <= {rule["taskType"] for rule in body["routingRules"]}
    assert "note_query" not in {rule["taskType"] for rule in body["routingRules"]}
    assert "note_write" not in {rule["taskType"] for rule in body["routingRules"]}
    assert all(rule["primaryProvider"] == "auto" for rule in body["routingRules"])
    assert all(rule["fallbackProviders"] == ["deepseek"] for rule in body["routingRules"])
    assert body["autoModelPolicy"]["autoProviderOrder"][:3] == ["deepseek", "zhipu_glm", "kimi"]
    assert body["autoModelPolicy"]["taskStrategy"]["command_decision"] == "fast_low_cost"
    assert body["autoModelPolicy"]["taskStrategy"]["plan_generation"] == "structured_stable"


def test_ai_settings_are_saved_without_exposing_key(client):
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["provider"] == "deepseek"
    assert body["hasApiKey"] is True
    assert "apiKey" not in body

    loaded = client.get("/api/ai/settings")
    assert loaded.status_code == 200
    assert loaded.json()["hasApiKey"] is True
    assert "apiKey" not in loaded.json()


def test_kimi_and_zhipu_settings_are_saved_and_loaded(client):
    kimi = client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "kimi-k2.7-code",
            "apiKey": "moonshot-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert kimi.status_code == 200
    assert kimi.json()["provider"] == "kimi"
    assert kimi.json()["baseUrl"] == "https://api.moonshot.ai/v1"
    assert kimi.json()["hasApiKey"] is True

    zhipu = client.put(
        "/api/ai/settings",
        json={
            "provider": "zhipu_glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
            "apiKey": "zhipu-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert zhipu.status_code == 200
    loaded = client.get("/api/ai/settings")
    assert loaded.json()["provider"] == "zhipu_glm"
    assert loaded.json()["baseUrl"] == "https://open.bigmodel.cn/api/paas/v4"
    assert loaded.json()["hasApiKey"] is True
    saved_providers = loaded.json()["savedProviders"]
    assert {item["provider"] for item in saved_providers if item["hasApiKey"]} == {"kimi", "zhipu_glm"}


def test_model_routing_rules_save_without_key_validation(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("routing save must not validate provider keys")

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", fail_if_called)
    loaded = client.get("/api/ai/settings").json()
    rules = loaded["routingRules"]
    next_rules = []
    for rule in rules:
        next_rules.append(
            {
                **rule,
                "primaryProvider": "kimi" if rule["taskType"] == "plan_generation" else rule["primaryProvider"],
                "fallbackProviders": ["deepseek"] if rule["taskType"] == "plan_generation" else rule["fallbackProviders"],
                "localFallbackEnabled": rule["taskType"] != "chat",
            }
        )

    saved = client.put("/api/ai/settings/routing", json={"routingRules": next_rules})

    assert saved.status_code == 200
    saved_rules = {rule["taskType"]: rule for rule in saved.json()["routingRules"]}
    assert saved_rules["plan_generation"]["primaryProvider"] == "kimi"
    assert saved_rules["plan_generation"]["fallbackProviders"] == ["deepseek"]
    assert saved_rules["chat"]["localFallbackEnabled"] is False
    assert all(
        rule["localFallbackEnabled"] is False
        for task_type, rule in saved_rules.items()
        if task_type.startswith("planning_")
    )


def test_model_routing_legacy_note_rules_are_normalized_to_memory_tasks(client):
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_model_routing_rules")
        conn.execute(
            """
            INSERT INTO ai_model_routing_rules(
              task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
            )
            VALUES ('note_query', 'kimi', '["deepseek"]', 1, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            """
            INSERT INTO ai_model_routing_rules(
              task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
            )
            VALUES ('note_write', 'zhipu_glm', '["deepseek"]', 0, CURRENT_TIMESTAMP)
            """
        )

    loaded = client.get("/api/ai/settings")

    assert loaded.status_code == 200
    rules = {rule["taskType"]: rule for rule in loaded.json()["routingRules"]}
    assert "note_query" not in rules
    assert "note_write" not in rules
    assert rules["memory_query"]["primaryProvider"] == "kimi"
    assert rules["memory_query"]["fallbackProviders"] == ["deepseek"]
    assert rules["memory_write"]["primaryProvider"] == "zhipu_glm"
    assert rules["memory_write"]["localFallbackEnabled"] is False

    saved = client.put(
        "/api/ai/settings/routing",
        json={
            "routingRules": [
                {
                    "taskType": "note_query",
                    "primaryProvider": "openai",
                    "fallbackProviders": ["deepseek"],
                    "localFallbackEnabled": True,
                }
            ]
        },
    )
    assert saved.status_code == 200
    saved_rules = {rule["taskType"]: rule for rule in saved.json()["routingRules"]}
    assert "note_query" not in saved_rules
    assert saved_rules["memory_query"]["primaryProvider"] == "openai"


def test_retired_planning_routing_rows_migrate_idempotently_without_touching_provider_config(client):
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "apiKey": "sk-routing-migration-test",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert saved.status_code == 200
    with get_conn() as conn:
        provider_before = dict(conn.execute(
            "SELECT * FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone())
        conn.execute("DELETE FROM ai_model_routing_rules")
        rows = [
            ("goal_understanding", "kimi"),
            ("planning_goal_model", "openai"),
            ("planning_reality", "kimi"),
            ("planning_evidence", "kimi"),
            ("planning_strategy", "kimi"),
            ("planning_execution", "zhipu_glm"),
            ("planning_critique", "custom"),
        ]
        conn.executemany(
            """
            INSERT INTO ai_model_routing_rules(
              task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at
            ) VALUES (?, ?, '["deepseek"]', 1, CURRENT_TIMESTAMP)
            """,
            rows,
        )

    first = client.get("/api/ai/settings")
    second = client.get("/api/ai/settings")
    assert first.status_code == second.status_code == 200
    first_rules = {rule["taskType"]: rule for rule in first.json()["routingRules"]}
    second_rules = {rule["taskType"]: rule for rule in second.json()["routingRules"]}
    assert first_rules == second_rules
    assert first_rules["planning_understanding"]["primaryProvider"] == "openai"
    assert first_rules["planning_plan"]["primaryProvider"] == "zhipu_glm"
    assert first_rules["planning_review"]["primaryProvider"] == "custom"
    assert all(not rule["localFallbackEnabled"] for task, rule in first_rules.items() if task.startswith("planning_"))
    with get_conn() as conn:
        remaining = {row["task_type"] for row in conn.execute("SELECT task_type FROM ai_model_routing_rules")}
        provider_after = dict(conn.execute(
            "SELECT * FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone())
    assert not remaining.intersection({
        "goal_understanding",
        "planning_goal_model",
        "planning_reality",
        "planning_evidence",
        "planning_strategy",
        "planning_execution",
        "planning_critique",
    })
    assert provider_after == provider_before


def test_model_routing_accepts_auto_primary_provider(client):
    loaded = client.get("/api/ai/settings").json()
    rule = next(rule for rule in loaded["routingRules"] if rule["taskType"] == "plan_generation")

    saved = client.put(
        "/api/ai/settings/routing",
        json={
            "routingRules": [
                {
                    **rule,
                    "primaryProvider": "auto",
                    "fallbackProviders": ["deepseek"],
                    "localFallbackEnabled": True,
                }
            ]
        },
    )

    assert saved.status_code == 200
    saved_rules = {rule["taskType"]: rule for rule in saved.json()["routingRules"]}
    assert saved_rules["plan_generation"]["primaryProvider"] == "auto"
    assert saved_rules["plan_generation"]["fallbackProviders"] == ["deepseek"]


def test_model_routing_saves_auto_model_policy(client):
    loaded = client.get("/api/ai/settings").json()

    saved = client.put(
        "/api/ai/settings/routing",
        json={
            "routingRules": loaded["routingRules"],
            "autoModelPolicy": {
                "autoProviderOrder": ["kimi", "deepseek", "zhipu_glm", "mock", "kimi"],
                "taskStrategy": {
                    "chat": "balanced",
                    "plan_generation": "structured_stable",
                },
            },
        },
    )

    assert saved.status_code == 200
    policy = saved.json()["autoModelPolicy"]
    assert policy["autoProviderOrder"][:3] == ["kimi", "deepseek", "zhipu_glm"]
    assert "mock" not in policy["autoProviderOrder"]
    assert policy["taskStrategy"]["chat"] == "balanced"
    assert policy["taskStrategy"]["command_decision"] == "fast_low_cost"


def test_model_routing_rejects_mock_and_too_many_fallbacks(client):
    loaded = client.get("/api/ai/settings").json()
    rule = loaded["routingRules"][0]

    bad_primary = client.put(
        "/api/ai/settings/routing",
        json={"routingRules": [{**rule, "primaryProvider": "mock"}]},
    )
    assert bad_primary.status_code == 422

    bad_fallbacks = client.put(
        "/api/ai/settings/routing",
        json={
            "routingRules": [
                {
                    **rule,
                    "fallbackProviders": ["kimi", "zhipu_glm", "openai"],
                }
            ]
        },
    )
    assert bad_fallbacks.status_code == 422


def test_save_validates_provider_key_before_writing(client, monkeypatch):
    def fail_validation(candidate, *, temperature, timeout_seconds):
        raise ai_settings_module.AiSettingsSaveValidationError(
            message="API key is invalid or expired.",
            error_type="auth_error",
            provider=candidate.provider,
            model=candidate.model,
            status_code=401,
        )

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", fail_validation)
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-wrong-provider",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )

    assert saved.status_code == 400
    detail = saved.json()["detail"]
    assert detail["errorType"] == "auth_error"
    assert detail["provider"] == "deepseek"
    assert "sk-wrong-provider" not in str(detail)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_provider_configs WHERE provider = 'deepseek'").fetchone()
    assert row is None


def test_save_validation_failure_does_not_overwrite_existing_provider_config(client, monkeypatch):
    first = client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "kimi-k2.7-code",
            "apiKey": "moonshot-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert first.status_code == 200

    def fail_validation(candidate, *, temperature, timeout_seconds):
        raise ai_settings_module.AiSettingsSaveValidationError(
            message="Model name does not exist.",
            error_type="bad_model",
            provider=candidate.provider,
            model=candidate.model,
            status_code=404,
        )

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", fail_validation)
    changed = client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "bad-model",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )

    assert changed.status_code == 400
    loaded = client.get("/api/ai/settings").json()
    assert loaded["provider"] == "kimi"
    assert loaded["model"] == "kimi-k2.7-code"
    assert loaded["hasApiKey"] is True
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_provider_configs WHERE provider = 'kimi'").fetchone()
    assert row["model"] == "kimi-k2.7-code"
    assert row["api_key_encrypted"] == "moonshot-test-local"


def test_successful_save_validation_writes_provider_config(client, monkeypatch):
    calls = []

    def pass_validation(candidate, *, temperature, timeout_seconds):
        calls.append((candidate.provider, candidate.model, temperature, timeout_seconds))

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", pass_validation)
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "zhipu_glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
            "apiKey": "zhipu-test-local",
            "temperature": 0.4,
            "timeoutSeconds": 35,
        },
    )

    assert saved.status_code == 200
    assert calls == [("zhipu_glm", "glm-4-flash", 0.4, 35)]
    assert saved.json()["hasApiKey"] is True


def test_kimi_save_validation_does_not_force_temperature_zero(monkeypatch):
    requests = []

    class FakeRouter:
        def __init__(self, settings, *, routing_enabled=True):
            self.settings = settings
            self.routing_enabled = routing_enabled

        def complete(self, request):
            requests.append(request)

            class Result:
                mode = "llm"

            return Result(), None

    monkeypatch.setattr("app.services.model_provider.ModelRouter", FakeRouter)
    real_validate_provider_config(
        PendingProviderConfig(
            provider="kimi",
            base_url="https://api.moonshot.ai/v1",
            model="kimi-k2.6",
            api_key="moonshot-test-local",
            api_key_source="user",
            should_validate=True,
        ),
        temperature=0.3,
        timeout_seconds=30,
    )

    assert requests
    assert requests[0].temperature is None


def test_provider_keys_are_preserved_per_provider_and_reused_on_switch(client):
    kimi = client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "kimi-k2.7-code",
            "apiKey": "moonshot-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    assert kimi.status_code == 200
    zhipu = client.put(
        "/api/ai/settings",
        json={
            "provider": "zhipu_glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
            "apiKey": "zhipu-test-local",
            "temperature": 0.4,
            "timeoutSeconds": 35,
        },
    )
    assert zhipu.status_code == 200

    switched = client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "kimi-k2.7-code",
            "temperature": 0.5,
            "timeoutSeconds": 40,
        },
    )
    assert switched.status_code == 200
    body = switched.json()
    assert body["provider"] == "kimi"
    assert body["hasApiKey"] is True
    assert {item["provider"] for item in body["savedProviders"] if item["hasApiKey"]} == {"kimi", "zhipu_glm"}


def test_delete_provider_key_does_not_delete_other_provider_keys(client):
    client.put(
        "/api/ai/settings",
        json={
            "provider": "kimi",
            "baseUrl": "https://api.moonshot.ai/v1",
            "model": "kimi-k2.7-code",
            "apiKey": "moonshot-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )
    client.put(
        "/api/ai/settings",
        json={
            "provider": "zhipu_glm",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-flash",
            "apiKey": "zhipu-test-local",
            "temperature": 0.2,
            "timeoutSeconds": 30,
        },
    )

    deleted = client.delete("/api/ai/settings/key/kimi")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["provider"] == "zhipu_glm"
    assert body["hasApiKey"] is True
    saved = {item["provider"]: item["hasApiKey"] for item in body["savedProviders"]}
    assert saved["kimi"] is False
    assert saved["zhipu_glm"] is True

    deleted_active = client.delete("/api/ai/settings/key/zhipu_glm")
    assert deleted_active.status_code == 200
    active = deleted_active.json()
    assert active["provider"] == "zhipu_glm"
    assert active["hasApiKey"] is False


def test_legacy_user_key_is_migrated_to_provider_config(client):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_settings(
              id, provider, base_url, model, api_key_encrypted, api_key_source,
              temperature, timeout_seconds, updated_at
            )
            VALUES (
              'local-default', 'deepseek', 'https://api.deepseek.com',
              'deepseek-v4-flash', 'sk-user-local', 'user', 0.3, 20, CURRENT_TIMESTAMP
            )
            """
        )

    loaded = client.get("/api/ai/settings")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["hasApiKey"] is True
    assert any(item["provider"] == "deepseek" and item["hasApiKey"] for item in body["savedProviders"])


def test_ai_settings_test_uses_mock_without_key(client):
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "mock",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["hasApiKey"] is False

    tested = client.post("/api/ai/test", json={"prompt": "ping"})
    assert tested.status_code == 200
    body = tested.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"


def test_mock_provider_save_does_not_validate_key(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("mock settings should not validate a provider key")

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", fail_if_called)
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "mock",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["hasApiKey"] is False


def test_blank_api_key_clears_saved_key(client):
    first = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-local",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert first.status_code == 200
    assert first.json()["hasApiKey"] is True

    cleared = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["hasApiKey"] is False
    assert client.get("/api/ai/settings").json()["hasApiKey"] is False


def test_blank_api_key_clear_does_not_validate_provider_key(client, monkeypatch):
    client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-local",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("clearing a key should not validate provider credentials")

    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", fail_if_called)
    cleared = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["hasApiKey"] is False


def test_missing_api_key_preserves_saved_user_key(client):
    first = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-local",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert first.status_code == 200
    assert first.json()["hasApiKey"] is True

    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["hasApiKey"] is True
    assert client.get("/api/ai/settings").json()["hasApiKey"] is True


def test_invalid_api_key_format_is_rejected(client):
    rejected_key = "saved DeepSeek key"
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": rejected_key,
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 422
    detail = str(saved.json()["detail"])
    assert "plain ASCII without spaces" in detail
    assert rejected_key not in saved.text
    assert "[REDACTED]" in saved.text


def test_settings_test_failure_does_not_expose_provider_detail(client, monkeypatch):
    provider_detail = "Authorization: Bearer audit-secret-provider-detail"

    class FailingClient:
        settings = EffectiveAiSettings(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test-local",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        )

        def is_enabled(self):
            return True

        def complete(self, *args, **kwargs):
            return None, llm_module.LlmError(
                "provider-specific message that must not be returned",
                "auth_error",
                401,
                detail=provider_detail,
            )

    monkeypatch.setattr("app.routers.settings.LlmClient", FailingClient)

    response = client.post("/api/ai/test", json={"prompt": "ping"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "mode": "error",
        "message": "API key is invalid or expired.",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "errorType": "auth_error",
        "statusCode": 401,
        "detail": None,
    }
    assert provider_detail not in response.text
    assert "provider-specific message" not in response.text


def test_settings_test_normalizes_unknown_error_type(client, monkeypatch):
    class FailingClient:
        settings = EffectiveAiSettings(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test-local",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        )

        def is_enabled(self):
            return True

        def complete(self, *args, **kwargs):
            return None, llm_module.LlmError("unsafe dynamic error", "provider_private_error", 500, detail="unsafe detail")

    monkeypatch.setattr("app.routers.settings.LlmClient", FailingClient)

    response = client.post("/api/ai/test", json={"prompt": "ping"})

    assert response.status_code == 200
    assert response.json()["errorType"] == "unknown"
    assert response.json()["message"] == "The model test failed. Check the provider settings and try again."
    assert response.json()["detail"] is None
    assert "unsafe" not in response.text


def test_saved_empty_key_does_not_fallback_to_env(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-local")
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "",
            "temperature": 0.3,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["hasApiKey"] is False
    assert client.get("/api/ai/settings").json()["hasApiKey"] is False


def test_legacy_cached_key_is_not_trusted(client):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_settings(
              id, provider, base_url, model, api_key_encrypted,
              temperature, timeout_seconds, updated_at
            )
            VALUES (
              'local-default', 'deepseek', 'https://api.deepseek.com',
              'deepseek-v4-flash', 'sk-legacy-local', 0.3, 20, CURRENT_TIMESTAMP
            )
            """
        )

    loaded = client.get("/api/ai/settings")
    assert loaded.status_code == 200
    assert loaded.json()["hasApiKey"] is False


def test_deepseek_url_does_not_add_v1():
    assert _chat_completions_url("https://api.deepseek.com", "deepseek") == "https://api.deepseek.com/chat/completions"
    assert _chat_completions_url("https://api.deepseek.com/", "deepseek") == "https://api.deepseek.com/chat/completions"
    assert _chat_completions_url("https://api.deepseek.com/v1", "deepseek") == "https://api.deepseek.com/chat/completions"


def test_http_errors_are_classified_for_safe_frontend_messages():
    assert _classify_http_error(401, "").error_type == "auth_error"
    assert _classify_http_error(403, "").error_type == "auth_error"
    assert _classify_http_error(404, "model does not exist").error_type == "bad_model"
    assert _classify_http_error(404, "model not found").error_type == "bad_model"
    assert _classify_http_error(404, "endpoint not found").error_type == "bad_base_url"
    assert _classify_http_error(429, "quota exceeded").error_type == "rate_limit"
    assert _classify_http_error(402, "").error_type == "insufficient_balance"


def test_deepseek_reasoning_models_get_minimum_token_budget():
    settings = EffectiveAiSettings(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="sk-test-local",
        temperature=0.1,
        timeout_seconds=10,
        updated_at="",
    )
    assert _effective_max_tokens(settings, 32) == 512


def test_llm_is_enabled_when_saved_key_exists_without_env_gate(monkeypatch):
    monkeypatch.setenv("USE_REAL_LLM", "0")
    monkeypatch.setattr(
        llm_module,
        "get_effective_ai_settings",
        lambda: EffectiveAiSettings(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test-local",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        ),
    )

    assert llm_module.LlmClient().is_enabled() is True


def test_llm_stays_disabled_for_mock_provider(monkeypatch):
    monkeypatch.setattr(
        llm_module,
        "get_effective_ai_settings",
        lambda: EffectiveAiSettings(
            provider="mock",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test-local",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        ),
    )

    assert llm_module.LlmClient().is_enabled() is False


def test_message_content_rejects_empty_text():
    assert _message_content({"content": ""}) == ""
    assert _message_content({"content": [{"type": "text", "text": "OK"}]}) == "OK"


def test_deepseek_key_enables_live_test_without_real_llm_gate(client, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"OK live"},"finish_reason":"stop"}]}'

        def json(self):
            return {"choices": [{"message": {"content": "OK live"}, "finish_reason": "stop"}]}

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

    monkeypatch.setenv("USE_REAL_LLM", "0")
    monkeypatch.setattr(llm_module.httpx, "Client", FakeHttpClient)

    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-local",
            "temperature": 0.1,
            "timeoutSeconds": 10,
        },
    )
    assert saved.status_code == 200

    tested = client.post("/api/ai/test", json={"prompt": "ping"})
    assert tested.status_code == 200
    body = tested.json()
    assert body["ok"] is True
    assert body["mode"] == "llm"
    assert body["message"] == "OK live"
    assert calls
    assert calls[0]["json"]["model"] == "deepseek-v4-flash"


def test_llm_finish_reason_length_is_reported_as_truncated(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"{\\"summary\\":\\"partial"},"finish_reason":"length"}]}'

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": '{"summary":"partial'},
                        "finish_reason": "length",
                    }
                ]
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            return FakeResponse()

    monkeypatch.setattr(
        llm_module,
        "get_effective_ai_settings",
        lambda: EffectiveAiSettings(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="sk-test-local",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        ),
    )
    monkeypatch.setattr(llm_module.httpx, "Client", FakeHttpClient)

    result, error = llm_module.LlmClient().complete(
        "planning_goal_plan", "system", "user", routing_enabled=False
    )

    assert result is None
    assert error is not None
    assert error.error_type == "model_output_truncated"
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT error, output_summary FROM ai_runs
            WHERE feature = 'planning_goal_plan'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row["error"] == "model output was truncated"
    assert row["output_summary"] == ""


def test_llm_rejects_invalid_key_format_before_request(monkeypatch):
    monkeypatch.setenv("USE_REAL_LLM", "1")
    monkeypatch.setattr(
        llm_module,
        "get_effective_ai_settings",
        lambda: EffectiveAiSettings(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="saved DeepSeek key",
            temperature=0.1,
            timeout_seconds=10,
            updated_at="",
        ),
    )

    result, error = llm_module.LlmClient().complete("settings_test", "system", "user")

    assert result is None
    assert error is not None
    assert error.error_type == "invalid_key_format"
    assert "plain ASCII without spaces" in error.message


def test_provider_key_status_becomes_invalid_and_can_be_replaced_without_delete(client):
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-old-provider-key",
            "temperature": 0.2,
            "timeoutSeconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["keyStatus"] == "valid"

    ai_settings_module.mark_provider_key_invalid("deepseek", "sk-old-provider-key", "auth_error")
    invalid = client.get("/api/ai/settings").json()
    assert invalid["hasApiKey"] is True
    assert invalid["keyStatus"] == "invalid"
    assert invalid["keyErrorType"] == "auth_error"
    assert invalid["savedProviders"][0]["keyStatus"] == "invalid"
    assert ai_settings_module.get_effective_ai_settings_for_provider("deepseek").has_usable_api_key is False

    replaced = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-new-provider-key",
            "temperature": 0.2,
            "timeoutSeconds": 20,
        },
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["hasApiKey"] is True
    assert body["keyStatus"] == "valid"
    assert body["keyErrorType"] == ""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT key_status, key_error_type, api_key_encrypted FROM ai_provider_configs WHERE provider = 'deepseek'"
        ).fetchone()
    assert row["key_status"] == "valid"
    assert row["key_error_type"] == ""
    assert row["api_key_encrypted"] == "sk-new-provider-key"
