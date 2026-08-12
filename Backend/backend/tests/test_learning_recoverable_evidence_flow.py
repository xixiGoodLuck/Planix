from __future__ import annotations

from copy import deepcopy
from threading import Event
import time

import pytest

from app.learning.evidence.providers import MockVideoProvider
from app.learning.runtime import (
    InMemoryArtifactStore,
    LearningRuntime,
    LearningRuntimeError,
    LearningWaitingEvidenceResult,
    PostgresArtifactStore,
    PostgresLearningArtifactRepository,
)
from app.learning.services import LearningPipeline
from app.main import app
from app.routers.learning import LearningRunManager, get_learning_run_manager

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


def _partial_and_full_responses() -> list[dict]:
    responses = fastapi_pipeline_responses()
    full_evidence = deepcopy(responses[3])
    partial_evidence = deepcopy(full_evidence)
    partial_evidence["segmentAnnotations"] = partial_evidence[
        "segmentAnnotations"
    ][:2]
    partial_evidence["coverage"] = [
        item
        for item in partial_evidence["coverage"]
        if item["knowledgeIndex"] in {0, 1}
    ]
    return [*responses[:3], partial_evidence, full_evidence]


def _documents():
    fixture = build_fastapi_learning_pipeline_fixture()
    return list(fixture.provider._documents.values())


def _partial_runtime(*, store=None, provider=None, model=None):
    fixture = build_fastapi_learning_pipeline_fixture()
    documents = _documents()
    resolved_provider = provider or MockVideoProvider([documents[0]])
    resolved_model = model or ScriptedPipelineModel(_partial_and_full_responses())
    resolved_store = store or InMemoryArtifactStore()
    runtime = LearningRuntime(
        LearningPipeline(provider=resolved_provider, model=resolved_model),
        artifact_store=resolved_store,
        checkpoint_store=resolved_store,
    )
    return fixture, documents, resolved_provider, resolved_model, resolved_store, runtime


def _install_second_document(provider: MockVideoProvider, documents) -> None:
    provider._documents[documents[1].metadata.external_id] = documents[1]


def _wait_for_status(runtime, run_id: str, expected: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = runtime.get_session(run_id)
        if state is not None and state.status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"Learning run did not reach {expected}")


def test_evidence_gap_waits_then_same_run_resumes_without_knowledge_regeneration() -> None:
    fixture, documents, provider, model, store, runtime = _partial_runtime()

    waiting = runtime.run(fixture.scope)

    assert isinstance(waiting, LearningWaitingEvidenceResult)
    assert waiting.session.status == "waiting_evidence"
    assert waiting.session.error is None
    assert waiting.intervention_report.required_gaps
    assert store.get_latest_version(waiting.session.session_id, "learning_content_plan") is None
    knowledge_ref = waiting.artifacts["knowledge_graph"]
    evidence_v1 = waiting.artifacts["evidence_graph"]
    knowledge_calls = [call["stage"] for call in model.calls[:3]]

    _install_second_document(provider, documents)
    completed = runtime.resume_evidence(waiting.session.session_id)

    assert completed.session.session_id == waiting.session.session_id
    assert completed.session.status == "completed"
    assert completed.quality_report.passed is True
    assert completed.artifacts["knowledge_graph"] == knowledge_ref
    assert completed.artifacts["evidence_graph"].artifact_id == evidence_v1.artifact_id
    assert completed.artifacts["evidence_graph"].version == evidence_v1.version + 1
    assert [call["stage"] for call in model.calls[:3]] == knowledge_calls
    assert [call["stage"] for call in model.calls[3:]] == [
        "learning_evidence_semantics",
        "learning_evidence_semantics",
    ]


def test_true_model_contract_error_is_failed_and_cannot_resume() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    responses = fastapi_pipeline_responses()
    responses[1]["capabilities"][0]["outcomeIndexes"] = [99]
    runtime = LearningRuntime(
        LearningPipeline(
            provider=fixture.provider,
            model=ScriptedPipelineModel(responses),
        ),
        artifact_store=InMemoryArtifactStore(),
    )

    with pytest.raises(LearningRuntimeError) as caught:
        runtime.run(fixture.scope)

    assert caught.value.session.status == "failed"
    assert caught.value.session.error is not None
    assert caught.value.session.error.validator_rule == "generation_contract"
    with pytest.raises(ValueError, match="waiting_evidence"):
        runtime.resume_evidence(caught.value.session.session_id)


def test_waiting_state_recovers_after_runtime_restart_and_resumes() -> None:
    fixture, documents, provider, model, store, runtime = _partial_runtime()
    waiting = runtime.run(fixture.scope)
    assert isinstance(waiting, LearningWaitingEvidenceResult)
    original_knowledge_ref = waiting.artifacts["knowledge_graph"]
    _install_second_document(provider, documents)
    restarted_model = ScriptedPipelineModel([deepcopy(fastapi_pipeline_responses()[3])])
    restarted = LearningRuntime(
        LearningPipeline(provider=provider, model=restarted_model),
        artifact_store=store,
        checkpoint_store=store,
    )

    recovered = restarted.get_session(waiting.session.session_id)
    completed = restarted.resume_evidence(waiting.session.session_id)

    assert recovered is not None
    assert recovered.status == "waiting_evidence"
    assert completed.session.status == "completed"
    assert completed.artifacts["knowledge_graph"] == original_knowledge_ref
    assert [call["stage"] for call in restarted_model.calls] == [
        "learning_evidence_semantics"
    ]


