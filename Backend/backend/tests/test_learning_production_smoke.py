from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.learning.evidence.providers import MockVideoProvider
from app.learning.evidence.transcript import (
    MockTranscriptProvider,
    TranscriptDocument,
)
from app.learning.runtime import LearningRuntimeConfig, RuntimeUnavailable
from app.learning.runtime.bootstrap import LearningRuntimeBootstrap

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


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

    def health_check(self):
        return True

    def fetch_transcript(self, resource):
        if resource.external_id == "fastapi-pipeline-a":
            segments = [
                {
                    "id": "routing",
                    "startSeconds": 300,
                    "endSeconds": 600,
                    "text": "FastAPI routing maps HTTP requests to handlers.",
                },
                {
                    "id": "pydantic",
                    "startSeconds": 600,
                    "endSeconds": 900,
                    "text": "Pydantic validates request and response data.",
                },
                {
                    "id": "routing-repeat",
                    "startSeconds": 1200,
                    "endSeconds": 2100,
                    "text": "A second routing and validation implementation.",
                },
            ]
        else:
            segments = [
                {
                    "id": "persistence-crud",
                    "startSeconds": 240,
                    "endSeconds": 840,
                    "text": "Database persistence implements all CRUD operations.",
                }
            ]
        return TranscriptDocument(
            resourceId=resource.id,
            fingerprint=resource.content_fingerprint,
            language=resource.language,
            segments=segments,
        )


class ProductionSemanticModel:
    def __init__(self, delegate, *, healthy=True):
        self.delegate = delegate
        self.healthy = healthy

    def complete(self, **kwargs):
        return self.delegate.complete(**kwargs)

    def health_check(self):
        return self.healthy


class ProductionArtifactRepository:
    repository_namespace = "learning_artifacts"
    schema_version = 1

    def __init__(self, *, healthy=True):
        self.healthy = healthy
        self.artifacts = {}
        self.checkpoints = {}
        self.resume_events = []

    def health_check(self):
        return self.healthy

    @staticmethod
    def _key(session_id, artifact_type, artifact_id, version):
        return session_id, artifact_type, artifact_id, version

    def save(self, envelope):
        key = self._key(
            envelope.session_id,
            envelope.artifact_type,
            envelope.artifact_id,
            envelope.version,
        )
        if key in self.artifacts:
            raise ValueError("duplicate artifact")
        self.artifacts[key] = envelope.model_copy(deep=True)

    def get(self, session_id, artifact_type, artifact_id, version):
        value = self.artifacts.get(
            self._key(session_id, artifact_type, artifact_id, version)
        )
        return value.model_copy(deep=True) if value is not None else None

    def list_versions(self, session_id, artifact_type, artifact_id=None):
        return [
            value.model_copy(deep=True)
            for key, value in self.artifacts.items()
            if key[0] == session_id
            and key[1] == artifact_type
            and (artifact_id is None or key[2] == artifact_id)
        ]

    def delete(self, session_id, artifact_type, artifact_id, version):
        self.artifacts.pop(
            self._key(session_id, artifact_type, artifact_id, version),
            None,
        )

    def save_checkpoint(self, checkpoint):
        self.checkpoints[checkpoint.run_id] = checkpoint.model_copy(deep=True)

    def get_checkpoint(self, run_id):
        value = self.checkpoints.get(run_id)
        return value.model_copy(deep=True) if value is not None else None

    @contextmanager
    def transaction(self):
        yield

    def save_resume_event(self, event):
        self.resume_events.append(event)

    def get_resume_events(self, run_id):
        return [
            event
            for event in self.resume_events
            if getattr(event, "run_id", None) == run_id
        ]


def _model() -> ProductionSemanticModel:
    responses = deepcopy(fastapi_pipeline_responses())
    for item in responses[3]["coverage"]:
        if item["segmentIndex"] == 1:
            item["evidenceIndexes"] = [2]
        elif item["segmentIndex"] == 2:
            item["evidenceIndexes"] = [3]
    return ProductionSemanticModel(ScriptedPipelineModel(responses))


