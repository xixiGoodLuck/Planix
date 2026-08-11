from __future__ import annotations

from copy import deepcopy

import pytest

from app.learning.contracts import ResourcePreference
from app.learning.evidence.providers import BilibiliProvider, VideoSourceProviderError
from app.learning.evidence.transcript import (
    LearningTranscriptRepository,
    PersistentTranscriptProvider,
)
from app.learning.runtime import (
    LearningRuntimeConfig,
    LearningRuntimeError,
    LearningRuntimeFactory,
    PostgresLearningArtifactRepository,
)
from app.learning.runtime.bootstrap import (
    LearningRuntimeBootstrap,
    load_learning_runtime_config,
)

from learning_pipeline_fixtures import ScriptedPipelineModel, build_fastapi_learning_pipeline_fixture
from test_learning_transcript_repository import transcript_resource


class ControlledProductionVideoAdapter:
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


class ControlledProductionModelAdapter:
    def __init__(self):
        fixture = build_fastapi_learning_pipeline_fixture()
        self.delegate = ScriptedPipelineModel(deepcopy(fixture.model.responses))

    def complete(self, **kwargs):
        return self.delegate.complete(**kwargs)

    def health_check(self):
        return True


def test_production_bootstrap_binds_persistent_transcript_provider(monkeypatch) -> None:
    monkeypatch.setenv("PLANIX_LEARNING_ENVIRONMENT", "production")
    config = load_learning_runtime_config()

    assert isinstance(config.video_provider, BilibiliProvider)
    assert isinstance(config.transcript_repository, LearningTranscriptRepository)
    assert isinstance(config.transcript_provider, PersistentTranscriptProvider)
    bootstrap = LearningRuntimeBootstrap(lambda: config)
    report = bootstrap.startup()
    try:
        assert report.status == "ready"
        health = bootstrap.health()
        assert health["runtime"]["status"] == "ready"
        assert health["providers"]["transcript"]["status"] == "ready"
        assert health["artifact_store"]["status"] == "ready"
        assert bootstrap.transcript_registration_service() is not None
    finally:
        bootstrap.shutdown()


def test_bilibili_url_validation_rejects_non_bilibili_identity() -> None:
    with pytest.raises(VideoSourceProviderError, match="bilibili.com"):
        BilibiliProvider.external_id_from_url("https://example.test/video/BV1xx411c7mD")
    assert (
        BilibiliProvider.external_id_from_url(
            "https://www.bilibili.com/video/BV1xx411c7mD"
        )
        == "BV1xx411c7mD"
    )


def test_user_supplied_video_without_transcript_fails_closed() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    runtime = LearningRuntimeFactory(
        LearningRuntimeConfig(
            video_provider=ControlledProductionVideoAdapter(),
            transcript_provider=PersistentTranscriptProvider(
                LearningTranscriptRepository()
            ),
            artifact_store="postgres",
            model_provider=ControlledProductionModelAdapter(),
            environment="production",
            artifact_repository=PostgresLearningArtifactRepository(),
            transcript_repository=LearningTranscriptRepository(),
        )
    ).create()
    scope = fixture.scope.model_copy(
        update={
            "resource_preference": ResourcePreference(
                userSuppliedUrls=[transcript_resource().canonical_url]
            )
        }
    )

    with pytest.raises(LearningRuntimeError) as caught:
        runtime.run(scope)

    assert caught.value.session.status == "failed"
    assert "TRANSCRIPT_UNAVAILABLE" in caught.value.session.error.message
