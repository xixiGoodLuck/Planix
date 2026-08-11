from __future__ import annotations

from app.learning.contracts import (
    CoverageGap,
    SelectedSegment,
    SelectionFacts,
)
from app.learning.quality import LearningQualityEngine
from app.learning.selection.services import RedundancyAnalyzer
from app.learning.services import LearningPipeline

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture


def _result():
    fixture = build_fastapi_learning_pipeline_fixture()
    return LearningPipeline(provider=fixture.provider, model=fixture.model).run(fixture.scope)


def _evaluate(result, *, evidence_graph=None, content_selection=None, plan=None):
    return LearningQualityEngine().evaluate(
        scope=result.scope,
        capability_graph=result.capability_graph,
        knowledge_graph=result.knowledge_graph,
        evidence_graph=evidence_graph or result.evidence_graph,
        content_selection=content_selection or result.content_selection,
        learning_content_plan=plan or result.learning_content_plan,
    )


def test_valid_learning_chain_has_code_computed_quality_pass() -> None:
    result = _result()

    report = _evaluate(result)

    assert report.hard_rules_passed is True
    assert report.quality_checks
    assert all(item.passed for item in report.quality_checks)
    assert report.issues == []
    assert report.passed is True


def test_required_knowledge_without_evidence_fails_quality() -> None:
    result = _result()
    crud_id = next(item.id for item in result.knowledge_graph.nodes if item.name == "CRUD")
    graph = result.evidence_graph.model_copy(
        update={
            "coverage_edges": [
                item
                for item in result.evidence_graph.coverage_edges
                if item.knowledge_id != crud_id
            ]
        }
    )

    report = _evaluate(result, evidence_graph=graph)

    assert report.passed is False
    assert any(
        item.rule == "knowledge_coverage" and crud_id in item.evidence
        for item in report.quality_checks
        if not item.passed
    )


def test_selection_missing_required_knowledge_fails_quality() -> None:
    result = _result()
    selected = result.content_selection.selected_segments[0]
    retained_knowledge = set(selected.knowledge_refs)
    missing = [
        item.id for item in result.knowledge_graph.nodes if item.id not in retained_knowledge
    ]
    segment = next(
        item for item in result.evidence_graph.segments if item.id == selected.segment_id
    )
    selection = result.content_selection.model_copy(
        update={
            "selected_segments": [selected],
            "coverage_gaps": [
                CoverageGap(
                    knowledgeId=knowledge_id,
                    reason="No selected full coverage remains.",
                    impact="blocker",
                )
                for knowledge_id in missing
            ],
            "total_duration_seconds": segment.end_seconds - segment.start_seconds,
        }
    )

    report = _evaluate(result, content_selection=selection)

    assert report.passed is False
    assert any(
        item.rule == "knowledge_coverage" and not item.passed
        for item in report.quality_checks
    )


def test_plan_reference_to_unselected_segment_fails_quality() -> None:
    result = _result()
    original_item = result.learning_content_plan.items[0]
    original_recommendation = original_item.recommended_content[0]
    bad_recommendation = original_recommendation.model_copy(
        update={"selection_id": "selection-not-present"}
    )
    bad_item = original_item.model_copy(
        update={"recommended_content": [bad_recommendation]}
    )
    plan = result.learning_content_plan.model_copy(
        update={"items": [bad_item, *result.learning_content_plan.items[1:]]}
    )

    report = _evaluate(result, plan=plan)

    assert report.passed is False
    assert any("only reference current ContentSelection" in item.description for item in report.issues)


def test_resource_fingerprint_change_fails_quality() -> None:
    result = _result()
    changed = result.evidence_graph.resources[0].model_copy(
        update={"content_fingerprint": "sha256:changed-resource"}
    )
    graph = result.evidence_graph.model_copy(
        update={"resources": [changed, *result.evidence_graph.resources[1:]]}
    )

    report = _evaluate(result, evidence_graph=graph)

    assert report.passed is False
    assert any(
        item.rule == "version_compatibility" and not item.passed
        for item in report.quality_checks
    )


def test_unsupported_timestamp_fails_quality() -> None:
    result = _result()
    original = result.evidence_graph.segments[0]
    resource = next(
        item for item in result.evidence_graph.resources if item.id == original.resource_id
    )
    changed = original.model_copy(update={"end_seconds": resource.duration_seconds + 1})
    graph = result.evidence_graph.model_copy(
        update={"segments": [changed, *result.evidence_graph.segments[1:]]}
    )

    report = _evaluate(result, evidence_graph=graph)

    assert report.passed is False
    assert any(
        item.rule == "unsupported_timestamp" and not item.passed
        for item in report.quality_checks
    )


def test_redundant_segment_in_selection_fails_quality() -> None:
    result = _result()
    decisions = RedundancyAnalyzer().analyze(
        result.knowledge_graph,
        result.evidence_graph,
    )
    redundant_id = next(
        item.segment_id for item in decisions.segments if item.classification == "REDUNDANT"
    )
    segment = next(item for item in result.evidence_graph.segments if item.id == redundant_id)
    edges = [
        item for item in result.evidence_graph.coverage_edges if item.segment_id == redundant_id
    ]
    evidence_refs = list(dict.fromkeys(item_id for edge in edges for item_id in edge.evidence_refs))
    selected = SelectedSegment(
        id="selection-redundant",
        segmentId=redundant_id,
        knowledgeRefs=list(dict.fromkeys(item.knowledge_id for item in edges)),
        coverageEdgeRefs=[item.id for item in edges],
        evidenceRefs=evidence_refs,
        viewingOrder=len(result.content_selection.selected_segments),
        selectionReason="Deliberately selected redundant fixture segment.",
        selectionFacts=SelectionFacts(
            knowledgeCovered=list(dict.fromkeys(item.knowledge_id for item in edges)),
            evidenceLevel="transcript",
            versionCompatible=True,
            selectionRuleRefs=["fixture"],
        ),
    )
    selection = result.content_selection.model_copy(
        update={
            "selected_segments": [*result.content_selection.selected_segments, selected],
            "total_duration_seconds": (
                result.content_selection.total_duration_seconds
                + segment.end_seconds
                - segment.start_seconds
            ),
        }
    )

    report = _evaluate(result, content_selection=selection)

    assert report.passed is False
    assert any(
        item.rule == "content_redundancy" and redundant_id in item.evidence
        for item in report.quality_checks
        if not item.passed
    )


def test_coverage_gap_cannot_overlap_selected_knowledge() -> None:
    result = _result()
    selected_knowledge = result.content_selection.selected_segments[0].knowledge_refs[0]
    selection = result.content_selection.model_copy(
        update={
            "coverage_gaps": [
                CoverageGap(
                    knowledgeId=selected_knowledge,
                    reason="False gap for already covered knowledge.",
                    impact="major",
                )
            ]
        }
    )

    report = _evaluate(result, content_selection=selection)

    assert report.passed is False
    assert any(
        "genuinely unavailable verified coverage" in item.description
        for item in report.issues
    )
