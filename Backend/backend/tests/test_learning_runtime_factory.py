from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from app.learning.evidence.providers import MockVideoProvider
from app.learning.evidence.transcript import MockTranscriptProvider, TranscriptDocument
from app.learning.runtime import (
    LearningRuntime,
    LearningRuntimeConfig,
    LearningRuntimeFactory,
    PostgresArtifactStore,
    RuntimeUnavailable,
)
from app.main import app
from app.routers.learning import LearningRunManager, get_learning_run_manager

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture


class ProductionVideoSource:
    def __init__(self, delegate):
        self.delegate = delegate

    def search(self, query):
        return self.delegate.search(query)

    def fetch_metadata(self, external_id):
        return self.delegate.fetch_metadata(external_id)

    def health_check(self):
        return True


class ProductionTranscriptProvider:
    source_type = "authorized"

    def fetch_transcript(self, resource):
        return TranscriptDocument(
            resourceId=resource.id,
            fingerprint=resource.content_fingerprint,
            language=resource.language,
            segments=[
                {
                    "id": "production-transcript-segment",
                    "startSeconds": 0,
                    "endSeconds": min(60, resource.duration_seconds),
                    "text": "Verified production transcript content.",
                }
            ],
        )

    def health_check(self):
        return True


class ProductionSemanticModel:
    def __init__(self, delegate):
        self.delegate = delegate

    def complete(self, **kwargs):
        return self.delegate.complete(**kwargs)

    def health_check(self):
        return True


class ProductionArtifactRepository:
    repository_namespace = "learning_artifacts"
    schema_version = 1

    def __init__(self, *, healthy=True):
        self.healthy = healthy

    def health_check(self):
        return self.healthy

    def save(self, envelope):
        raise NotImplementedError

    def get(self, *args):
        return None

    def list_versions(self, *args):
        return []

    def delete(self, *args):
        return None

    def save_checkpoint(self, checkpoint):
        raise NotImplementedError

    def get_checkpoint(self, run_id):
        return None

    @contextmanager
    def transaction(self):
        yield

    def save_resume_event(self, event):
        raise NotImplementedError

    def get_resume_events(self, run_id):
        return []


def _production_config(**updates):
    fixture = build_fastapi_learning_pipeline_fixture()
    config = LearningRuntimeConfig(
        video_provider=ProductionVideoSource(fixture.provider),
        transcript_provider=ProductionTranscriptProvider(),
        artifact_store="postgres",
        model_provider=ProductionSemanticModel(fixture.model),
        environment="production",
        artifact_repository=ProductionArtifactRepository(),
    )
    return replace(config, **updates)


def test_complete_production_config_creates_learning_runtime() -> None:
    runtime = LearningRuntimeFactory(_production_config()).create()

    assert isinstance(runtime, LearningRuntime)
    assert isinstance(runtime.artifact_store, PostgresArtifactStore)
    assert runtime.pipeline.evidence_pipeline.provider.video_provider.__class__ is (
        ProductionVideoSource
    )
    assert runtime.pipeline.evidence_pipeline.provider.transcript_provider.__class__ is (
        ProductionTranscriptProvider
    )


def test_learning_health_returns_ready_without_secrets(client) -> None:
    manager = LearningRunManager(LearningRuntimeFactory(_production_config()))
    app.dependency_overrides[get_learning_run_manager] = lambda: manager
    try:
        response = client.get("/api/learning/health")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager.shutdown()

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert payload["providers"]["video"]["status"] == "ready"
    assert payload["providers"]["transcript"]["status"] == "ready"
    assert payload["providers"]["model"]["status"] == "ready"
    assert payload["artifact_store"]["status"] == "ready"
    serialized = response.text.casefold()
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert "token" not in serialized


def test_missing_video_provider_fails_and_api_returns_503(client) -> None:
    factory = LearningRuntimeFactory(_production_config(video_provider=None))
    with pytest.raises(RuntimeUnavailable, match="video provider is not configured"):
        factory.create()

    manager = LearningRunManager(factory)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager
    try:
        health_response = client.get("/api/learning/health")
        response = client.post(
            "/api/learning/runs",
            json={"goal": "学习FastAPI", "preferences": {}, "constraints": []},
        )
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager.shutdown()
    assert health_response.status_code == 503
    assert health_response.json()["error"]["component"] == "video_provider"
    assert response.status_code == 503
    assert "video_provider" in response.json()["detail"]


def test_missing_transcript_provider_fails_explicitly() -> None:
    factory = LearningRuntimeFactory(_production_config(transcript_provider=None))

    with pytest.raises(RuntimeUnavailable, match="transcript provider is not configured"):
        factory.create()


def test_unavailable_artifact_store_fails_explicitly() -> None:
    factory = LearningRuntimeFactory(
        _production_config(
            artifact_repository=ProductionArtifactRepository(healthy=False)
        )
    )

    with pytest.raises(RuntimeUnavailable, match="artifact_store: health check failed"):
        factory.create()


def test_production_never_falls_back_to_mock_providers() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    config = _production_config(
        video_provider=MockVideoProvider([]),
        transcript_provider=MockTranscriptProvider([]),
        model_provider=ProductionSemanticModel(fixture.model),
    )

    with pytest.raises(RuntimeUnavailable, match="Mock provider is forbidden"):
        LearningRuntimeFactory(config).create()
