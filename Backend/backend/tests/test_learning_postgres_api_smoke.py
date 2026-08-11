from __future__ import annotations

import time

from dataclasses import replace

from app.db import get_conn
from app.learning.runtime import (
    LearningRuntimeFactory,
    PostgresLearningArtifactRepository,
)
from app.main import app
from app.routers.learning import LearningRunManager, get_learning_run_manager

from test_learning_production_smoke import _production_config


def _runtime():
    config = replace(
        _production_config(),
        artifact_repository=PostgresLearningArtifactRepository(),
    )
    return LearningRuntimeFactory(config).create()


def _wait(client, run_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/learning/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] in {
            "completed",
            "failed",
        }:
            return response
        time.sleep(0.02)
    raise AssertionError("PostgreSQL Learning run did not reach a terminal state")


def test_production_api_run_survives_manager_and_repository_recreation(client) -> None:
    with get_conn() as connection:
        pure_v2_before = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM planning_sessions) AS sessions,
              (SELECT COUNT(*) FROM planning_artifacts) AS artifacts,
              (SELECT COUNT(*) FROM harness_states) AS harness
            """
        ).fetchone()

    manager_a = LearningRunManager(_runtime)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_a
    try:
        created = client.post(
            "/api/learning/runs",
            json={
                "goal": "Learn FastAPI and build a CRUD API",
                "preferences": {
                    "target_result": "Build a working FastAPI CRUD API",
                    "content_budget": {"maximumTotalMinutes": 60},
                },
                "constraints": [],
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        terminal = _wait(client, run_id)
        assert terminal.json()["status"] == "completed"
        result_a = client.get(f"/api/learning/runs/{run_id}/result")
        assert result_a.status_code == 200
        assert result_a.json()["learning_quality_report"]["passed"] is True
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager_a.shutdown()

    manager_b = LearningRunManager(_runtime)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_b
    try:
        recovered_status = client.get(f"/api/learning/runs/{run_id}")
        recovered_result = client.get(f"/api/learning/runs/{run_id}/result")
        recovered_events = client.get(f"/api/learning/runs/{run_id}/events")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager_b.shutdown()

    assert recovered_status.status_code == 200
    assert recovered_status.json()["status"] == "completed"
    assert recovered_result.status_code == 200
    assert recovered_result.json() == result_a.json()
    assert recovered_events.status_code == 200
    assert "session_created" in recovered_events.text
    assert "session_completed" in recovered_events.text

    with get_conn() as connection:
        learning = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM learning_runs WHERE run_id = %s) AS runs,
              (SELECT COUNT(*) FROM learning_artifacts WHERE run_id = %s) AS artifacts,
              (SELECT COUNT(*) FROM learning_checkpoints WHERE run_id = %s) AS checkpoints,
              (SELECT COUNT(*) FROM learning_resume_events WHERE run_id = %s) AS events
            """,
            (run_id, run_id, run_id, run_id),
        ).fetchone()
        pure_v2_after = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM planning_sessions) AS sessions,
              (SELECT COUNT(*) FROM planning_artifacts) AS artifacts,
              (SELECT COUNT(*) FROM harness_states) AS harness
            """
        ).fetchone()
    assert learning["runs"] == 1
    assert learning["artifacts"] == 7
    assert learning["checkpoints"] == 1
    assert learning["events"] > 0
    assert pure_v2_after == pure_v2_before