def test_postgres_waiting_state_recovers_and_preserves_artifact_lineage() -> None:
    store_a = PostgresArtifactStore(PostgresLearningArtifactRepository())
    fixture, documents, provider, _model, _store, runtime = _partial_runtime(
        store=store_a,
    )
    waiting = runtime.run(fixture.scope)
    assert isinstance(waiting, LearningWaitingEvidenceResult)
    run_id = waiting.session.session_id
    knowledge_ref = waiting.artifacts["knowledge_graph"]
    evidence_ref = waiting.artifacts["evidence_graph"]
    intervention_ref = waiting.artifacts["evidence_intervention_report"]

    _install_second_document(provider, documents)
    store_b = PostgresArtifactStore(PostgresLearningArtifactRepository())
    restarted_model = ScriptedPipelineModel(
        [deepcopy(fastapi_pipeline_responses()[3])]
    )
    restarted = LearningRuntime(
        LearningPipeline(provider=provider, model=restarted_model),
        artifact_store=store_b,
        checkpoint_store=store_b,
    )

    recovered = restarted.get_session(run_id)
    recovered_intervention = store_b.get_artifact(run_id, intervention_ref)
    completed = restarted.resume_evidence(run_id)

    assert recovered is not None
    assert recovered.status == "waiting_evidence"
    assert recovered_intervention is not None
    assert completed.session.session_id == run_id
    assert completed.session.status == "completed"
    assert completed.artifacts["knowledge_graph"] == knowledge_ref
    assert completed.artifacts["evidence_graph"].artifact_id == evidence_ref.artifact_id
    assert completed.artifacts["evidence_graph"].version == evidence_ref.version + 1
    assert [item.version for item in store_b.list_versions(
        run_id,
        "evidence_graph",
        evidence_ref.artifact_id,
    )] == [1, 2]
    assert [call["stage"] for call in restarted_model.calls] == [
        "learning_evidence_semantics"
    ]


class BlockingMutableVideoProvider(MockVideoProvider):
    def __init__(self, documents):
        super().__init__(documents)
        self.block = False
        self.entered = Event()
        self.release = Event()

    def search(self, query):
        if self.block:
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("resume search was not released")
        return super().search(query)


def test_completed_future_allows_resume_and_concurrent_resume_is_rejected() -> None:
    documents = _documents()
    provider = BlockingMutableVideoProvider([documents[0]])
    fixture, _, _, _, _, runtime = _partial_runtime(provider=provider)
    session = runtime.create_session()
    manager = LearningRunManager(lambda: runtime)
    try:
        manager._start_existing(session.session_id, fixture.scope)
        _wait_for_status(runtime, session.session_id, "waiting_evidence")
        deadline = time.monotonic() + 5
        while not manager._runs[session.session_id].future.done():
            if time.monotonic() >= deadline:
                raise AssertionError("initial waiting Future did not finish")
            time.sleep(0.01)

        _install_second_document(provider, documents)
        provider.block = True
        manager.resume_evidence(session.session_id)
        assert provider.entered.wait(5)
        with pytest.raises((ValueError, RuntimeError)):
            manager.resume_evidence(session.session_id)
        provider.release.set()
        _wait_for_status(runtime, session.session_id, "completed")
    finally:
        provider.release.set()
        manager.shutdown()

    assert runtime.get_session(session.session_id).status == "completed"


def test_typed_resume_evidence_api_continues_the_same_run(client) -> None:
    _fixture, documents, provider, model, _store, runtime = _partial_runtime()
    manager = LearningRunManager(lambda: runtime)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager
    try:
        created = client.post(
            "/api/learning/runs",
            json={
                "goal": "Learn FastAPI and build a CRUD API",
                "preferences": {
                    "target_result": "Build a working FastAPI CRUD API",
                },
                "constraints": [],
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        _wait_for_status(runtime, run_id, "waiting_evidence")
        knowledge_calls = [call["stage"] for call in model.calls[:3]]

        _install_second_document(provider, documents)
        resumed = client.post(f"/api/learning/runs/{run_id}/resume-evidence")
        _wait_for_status(runtime, run_id, "completed")
        result = client.get(f"/api/learning/runs/{run_id}/result")
    finally:
        app.dependency_overrides.pop(get_learning_run_manager, None)
        manager.shutdown()

    assert resumed.status_code == 202
    assert resumed.json()["status"] in {"waiting_evidence", "running", "completed"}
    assert result.status_code == 200
    assert result.json()["learning_quality_report"]["passed"] is True
    assert [call["stage"] for call in model.calls[:3]] == knowledge_calls
