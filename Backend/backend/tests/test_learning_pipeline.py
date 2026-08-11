from __future__ import annotations

import pytest

from app.learning.services import LearningPipeline, LearningPipelineError
from app.learning.validators import LearningArtifactValidator

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


def test_fastapi_scope_runs_the_complete_validated_learning_pipeline() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()

    result = LearningPipeline(
        provider=fixture.provider,
        model=fixture.model,
    ).run(fixture.scope)

    assert [item["stage"] for item in fixture.model.calls] == [
        "learning_outcomes",
        "learning_capabilities",
        "learning_knowledge",
        "learning_evidence_semantics",
    ]
    assert fixture.provider.search_calls == 1
    assert len(fixture.provider.fetch_calls) == 2
    assert {item.name for item in result.capability_graph.capabilities} == {
        "API Design",
        "Data Validation",
        "Persistence",
    }
    assert {item.name for item in result.knowledge_graph.nodes} == {
        "Routing",
        "Pydantic",
        "Database",
        "CRUD",
    }
    assert len(result.content_selection.selected_segments) == 2
    assert result.content_selection.total_duration_seconds == 1200
    assert result.learning_content_plan.total_duration_seconds == 1200
    assert result.quality_report.quality_checks
    assert result.quality_report.passed is True

    validated = LearningArtifactValidator().validate_chain(
        scope=result.scope,
        capability_graph=result.capability_graph,
        knowledge_graph=result.knowledge_graph,
        evidence_graph=result.evidence_graph,
        content_selection=result.content_selection,
        content_plan=result.learning_content_plan,
        quality_report=result.quality_report,
    )
    assert validated.content_plan == result.learning_content_plan


def test_pipeline_failure_is_structured_and_stops_before_evidence() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    responses = fastapi_pipeline_responses()
    responses[1]["capabilities"][0]["outcomeIndexes"] = [9]
    model = ScriptedPipelineModel(responses)

    with pytest.raises(LearningPipelineError) as caught:
        LearningPipeline(provider=fixture.provider, model=model).run(fixture.scope)

    error = caught.value.as_dict()
    assert error["stage"] == "learning_capabilities"
    assert error["artifact_type"] == "capability_graph"
    assert error["validator_rule"] == "generation_contract"
    assert error["field_path"]
    assert "available range" in error["message"]
    assert [item["stage"] for item in model.calls] == [
        "learning_outcomes",
        "learning_capabilities",
    ]
    assert fixture.provider.search_calls == 0


def test_pipeline_model_only_supplies_semantics() -> None:
    fixture = build_fastapi_learning_pipeline_fixture()

    LearningPipeline(provider=fixture.provider, model=fixture.model).run(fixture.scope)

    forbidden = {"artifactId", "version", "createdAt", "contentFingerprint"}
    for call in fixture.model.calls:
        assert not (forbidden & set(call["payload"]))
