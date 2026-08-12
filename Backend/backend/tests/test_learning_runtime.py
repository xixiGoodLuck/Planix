from __future__ import annotations

import pytest

from app.learning.quality import LearningQualityEngine
from app.learning.runtime import (
    InMemoryArtifactStore,
    LearningRuntime,
    LearningRuntimeError,
)
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


def _runtime(*, quality_engine=None):
    fixture = build_fastapi_learning_pipeline_fixture()
    store = InMemoryArtifactStore()
    pipeline = LearningPipeline(
        provider=fixture.provider,
        model=fixture.model,
        quality_engine=quality_engine,
    )
    return fixture, store, LearningRuntime(pipeline, artifact_store=store)


def test_runtime_executes_the_complete_phase_one_to_five_pipeline() -> None:
    fixture, _, runtime = _runtime()

    result = runtime.run(fixture.scope)

    assert result.session.status == "completed"
    assert result.session.current_stage == "completed"
    assert result.session.completed_stages == [
        "scope",
        "knowledge_generation",
        "evidence_generation",
        "coverage_analysis",
        "gap_completion",
        "selection",
        "quality",
    ]
    assert result.final_plan.total_duration_seconds == 1200
    assert result.quality_report.passed is True
    assert set(result.artifacts) == {
        "learning_scope",
        "capability_graph",
        "knowledge_graph",
        "evidence_graph",
        "content_selection",
        "learning_content_plan",
        "learning_quality_report",
    }


def test_runtime_emits_each_stage_state_change() -> None:
    fixture, _, runtime = _runtime()

    result = runtime.run(fixture.scope)
    events = runtime.get_events(result.session.session_id)

    assert [item.stage for item in events if item.event_type == "stage_started"] == [
        "scope",
        "knowledge_generation",
        "evidence_generation",
        "coverage_analysis",
        "gap_completion",
        "selection",
        "quality",
    ]
    assert [item.stage for item in events if item.event_type == "stage_completed"] == [
        "scope",
        "knowledge_generation",
        "evidence_generation",
        "coverage_analysis",
        "gap_completion",
        "selection",
        "quality",
    ]
    serialized = "".join(item.model_dump_json() for item in events).casefold()
    assert all(item not in serialized for item in ("prompt", "token", "reasoning"))


def test_runtime_stops_at_the_failed_pipeline_stage() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    responses = fastapi_pipeline_responses()
    responses[1]["capabilities"][0]["outcomeIndexes"] = [99]
    model = ScriptedPipelineModel(responses)
    runtime = LearningRuntime(
        LearningPipeline(provider=fixture.provider, model=model),
        artifact_store=InMemoryArtifactStore(),
    )

    with pytest.raises(LearningRuntimeError) as caught:
        runtime.run(fixture.scope)

    session = caught.value.session
    assert session.status == "failed"
    assert session.current_stage == "failed"
    assert session.error is not None
    assert session.error.stage == "knowledge_generation"
    assert session.error.validator_rule == "generation_contract"
    assert session.completed_stages == ["scope"]
    assert fixture.provider.search_calls == 0
    assert runtime.get_events(session.session_id)[-1].event_type == "session_failed"


def test_runtime_artifact_refs_resolve_to_the_saved_versions() -> None:
    fixture, store, runtime = _runtime()

    result = runtime.run(fixture.scope)

    for artifact_type, ref in result.artifacts.items():
        artifact = store.get_artifact(result.session.session_id, ref)
        latest = store.get_latest_version(result.session.session_id, artifact_type)
        assert artifact is not None
        assert artifact.artifact_id == ref.artifact_id
        assert artifact.version == ref.version
        assert latest == ref
    assert result.session.current_artifact_ref == result.artifacts["learning_quality_report"]


def test_runtime_progress_event_order_is_deterministic() -> None:
    fixture, _, runtime = _runtime()

    result = runtime.run(fixture.scope)

    assert [item.event_type for item in runtime.get_events(result.session.session_id)] == [
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
        "stage_completed",
        "stage_started",
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


class RejectingQualityEngine:
    def __init__(self):
        self.delegate = LearningQualityEngine()

    def evaluate(self, **kwargs):
        report = self.delegate.evaluate(**kwargs)
        return report.model_copy(update={"hard_rules_passed": False})


def test_quality_failure_never_returns_a_success_result() -> None:
    fixture, store, runtime = _runtime(quality_engine=RejectingQualityEngine())

    with pytest.raises(LearningRuntimeError) as caught:
        runtime.run(fixture.scope)

    session = caught.value.session
    assert session.status == "failed"
    assert session.error is not None
    assert session.error.stage == "quality"
    assert session.error.validator_rule == "quality_gate"
    assert (
        store.get_latest_version(session.session_id, "learning_quality_report")
        is not None
    )
    assert all(
        item.event_type != "session_completed"
        for item in runtime.get_events(session.session_id)
    )
