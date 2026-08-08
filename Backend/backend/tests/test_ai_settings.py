from app.db import get_conn
from app.schemas import normalize_local_base_url
from app.services.ai_settings import ROUTABLE_TASK_TYPES


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


def test_v2_routing_save_is_idempotent_and_does_not_validate_keys(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_settings._validate_provider_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("routing save must not validate keys")),
    )
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
            """INSERT INTO ai_provider_configs(provider, base_url, model, api_key_encrypted, updated_at)
               VALUES ('custom', 'http://example.invalid/v1', 'custom-model', 'encrypted-value', CURRENT_TIMESTAMP)"""
        )
        conn.execute(
            """INSERT INTO ai_model_routing_rules(task_type, primary_provider, fallback_providers_json, local_fallback_enabled, updated_at)
               VALUES ('goal_understanding', 'custom', '[]', 0, CURRENT_TIMESTAMP)"""
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
