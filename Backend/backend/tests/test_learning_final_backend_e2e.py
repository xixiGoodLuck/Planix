from __future__ import annotations

from copy import deepcopy
import time

from app.learning.contracts import VideoResource
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
from learning_pipeline_fixtures import ScriptedPipelineModel


VIDEO_URL = "https://controlled.example/video/routing-golden"
CONTROLLED_ROUTING_SRT = """1
00:00:10,000 --> 00:00:30,000
HTTP GET reads a resource and HTTP POST creates a resource.

2
00:00:30,000 --> 00:00:50,000
A FastAPI path operation binds an HTTP method and URL path to a handler.

3
00:00:50,000 --> 00:01:10,000
FastAPI publishes OpenAPI documentation for registered path operations.

4
00:01:10,000 --> 00:01:30,000
Swagger UI displays and executes the GET and POST path operations.
"""


def _resource() -> VideoResource:
    return VideoResource(
        id="video-controlled-routing-golden",
        provider="bilibili",
        externalId="BV-CONTROLLED-ROUTING",
        canonicalUrl=VIDEO_URL,
        title="Controlled FastAPI routing corpus",
        language="en",
        durationSeconds=600,
        contentFingerprint="sha256:controlled-routing-golden-v1",
    )


class ControlledMetadataAdapter:
    def search(self, _query):
        return []

    def fetch_metadata(self, external_id):
        assert external_id == _resource().external_id
        return _resource()

    def resolve_url(self, value):
        assert value == VIDEO_URL
        return _resource()

    def health_check(self):
        return True


class ControlledSemanticAdapter:
    def __init__(self, responses):
        self.delegate = ScriptedPipelineModel(responses)

    @property
    def calls(self):
        return self.delegate.calls

    def complete(self, **kwargs):
        return self.delegate.complete(**kwargs)

    def health_check(self):
        return True