def _production_config(**updates) -> LearningRuntimeConfig:
    fixture = build_fastapi_learning_pipeline_fixture()
    values = {
        "video_provider": ProductionVideoSource(fixture.provider),
        "transcript_provider": ProductionTranscriptProvider(),
        "artifact_store": "postgres",
        "model_provider": _model(),
        "environment": "production",
        "artifact_repository": ProductionArtifactRepository(),
    }
    values.update(updates)
    return LearningRuntimeConfig(**values)


def test_production_startup_health_run_and_events(monkeypatch) -> None:
    bootstrap = LearningRuntimeBootstrap(lambda: _production_config())
    monkeypatch.setattr(
        main_module,
        "get_learning_runtime_bootstrap",
        lambda: bootstrap,
    )

    with TestClient(main_module.app) as client:
        health = client.get("/api/learning/health")
        assert health.status_code == 200
        assert health.json()["runtime"]["status"] == "ready"
        assert health.json()["startup_status"]["status"] == "ready"
        assert health.json()["transcript_source_status"] == {
            "status": "ready",
            "error_type": "",
            "source_type": "authorized",
        }

        created = client.post(
            "/api/learning/runs",
            json={
                "goal": "学习 FastAPI 并完成 CRUD API",
                "preferences": {
                    "target_result": "独立完成一个可运行的 FastAPI CRUD API",
                    "content_budget": {"maximumTotalMinutes": 60},
                },
                "constraints": [],
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        status = None
        for _ in range(200):
            status = client.get(f"/api/learning/runs/{run_id}")
            if status.json()["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert status is not None
        assert status.json()["status"] == "completed"

        events = client.get(f"/api/learning/runs/{run_id}/events")
        assert events.status_code == 200
        assert "session_created" in events.text
        assert "session_completed" in events.text


def test_startup_reports_missing_provider() -> None:
    bootstrap = LearningRuntimeBootstrap(
        lambda: _production_config(video_provider=None)
    )

    report = bootstrap.startup()

    assert report.status == "unavailable"
    assert next(
        item for item in report.checks if item.component == "video_provider"
    ).error_type == "missing_configuration"
    with pytest.raises(RuntimeUnavailable):
        bootstrap.create_runtime()


def test_startup_reports_artifact_store_failure() -> None:
    bootstrap = LearningRuntimeBootstrap(
        lambda: _production_config(
            artifact_repository=ProductionArtifactRepository(healthy=False)
        )
    )

    report = bootstrap.startup()

    assert report.status == "unavailable"
    assert next(
        item for item in report.checks if item.component == "artifact_store"
    ).error_type == "unavailable"


def test_startup_reports_model_unavailable() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    unavailable_model = ProductionSemanticModel(fixture.model, healthy=False)
    bootstrap = LearningRuntimeBootstrap(
        lambda: _production_config(model_provider=unavailable_model)
    )

    report = bootstrap.startup()

    assert report.status == "unavailable"
    assert next(
        item for item in report.checks if item.component == "model_provider"
    ).error_type == "unavailable"


def test_production_bootstrap_forbids_mock_fallback() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    bootstrap = LearningRuntimeBootstrap(
        lambda: _production_config(
            video_provider=MockVideoProvider([]),
            transcript_provider=MockTranscriptProvider([]),
            model_provider=fixture.model,
        )
    )

    report = bootstrap.startup()

    assert report.status == "unavailable"
    errors = {
        item.component: item.error_type
        for item in report.checks
        if item.status == "unavailable"
    }
    assert errors["video_provider"] == "mock_forbidden"
    assert errors["transcript_provider"] == "mock_forbidden"
    assert errors["model_provider"] == "mock_forbidden"


def test_development_bootstrap_allows_explicit_mock_components() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    bootstrap = LearningRuntimeBootstrap(
        lambda: LearningRuntimeConfig(
            video_provider=fixture.provider,
            transcript_provider=MockTranscriptProvider([]),
            artifact_store="memory",
            model_provider=fixture.model,
            environment="development",
        )
    )

    report = bootstrap.startup()

    assert report.status == "ready"
