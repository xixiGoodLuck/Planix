from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.learning.contracts import ContentSelection
from app.learning.selection.services import (
    ContentSelector,
    CoverageAnalyzer,
    PlanComposer,
    RedundancyAnalyzer,
)
from app.learning.selection.validators import (
    ContentSelectionValidator,
    SelectionValidationReport,
)
from app.learning.validators import LearningArtifactValidationError

from learning_selection_fixtures import (
    FastApiSelectionFixture,
    build_fastapi_selection_fixture,
)


def _complete_selection(fixture: FastApiSelectionFixture | None = None):
    fixture = fixture or build_fastapi_selection_fixture()
    selection_result = ContentSelector().select(
        fixture.knowledge_graph,
        fixture.evidence_graph,
    )
    validator = ContentSelectionValidator()
    validated_selection = validator.validate_selection(
        fixture.scope,
        fixture.knowledge_graph,
        fixture.evidence_graph,
        selection_result.content_selection,
    )
    plan = PlanComposer().compose(
        fixture.scope,
        fixture.knowledge_graph,
        fixture.evidence_graph,
        validated_selection.content_selection,
    )
    validated_plan = validator.validate_plan(
        fixture.scope,
        fixture.knowledge_graph,
        fixture.evidence_graph,
        validated_selection.content_selection,
        plan,
    )
    return fixture, selection_result, validated_selection, validated_plan


def test_complete_required_knowledge_coverage_passes() -> None:
    fixture, selection_result, validated, _ = _complete_selection()
    coverage = CoverageAnalyzer().analyze(
        fixture.knowledge_graph,
        fixture.evidence_graph,
    )

    assert all(item.covered and item.sufficient for item in coverage.knowledge)
    assert validated.report.required_coverage_complete is True
    assert validated.report.passed is True
    assert selection_result.content_selection.coverage_gaps == []


def test_two_segments_are_selected_and_composed_into_a_plan() -> None:
    _, _, validated, plan = _complete_selection()

    assert [item.segment_id for item in validated.content_selection.selected_segments] == [
        "segment-a-core",
        "segment-b-core",
    ]
    assert len(plan.items) == 4
    assert all(len(item.recommended_content) == 1 for item in plan.items)


def test_total_duration_is_calculated_from_selected_segments() -> None:
    _, selection_result, validated, plan = _complete_selection()

    assert selection_result.content_selection.total_duration_seconds == 0
    assert validated.content_selection.total_duration_seconds == 1200
    assert plan.total_duration_seconds == 1200


def test_recommendation_facts_are_generated_from_selection_rules() -> None:
    _, _, validated, plan = _complete_selection()
    selected_a = validated.content_selection.selected_segments[0]
    facts = selected_a.selection_facts

    assert facts is not None
    assert facts.evidence_level == "transcript"
    assert facts.version_compatible is True
    assert facts.saved_minutes == 5
    assert [(item.segment_id, item.reason) for item in facts.alternative_rejected] == [
        ("segment-a-duplicate", "duplicate_content")
    ]
    plan_facts = plan.items[0].recommended_content[0].selection_facts
    assert plan_facts == facts


def test_plan_duration_is_projected_without_copying_video_timestamps() -> None:
    fixture, _, _, plan = _complete_selection()
    segments = {item.id: item for item in fixture.evidence_graph.segments}
    payload = json.dumps(plan.model_dump(by_alias=True), ensure_ascii=False)

    assert "startSeconds" not in payload
    assert "endSeconds" not in payload
    for item in plan.items:
        for recommendation in item.recommended_content:
            segment = segments[recommendation.segment_id]
            assert recommendation.duration_seconds == (
                segment.end_seconds - segment.start_seconds
            )


