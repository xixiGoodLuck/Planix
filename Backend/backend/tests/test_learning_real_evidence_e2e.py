from __future__ import annotations

from copy import deepcopy
import time

from app.db import get_conn
from app.learning.evidence.transcript import (
    LearningTranscriptRegistrationService,
    LearningTranscriptRepository,
    PersistentTranscriptProvider,
)
from app.learning.runtime import (
    LearningRuntimeConfig,
    LearningRuntimeFactory,
    PostgresLearningArtifactRepository,
)
from app.main import app
from app.routers.learning import (
    LearningRunManager,
    get_learning_run_manager,
    get_learning_transcript_service,
)

from learning_pipeline_fixtures import ScriptedPipelineModel, fastapi_pipeline_responses
from test_learning_transcript_repository import transcript_resource


CONTROLLED_SRT = """1
00:00:10,000 --> 00:00:14,000
PLANIX_TRANSCRIPT_SECRET_728391

2
00:00:15,000 --> 00:00:20,000
FastAPI routing and Pydantic validation.

3
00:00:30,000 --> 00:00:34,000
Routing implementation with GET and POST.

4
00:00:35,000 --> 00:00:40,000
Pydantic request validation implementation.

5
00:00:50,000 --> 00:00:54,000
Database persistence stores records.

6
00:00:55,000 --> 00:01:00,000
CRUD creates reads updates and deletes records.
"""


class ControlledProductionMetadataAdapter:
    def search(self, _query):
        return []

    def fetch_metadata(self, external_id):
        assert external_id == transcript_resource().external_id
        return transcript_resource()

    def resolve_url(self, value):
        assert value == transcript_resource().canonical_url
        return transcript_resource()

    def health_check(self):
        return True


class ControlledProductionSemanticAdapter:
    def __init__(self):
        responses = deepcopy(fastapi_pipeline_responses())
        for coverage in responses[3]["coverage"]:
            coverage["evidenceIndexes"] = [coverage["segmentIndex"] * 2]
        self.delegate = ScriptedPipelineModel(responses)

    def complete(self, **kwargs):
        return self.delegate.complete(**kwargs)

    def health_check(self):
        return True


def _wait(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/learning/runs/{run_id}")
        if response.json()["status"] in {"completed", "failed"}:
            return response
        time.sleep(0.02)
    raise AssertionError("Learning run did not finish")


def _runtime_factory(video_provider, transcript_repository):
    def build():
        return LearningRuntimeFactory(
            LearningRuntimeConfig(
                video_provider=video_provider,
                transcript_provider=PersistentTranscriptProvider(
                    transcript_repository
                ),
                artifact_store="postgres",
                model_provider=ControlledProductionSemanticAdapter(),
                environment="production",
                artifact_repository=PostgresLearningArtifactRepository(),
                transcript_repository=transcript_repository,
            )
        ).create()

    return build


def test_controlled_production_adapter_e2e_and_restart_recovery(client) -> None:
    video_provider = ControlledProductionMetadataAdapter()
    transcript_repository = LearningTranscriptRepository()
    transcript_service = LearningTranscriptRegistrationService(
        video_provider,
        transcript_repository,
    )
    runtime_builder = _runtime_factory(video_provider, transcript_repository)
    manager_a = LearningRunManager(runtime_builder)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_a
    app.dependency_overrides[get_learning_transcript_service] = lambda: transcript_service
    try:
        registered = client.post(
            "/api/learning/transcripts",
            json={
                "videoUrl": transcript_resource().canonical_url,
                "format": "srt",
                "language": "en",
                "sourceName": "controlled-authorized.srt",
                "content": CONTROLLED_SRT,
            },
        )
        assert registered.status_code == 201

        created = client.post(
            "/api/learning/runs",
            json={
                "goal": "Learn FastAPI and build a CRUD API from this video",
                "preferences": {
                    "target_result": "Build a working FastAPI CRUD API",
                    "content_budget": {"maximumTotalMinutes": 10},
                    "resourcePreference": {
                        "userSuppliedUrls": [transcript_resource().canonical_url]
                    },
                },
                "constraints": [],
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        terminal = _wait(client, run_id)
        assert terminal.json()["status"] == "completed", terminal.json()
        events = client.get(f"/api/learning/runs/{run_id}/events")
        result_a = client.get(f"/api/learning/runs/{run_id}/result")
        assert events.status_code == 200
        assert "session_completed" in events.text
        assert result_a.status_code == 200
        quality = result_a.json()["learning_quality_report"]
        assert quality["passed"] is True
        unsupported = next(
            check
            for check in quality["qualityChecks"]
            if check["rule"] == "unsupported_timestamp"
        )
        assert unsupported["passed"] is True
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        app.dependency_overrides.pop(get_learning_transcript_service, None)
        manager_a.shutdown()

    with get_conn() as conn:
        isolation = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM learning_transcript_segments WHERE text LIKE %s) AS transcript_segments,
              (SELECT COUNT(*) FROM learning_artifacts WHERE content_json::text LIKE %s) AS artifacts,
              (SELECT COUNT(*) FROM learning_runs WHERE row_to_json(learning_runs)::text LIKE %s) AS runs,
              (SELECT COUNT(*) FROM learning_checkpoints WHERE row_to_json(learning_checkpoints)::text LIKE %s) AS checkpoints,
              (SELECT COUNT(*) FROM learning_resume_events WHERE row_to_json(learning_resume_events)::text LIKE %s) AS events
            """,
            tuple(["%PLANIX_TRANSCRIPT_SECRET_728391%"] * 5),
        ).fetchone()
    assert isolation == {
        "transcript_segments": 1,
        "artifacts": 0,
        "runs": 0,
        "checkpoints": 0,
        "events": 0,
    }

    manager_b = LearningRunManager(runtime_builder)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_b
    try:
        recovered = client.get(f"/api/learning/runs/{run_id}/result")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager_b.shutdown()
    assert recovered.status_code == 200
    assert recovered.json() == result_a.json()
