from __future__ import annotations

import json
import time

import pytest

from app.learning.runtime import InMemoryArtifactStore, LearningRuntime
from app.learning.services import LearningPipeline
from app.main import app
from app.routers.learning import LearningRunManager, get_learning_run_manager

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


def _successful_runtime() -> LearningRuntime:
    fixture = build_fastapi_learning_pipeline_fixture()
    return LearningRuntime(
        LearningPipeline(provider=fixture.provider, model=fixture.model),
        artifact_store=InMemoryArtifactStore(),
    )


def _failing_runtime() -> LearningRuntime:
    fixture = build_fastapi_learning_pipeline_fixture()
    responses = fastapi_pipeline_responses()
    responses[1]["capabilities"][0]["outcomeIndexes"] = [99]
    return LearningRuntime(
        LearningPipeline(
            provider=fixture.provider,
            model=ScriptedPipelineModel(responses),
        ),
        artifact_store=InMemoryArtifactStore(),
    )


@pytest.fixture()
def learning_api(client):
    managers = []

    def install(runtime_factory=_successful_runtime):
        manager = LearningRunManager(runtime_factory)
        managers.append(manager)
        app.dependency_overrides[get_learning_run_manager] = lambda: manager
        return client, manager

    yield install

    app.dependency_overrides.pop(get_learning_run_manager, None)
    for manager in managers:
        manager.shutdown()


def _create(client):
    response = client.post(
        "/api/learning/runs",
        json={
            "goal": "30天学习FastAPI并完成CRUD API",
            "preferences": {
                "target_result": "完成一个可运行的FastAPI CRUD API",
                "language_preference": {
                    "preferredLanguages": ["zh-CN"],
                    "acceptableLanguages": ["en"],
                    "subtitlesAcceptable": True,
                },
            },
            "constraints": ["每天最多学习一小时"],
        },
    )
    assert response.status_code == 202
    return response.json()["run_id"]


def _wait_for_terminal(client, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/learning/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Learning API run did not reach a terminal state")


def test_create_learning_run_returns_runtime_session_id(learning_api) -> None:
    client, manager = learning_api()

    run_id = _create(client)

    assert run_id.startswith("learning-session-")
    assert manager.get_runtime(run_id).get_session(run_id) is not None


def test_get_learning_run_returns_runtime_status(learning_api) -> None:
    client, _ = learning_api()
    run_id = _create(client)

    status = _wait_for_terminal(client, run_id)

    assert status["status"] == "completed"
    assert status["current_stage"] == "completed"
    assert status["completed_stages"] == [
        "understanding",
        "knowledge_generating",
        "evidence_generating",
        "content_selecting",
        "quality_checking",
    ]
    assert status["error"] is None


def test_learning_events_sse_preserves_runtime_event_order(learning_api) -> None:
    client, _ = learning_api()
    run_id = _create(client)

    response = client.get(f"/api/learning/runs/{run_id}/events")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [event["event_type"] for event in events] == [
        "session_created",
        "stage_started",
        "artifact_saved",
        "stage_completed",
        "stage_started",
        "artifact_saved",
        "artifact_saved",
        "stage_completed",
        "stage_started",
        "artifact_saved",
        "stage_completed",
        "stage_started",
        "artifact_saved",
        "artifact_saved",
        "stage_completed",
        "stage_started",
        "artifact_saved",
        "stage_completed",
        "session_completed",
    ]
    serialized = response.text.casefold()
    assert "prompt" not in serialized
    assert "token" not in serialized
    assert "reasoning" not in serialized


def test_get_learning_result_returns_plan_and_quality(learning_api) -> None:
    client, _ = learning_api()
    run_id = _create(client)
    assert _wait_for_terminal(client, run_id)["status"] == "completed"

    response = client.get(f"/api/learning/runs/{run_id}/result")
    payload = response.json()

    assert response.status_code == 200
    assert payload["learning_content_plan"]["items"]
    assert payload["learning_quality_report"]["passed"] is True
    assert payload["evidence_graph"]["resources"]
    assert payload["evidence_graph"]["segments"]


def test_failed_runtime_is_exposed_as_failed_status(learning_api) -> None:
    client, _ = learning_api(_failing_runtime)
    run_id = _create(client)

    status = _wait_for_terminal(client, run_id)

    assert status["status"] == "failed"
    assert status["current_stage"] == "failed"
    assert status["error"]["stage"] == "knowledge_generating"
    assert status["error"]["error_type"]
    result_response = client.get(f"/api/learning/runs/{run_id}/result")
    assert result_response.status_code == 409
