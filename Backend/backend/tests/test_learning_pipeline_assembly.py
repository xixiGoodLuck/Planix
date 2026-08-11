from __future__ import annotations

from app.learning.assembly import LearningPipelineRequest, LearningPipelineRunner
from app.learning.evidence.providers import MockVideoProvider
from app.learning.evidence.transcript import MockTranscriptProvider
from app.learning.quality import LearningQualityEngine

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


class FailingGapCompletionOrchestrator:
    def run(self, knowledge_graph, evidence_graph, coverage_report, provider):
        raise RuntimeError("gap completion fixture failure")


class FailingQualityEngine:
    def __init__(self):
        self.delegate = LearningQualityEngine()

    def evaluate(self, **kwargs):
        report = self.delegate.evaluate(**kwargs)
        return report.model_copy(update={"hard_rules_passed": False})


def _request(scope):
    return LearningPipelineRequest(
        scope=scope,
        providerKey="fixture:fastapi-learning",
    )


def _successful_run():
    fixture = build_fastapi_learning_pipeline_fixture()
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=fixture.model,
    )
    request = _request(fixture.scope)
    result = runner.run(request, fixture.provider)
    return fixture, runner, request, result


def test_complete_fastapi_offline_pipeline_returns_final_plan() -> None:
    fixture, _, _, result = _successful_run()

    assert result.status == "completed"
    assert result.error is None
    assert result.final_plan is not None
    assert result.quality_report is not None
    assert result.final_plan.items
    assert [item["stage"] for item in fixture.model.calls] == [
        "learning_outcomes",
        "learning_capabilities",
        "learning_knowledge",
        "learning_evidence_semantics",
    ]


def test_artifact_lineage_contains_scope_to_quality_chain() -> None:
    _, _, _, result = _successful_run()
    assert result.final_plan is not None
    assert result.quality_report is not None
    artifact_types = {item.artifact_type for item in result.artifact_refs}

    assert {
        "learning_scope",
        "capability_graph",
        "knowledge_graph",
        "evidence_graph",
        "coverage_report",
        "content_selection",
        "learning_content_plan",
        "learning_quality_report",
    } <= artifact_types
    assert (
        result.final_plan.evidence_graph_ref
        == result.quality_report.evidence_graph_ref
    )
    assert (
        result.final_plan.content_selection_ref
        == result.quality_report.content_selection_ref
    )


def test_final_quality_report_passes_code_owned_gate() -> None:
    _, _, _, result = _successful_run()

    assert result.quality_report is not None
    assert result.quality_report.hard_rules_passed is True
    assert result.quality_report.passed is True
    assert result.quality_report.issues == []


def test_repeated_run_is_idempotent_and_does_not_call_pipeline_twice() -> None:
    fixture, runner, request, first = _successful_run()
    model_calls = len(fixture.model.calls)
    provider_search_calls = fixture.provider.search_calls
    provider_fetch_calls = list(fixture.provider.fetch_calls)

    second = runner.run(request, fixture.provider)

    assert second == first
    assert second.run_id == first.run_id
    assert second.run_fingerprint == first.run_fingerprint
    assert len(fixture.model.calls) == model_calls
    assert fixture.provider.search_calls == provider_search_calls
    assert fixture.provider.fetch_calls == provider_fetch_calls


def test_knowledge_failure_stops_with_stage_error() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    model = ScriptedPipelineModel([{"outcomes": []}])
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=model,
    )

    result = runner.run(_request(fixture.scope), fixture.provider)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.stage == "knowledge_generation"
    assert result.final_plan is None
    assert result.quality_report is None
    assert fixture.provider.search_calls == 0


def test_evidence_failure_stops_before_gap_completion() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    model = ScriptedPipelineModel(fastapi_pipeline_responses()[:3])
    provider = MockVideoProvider([])
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=model,
    )

    result = runner.run(_request(fixture.scope), provider)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.stage == "evidence_generation"
    assert result.final_plan is None
    assert provider.search_calls == 1


def test_gap_completion_failure_stops_before_selection() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=fixture.model,
        gap_orchestrator=FailingGapCompletionOrchestrator(),
    )

    result = runner.run(_request(fixture.scope), fixture.provider)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.stage == "gap_completion"
    assert result.final_plan is None
    assert result.quality_report is None


def test_quality_failure_cannot_return_completed() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=fixture.model,
        quality_engine=FailingQualityEngine(),
    )

    result = runner.run(_request(fixture.scope), fixture.provider)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.stage == "quality_evaluation"
    assert result.final_plan is not None
    assert result.quality_report is not None
    assert result.quality_report.passed is False
