from app.db import get_conn, load_memory, save_memory
from app.services import ai_settings as ai_settings_module


def _seed_ai_memory_cache() -> None:
    save_memory("local-user", "每天 1 小时，项目驱动")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO plans(id, date, time, content, done)
            VALUES ('plan-1', '2026-07-04', '09:00', 'Formal plan', 0)
            """
        )
        conn.execute(
            """
            INSERT INTO planning_goals(id, goal, summary)
            VALUES ('goal-cache-1', 'Learn Python', 'Cached planning result')
            """
        )
        conn.execute(
            """
            INSERT INTO agent_runs(id, input, status, output_summary)
            VALUES ('run-1', 'Goal A', 'done', 'Runtime summary A')
            """
        )
        conn.execute(
            """
            INSERT INTO agent_runs(id, input, status, output_summary)
            VALUES ('run-2', 'Goal B', 'done', '')
            """
        )
        conn.execute(
            """
            INSERT INTO agent_events(id, run_id, sequence, event_type, payload)
            VALUES ('event-1', 'run-1', 1, 'node', '{}')
            """
        )


def test_ai_memory_cache_stats_counts_only_history_summaries(client):
    _seed_ai_memory_cache()

    stats = client.get("/api/settings/ai-memory-cache/stats").json()

    assert stats["preferenceMemory"] == 1
    assert stats["historySummaries"] == 1
    assert stats["agentRuns"] == 2
    assert stats["agentEvents"] == 1
    assert stats["planningGoals"] == 1
    assert stats["plans"] == 1


def test_delete_preference_memory_preserves_ai_settings_and_plans(client, monkeypatch):
    monkeypatch.setattr(ai_settings_module, "_validate_provider_config", lambda *args, **kwargs: None)
    _seed_ai_memory_cache()
    saved = client.put(
        "/api/ai/settings",
        json={
            "provider": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "apiKey": "sk-test-maintenance-key",
            "temperature": 0.3,
            "timeoutSeconds": 40,
        },
    )
    assert saved.status_code == 200

    response = client.delete("/api/settings/memory/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"]["preferenceMemory"] == 1
    assert body["preserved"]["aiSettings"] is True
    assert load_memory() == ""
    assert client.get("/api/ai/settings").json()["hasApiKey"] is True
    assert body["before"]["plans"] == body["after"]["plans"] == 1


def test_delete_history_memory_clears_summaries_without_deleting_runs_or_events(client):
    _seed_ai_memory_cache()

    response = client.delete("/api/settings/memory/history")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"]["historySummaries"] == 1
    assert body["before"]["agentRuns"] == body["after"]["agentRuns"] == 2
    assert body["before"]["agentEvents"] == body["after"]["agentEvents"] == 1
    assert body["before"]["plans"] == body["after"]["plans"] == 1
    with get_conn() as conn:
        remaining = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM agent_runs
            WHERE output_summary IS NOT NULL AND TRIM(output_summary) != ''
            """
        ).fetchone()["total"]
    assert remaining == 0


def test_delete_runtime_runs_removes_events_before_runs_and_preserves_plans(client):
    _seed_ai_memory_cache()

    response = client.delete("/api/settings/runtime/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == {"agentRuns": 2, "agentEvents": 1}
    assert body["after"]["agentRuns"] == 0
    assert body["after"]["agentEvents"] == 0
    assert body["before"]["plans"] == body["after"]["plans"] == 1


def test_delete_planning_history_preserves_formal_plans(client):
    _seed_ai_memory_cache()

    response = client.delete("/api/settings/planning/history")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"]["planningGoals"] == 1
    assert body["after"]["planningGoals"] == 0
    assert body["before"]["plans"] == body["after"]["plans"] == 1


def test_delete_all_ai_memory_cache_returns_step_results_and_is_idempotent(client):
    _seed_ai_memory_cache()

    response = client.delete("/api/settings/ai-memory-cache")
    second = client.delete("/api/settings/ai-memory-cache")

    assert response.status_code == 200
    assert second.status_code == 200
    body = response.json()
    assert body["steps"]["preferences"]["preferenceMemory"] == 1
    assert body["steps"]["historySummaries"]["historySummaries"] == 1
    assert body["steps"]["runtimeRuns"] == {"agentRuns": 2, "agentEvents": 1}
    assert body["steps"]["planningHistory"]["planningGoals"] == 1
    assert body["after"]["preferenceMemory"] == 0
    assert body["after"]["historySummaries"] == 0
    assert body["after"]["agentRuns"] == 0
    assert body["after"]["agentEvents"] == 0
    assert body["after"]["planningGoals"] == 0
    assert body["before"]["plans"] == body["after"]["plans"] == 1
    assert second.json()["after"]["plans"] == 1