def test_selection_with_forged_segment_fails() -> None:
    fixture, selection_result, _, _ = _complete_selection()
    selection = selection_result.content_selection
    forged = selection.selected_segments[0].model_copy(
        update={"segment_id": "segment-does-not-exist"}
    )
    invalid = selection.model_copy(
        update={"selected_segments": [forged, *selection.selected_segments[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="selection_segment_reference"):
        ContentSelectionValidator().validate_selection(
            fixture.scope,
            fixture.knowledge_graph,
            fixture.evidence_graph,
            invalid,
        )


def test_selection_cannot_submit_a_forged_time_range() -> None:
    _, selection_result, _, _ = _complete_selection()
    payload = selection_result.content_selection.model_dump(by_alias=True)
    payload["selectedSegments"][0]["startSeconds"] = 1
    payload["selectedSegments"][0]["endSeconds"] = 2

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContentSelection.model_validate(payload)


def test_missing_required_knowledge_creates_a_real_gap() -> None:
    fixture = build_fastapi_selection_fixture()
    crud_id = next(
        item.id for item in fixture.knowledge_graph.nodes if item.name == "CRUD"
    )
    evidence_graph = fixture.evidence_graph.model_copy(
        update={
            "coverage_edges": [
                edge
                for edge in fixture.evidence_graph.coverage_edges
                if edge.knowledge_id != crud_id
            ]
        }
    )
    selection = ContentSelector().select(fixture.knowledge_graph, evidence_graph)
    validated = ContentSelectionValidator().validate_selection(
        fixture.scope,
        fixture.knowledge_graph,
        evidence_graph,
        selection.content_selection,
    )

    assert [item.knowledge_id for item in validated.content_selection.coverage_gaps] == [
        crud_id
    ]
    assert validated.content_selection.coverage_gaps[0].impact == "blocker"
    assert validated.report.passed is False


def test_plan_cannot_reference_an_unselected_segment() -> None:
    fixture, _, validated, plan = _complete_selection()
    first_item = plan.items[0]
    forged_recommendation = first_item.recommended_content[0].model_copy(
        update={"segment_id": "segment-a-duplicate"}
    )
    forged_item = first_item.model_copy(
        update={"recommended_content": [forged_recommendation]}
    )
    forged_plan = plan.model_copy(update={"items": [forged_item, *plan.items[1:]]})

    with pytest.raises(LearningArtifactValidationError, match="plan_selection_reference"):
        ContentSelectionValidator().validate_plan(
            fixture.scope,
            fixture.knowledge_graph,
            fixture.evidence_graph,
            validated.content_selection,
            forged_plan,
        )


def test_incomplete_coverage_cannot_force_validation_pass() -> None:
    fixture = build_fastapi_selection_fixture()
    crud_id = next(
        item.id for item in fixture.knowledge_graph.nodes if item.name == "CRUD"
    )
    evidence_graph = fixture.evidence_graph.model_copy(
        update={
            "coverage_edges": [
                edge
                for edge in fixture.evidence_graph.coverage_edges
                if edge.knowledge_id != crud_id
            ]
        }
    )
    selection = ContentSelector().select(fixture.knowledge_graph, evidence_graph)
    report = ContentSelectionValidator().validate_selection(
        fixture.scope,
        fixture.knowledge_graph,
        evidence_graph,
        selection.content_selection,
    ).report
    payload = report.model_dump(by_alias=True, exclude={"passed"})
    payload["passed"] = True

    assert report.passed is False
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SelectionValidationReport.model_validate(payload)


def test_redundant_segment_is_identified_and_not_selected() -> None:
    fixture, selection_result, validated, _ = _complete_selection()
    redundancy = RedundancyAnalyzer().analyze(
        fixture.knowledge_graph,
        fixture.evidence_graph,
    )
    decisions = {item.segment_id: item for item in redundancy.segments}

    assert decisions["segment-a-duplicate"].classification == "REDUNDANT"
    assert decisions["segment-a-duplicate"].duplicate_of == "segment-a-core"
    assert "segment-a-duplicate" not in {
        item.segment_id for item in validated.content_selection.selected_segments
    }
    assert selection_result.redundancy_report == redundancy


def test_context_required_segment_is_kept_even_when_it_duplicates_content() -> None:
    fixture = build_fastapi_selection_fixture()
    segment_b = next(
        item for item in fixture.evidence_graph.segments if item.id == "segment-b-core"
    )
    segment_b = segment_b.model_copy(
        update={"context_segment_refs": ["segment-a-duplicate"]}
    )
    evidence_graph = fixture.evidence_graph.model_copy(
        update={
            "segments": [
                segment_b if item.id == segment_b.id else item
                for item in fixture.evidence_graph.segments
            ]
        }
    )

    result = ContentSelector().select(fixture.knowledge_graph, evidence_graph)
    selected_ids = [item.segment_id for item in result.content_selection.selected_segments]
    decisions = {item.segment_id: item for item in result.redundancy_report.segments}

    assert selected_ids == ["segment-a-core", "segment-a-duplicate", "segment-b-core"]
    assert decisions["segment-a-duplicate"].classification == "CONTEXT_REQUIRED"