def _narrow_responses() -> list[dict]:
    return [
        {
            "outcomes": [
                {
                    "statement": "Explain GET and POST path operations and inspect them in Swagger UI.",
                    "acceptanceCriteria": [
                        "Distinguish GET from POST",
                        "Explain a path operation",
                        "Inspect both operations in Swagger UI",
                    ],
                    "importance": "required",
                }
            ]
        },
        {
            "capabilities": [
                {
                    "name": "Path operation interpretation",
                    "description": "Interpret method and path bindings.",
                    "whyRequired": "The target names GET, POST, and path operations.",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "Swagger UI inspection",
                    "description": "Inspect registered operations in Swagger UI.",
                    "whyRequired": "Swagger inspection is an explicit target.",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
            ],
            "edges": [
                {"sourceIndex": 0, "targetIndex": 1, "relation": "supports"}
            ],
        },
        {
            "knowledge": [
                {
                    "name": "GET and POST semantics",
                    "explanation": "GET reads and POST creates a resource.",
                    "whyRequired": "Method semantics support path operation interpretation.",
                    "capabilityIndexes": [0],
                    "importance": "required",
                    "masteryIndicators": ["Distinguish GET from POST"],
                },
                {
                    "name": "Path operation binding",
                    "explanation": "A path operation binds a method and path to a handler.",
                    "whyRequired": "The learner must explain path operations.",
                    "capabilityIndexes": [0],
                    "importance": "required",
                    "masteryIndicators": ["Explain the binding"],
                },
                {
                    "name": "Swagger UI inspection",
                    "explanation": "Swagger UI displays and executes registered operations.",
                    "whyRequired": "The target requires documentation inspection.",
                    "capabilityIndexes": [1],
                    "importance": "required",
                    "masteryIndicators": ["Inspect GET and POST operations"],
                },
            ],
            "edges": [
                {
                    "sourceIndex": 0,
                    "targetIndex": 1,
                    "relation": "supports",
                    "reason": "Method semantics support operation interpretation.",
                },
                {
                    "sourceIndex": 1,
                    "targetIndex": 2,
                    "relation": "supports",
                    "reason": "Registered operations appear in documentation.",
                },
            ],
        },
        {
            "segmentAnnotations": [
                {
                    "segmentIndex": 0,
                    "contentSummary": "GET, POST, and FastAPI path operation semantics.",
                    "topics": ["GET", "POST", "Path operation"],
                },
                {
                    "segmentIndex": 1,
                    "contentSummary": "OpenAPI and Swagger UI operation inspection.",
                    "topics": ["OpenAPI", "Swagger UI"],
                },
            ],
            "coverage": [
                {
                    "knowledgeIndex": 0,
                    "segmentIndex": 0,
                    "evidenceIndexes": [0],
                    "coverageType": "explanation",
                    "coverageStrength": "full",
                    "confidence": 0.98,
                    "reason": "The verified cue states GET and POST semantics.",
                },
                {
                    "knowledgeIndex": 1,
                    "segmentIndex": 0,
                    "evidenceIndexes": [1],
                    "coverageType": "explanation",
                    "coverageStrength": "full",
                    "confidence": 0.98,
                    "reason": "The verified cue defines a path operation binding.",
                },
                {
                    "knowledgeIndex": 2,
                    "segmentIndex": 1,
                    "evidenceIndexes": [2, 3],
                    "coverageType": "demonstration",
                    "coverageStrength": "full",
                    "confidence": 0.98,
                    "reason": "Verified cues describe OpenAPI and Swagger UI inspection.",
                },
            ],
        },
    ]


def _broad_insufficient_responses() -> list[dict]:
    responses = deepcopy(_narrow_responses())
    responses[0]["outcomes"][0] = {
        "statement": "Build a complete persistent CRUD API.",
        "acceptanceCriteria": [
            "Define routes",
            "Persist records",
            "Create, read, update, and delete records",
        ],
        "importance": "required",
    }
    responses[1]["capabilities"].append(
        {
            "name": "Data persistence",
            "description": "Persist and mutate records.",
            "whyRequired": "A CRUD API requires stored state.",
            "outcomeIndexes": [0],
            "importance": "required",
        }
    )
    responses[2]["knowledge"].append(
        {
            "name": "Database persistence and CRUD",
            "explanation": "Persistent storage supports all CRUD operations.",
            "whyRequired": "The broad target explicitly requires complete CRUD.",
            "capabilityIndexes": [2],
            "importance": "required",
            "masteryIndicators": ["Persist and mutate records"],
        }
    )
    return responses


def _wait(client, run_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/learning/runs/{run_id}")
        if response.json()["status"] in {"completed", "failed", "waiting_evidence"}:
            return response
        time.sleep(0.02)
    raise AssertionError("Learning run did not finish")


def _setup(responses):
    metadata = ControlledMetadataAdapter()
    transcript_repository = LearningTranscriptRepository()
    transcript_service = LearningTranscriptRegistrationService(
        metadata,
        transcript_repository,
    )
    semantic = ControlledSemanticAdapter(responses)

    def build():
        return LearningRuntimeFactory(
            LearningRuntimeConfig(
                video_provider=metadata,
                transcript_provider=PersistentTranscriptProvider(
                    transcript_repository
                ),
                artifact_store="postgres",
                model_provider=semantic,
                environment="production",
                artifact_repository=PostgresLearningArtifactRepository(),
                transcript_repository=transcript_repository,
            )
        ).create()

    manager = LearningRunManager(build)
    return manager, transcript_service, semantic, build


def _register(client):
    response = client.post(
        "/api/learning/transcripts",
        json={
            "videoUrl": VIDEO_URL,
            "format": "srt",
            "language": "en",
            "sourceName": "controlled-production-routing.srt",
            "content": CONTROLLED_ROUTING_SRT,
        },
    )
    assert response.status_code == 201


def _create(client, goal: str):
    response = client.post(
        "/api/learning/runs",
        json={
            "goal": goal,
            "preferences": {
                "target_result": goal,
                "content_budget": {"maximumTotalMinutes": 5},
                "resourcePreference": {"userSuppliedUrls": [VIDEO_URL]},
            },
            "constraints": ["Use only the supplied authorized transcript"],
        },
    )
    assert response.status_code == 202
    return response.json()["run_id"]


def test_controlled_narrow_golden_api_sse_result_and_restart_recovery(client) -> None:
    manager, transcript_service, semantic, build = _setup(_narrow_responses())
    app.dependency_overrides[get_learning_run_manager] = lambda: manager
    app.dependency_overrides[get_learning_transcript_service] = (
        lambda: transcript_service
    )
    try:
        _register(client)
        run_id = _create(
            client,
            "Understand GET, POST, Path Operation, and Swagger UI from this video",
        )
        terminal = _wait(client, run_id)
        assert terminal.json()["status"] == "completed", terminal.json()
        events = client.get(f"/api/learning/runs/{run_id}/events")
        result = client.get(f"/api/learning/runs/{run_id}/result")
        assert events.status_code == 200
        assert events.text.index("knowledge_generation") < events.text.index(
            "evidence_generation"
        )
        assert events.text.index("evidence_generation") < events.text.index(
            "selection"
        )
        assert "session_completed" in events.text
        assert result.status_code == 200
        body = result.json()
        assert body["learning_quality_report"]["passed"] is True
        assert all(
            item["recommendedContent"]
            for item in body["learning_content_plan"]["items"]
        )
        unsupported = next(
            item
            for item in body["learning_quality_report"]["qualityChecks"]
            if item["rule"] == "unsupported_timestamp"
        )
        assert unsupported["passed"] is True
        assert len(semantic.calls) == 4
        assert all(call["feature"].startswith("learning_") for call in semantic.calls)
        assert "mock" not in type(semantic).__name__.casefold()
        expected_result = body
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        app.dependency_overrides.pop(get_learning_transcript_service, None)
        manager.shutdown()

    restarted = LearningRunManager(build)
    app.dependency_overrides[get_learning_run_manager] = lambda: restarted
    try:
        recovered_status = client.get(f"/api/learning/runs/{run_id}")
        recovered_result = client.get(f"/api/learning/runs/{run_id}/result")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        restarted.shutdown()
    assert recovered_status.json()["status"] == "completed"
    assert recovered_result.json() == expected_result


def test_broad_goal_with_routing_only_transcript_waits_for_evidence(client) -> None:
    manager, transcript_service, semantic, _build = _setup(
        _broad_insufficient_responses()
    )
    app.dependency_overrides[get_learning_run_manager] = lambda: manager
    app.dependency_overrides[get_learning_transcript_service] = (
        lambda: transcript_service
    )
    try:
        _register(client)
        run_id = _create(client, "Build a complete persistent FastAPI CRUD API")
        terminal = _wait(client, run_id)
        payload = terminal.json()
        result = client.get(f"/api/learning/runs/{run_id}/result")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        app.dependency_overrides.pop(get_learning_transcript_service, None)
        manager.shutdown()

    assert payload["status"] == "waiting_evidence"
    assert payload["error"] is None
    assert payload["intervention"]["kind"] == "additional_evidence_required"
    assert any(
        item["coverageStrength"] in {"MISSING", "PARTIAL"}
        for item in payload["intervention"]["requiredGaps"]
    )
    assert result.status_code == 409
    assert len(semantic.calls) == 4
