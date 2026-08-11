from __future__ import annotations

import inspect

import pytest

from app.learning.contracts import ContentBudget, CoverageGap, SelectionOmission
from app.learning.quality.validators import SelectionQualityValidator
from app.learning.selection import (
    ContentSelectionValidator,
    ContentSelector,
    PlanComposer,
    marginal_duration_seconds,
    range_union_duration_seconds,
    resolve_selected_knowledge_coverage,
)
from app.learning.validators import LearningArtifactValidationError

from learning_selection_fixtures import build_fastapi_selection_fixture
from learning_fixtures import build_fastapi_crud_learning_fixture


def _fixture_with_importance(
    *,
    segment_a_importance: str = "required",
    segment_b_importance: str = "required",
    maximum_minutes: int | None = None,
):
    fixture = build_fastapi_selection_fixture()
    edge_by_segment: dict[str, set[str]] = {}
    for edge in fixture.evidence_graph.coverage_edges:
        edge_by_segment.setdefault(edge.segment_id, set()).add(edge.knowledge_id)
    segment_a_ids = edge_by_segment["segment-a-core"]
    segment_b_ids = edge_by_segment["segment-b-core"]
    nodes = []
    for node in fixture.knowledge_graph.nodes:
        importance = node.importance
        if node.id in segment_a_ids:
            importance = segment_a_importance
        if node.id in segment_b_ids:
            importance = segment_b_importance
        nodes.append(node.model_copy(update={"importance": importance}))
    scope = fixture.scope.model_copy(
        update={
            "content_budget": ContentBudget(
                maximumTotalMinutes=maximum_minutes,
            )
        }
    )
    knowledge_graph = fixture.knowledge_graph.model_copy(update={"nodes": nodes})
    return fixture, scope, knowledge_graph, segment_a_ids, segment_b_ids


def test_selected_segment_projects_every_full_knowledge_edge() -> None:
    fixture = build_fastapi_selection_fixture()
    result = ContentSelector().select(
        fixture.knowledge_graph,
        fixture.evidence_graph,
    )
    resolved = resolve_selected_knowledge_coverage(
        fixture.evidence_graph,
        [item.segment_id for item in result.content_selection.selected_segments],
    )

    declared = {
        knowledge_id
        for item in result.content_selection.selected_segments
        for knowledge_id in item.knowledge_refs
    }
    assert declared == set(resolved.selected_knowledge_ids)
    assert len(declared) == 4


def test_important_zero_cost_coverage_is_selected_automatically() -> None:
    fixture, scope, graph, segment_a_ids, _ = _fixture_with_importance(
        segment_a_importance="important"
    )
    first_id = sorted(segment_a_ids)[0]
    nodes = [
        node.model_copy(update={"importance": "required"})
        if node.id == first_id
        else node
        for node in graph.nodes
    ]
    graph = graph.model_copy(update={"nodes": nodes})

    selection = ContentSelector().select(graph, fixture.evidence_graph, scope=scope)
    selected = {
        knowledge_id
        for item in selection.content_selection.selected_segments
        for knowledge_id in item.knowledge_refs
    }

    assert segment_a_ids <= selected
    assert not (segment_a_ids & {
        item.knowledge_id for item in selection.content_selection.selection_omissions
    })


def test_important_full_coverage_is_selected_when_budget_allows() -> None:
    fixture, scope, graph, _, segment_b_ids = _fixture_with_importance(
        segment_b_importance="important",
        maximum_minutes=30,
    )

    selection = ContentSelector().select(graph, fixture.evidence_graph, scope=scope)
    selected = {
        knowledge_id
        for item in selection.content_selection.selected_segments
        for knowledge_id in item.knowledge_refs
    }

    assert segment_b_ids <= selected
    assert selection.content_selection.selection_omissions == []


def test_important_full_coverage_over_budget_is_omitted_not_gapped() -> None:
    fixture, scope, graph, _, segment_b_ids = _fixture_with_importance(
        segment_b_importance="important",
        maximum_minutes=10,
    )

    result = ContentSelector().select(graph, fixture.evidence_graph, scope=scope)
    validated = ContentSelectionValidator().validate_selection(
        scope,
        graph,
        fixture.evidence_graph,
        result.content_selection,
    )
    plan = PlanComposer().compose(
        scope,
        graph,
        fixture.evidence_graph,
        validated.content_selection,
    )
    plan = ContentSelectionValidator().validate_plan(
        scope,
        graph,
        fixture.evidence_graph,
        validated.content_selection,
        plan,
    )

    assert {item.knowledge_id for item in plan.deferred_knowledge} == segment_b_ids
    assert all(item.reason == "budget_limit" for item in plan.deferred_knowledge)
    assert plan.evidence_gaps == []
    assert all(
        item.uncovered_reason is None
        for item in plan.items
        if item.knowledge_id in segment_b_ids
    )


def test_optional_full_coverage_uses_scope_omission() -> None:
    fixture, scope, graph, _, segment_b_ids = _fixture_with_importance(
        segment_b_importance="optional"
    )
    scope = scope.model_copy(
        update={
            "user_goal": "Understand the core backend concepts",
            "target_result": "Explain the core concepts",
        }
    )

    selection = ContentSelector().select(graph, fixture.evidence_graph, scope=scope)

    assert selection.content_selection.coverage_gaps == []
    assert {item.knowledge_id for item in selection.content_selection.selection_omissions} == segment_b_ids
    assert all(
        item.reason == "not_required_by_scope"
        for item in selection.content_selection.selection_omissions
    )


def test_required_full_coverage_not_selected_fails_quality() -> None:
    fixture = build_fastapi_selection_fixture()
    result = ContentSelector().select(
        fixture.knowledge_graph,
        fixture.evidence_graph,
    ).content_selection
    retained = [
        item for item in result.selected_segments if item.segment_id == "segment-a-core"
    ]
    missing_ids = {
        edge.knowledge_id
        for edge in fixture.evidence_graph.coverage_edges
        if edge.segment_id == "segment-b-core"
    }
    forged = result.model_copy(
        update={
            "selected_segments": retained,
            "coverage_gaps": [
                CoverageGap(
                    knowledgeId=knowledge_id,
                    reason="forged gap",
                    impact="blocker",
                )
                for knowledge_id in sorted(missing_ids)
            ],
            "total_duration_seconds": 0,
        }
    )

    evaluation = SelectionQualityValidator().evaluate(
        fixture.scope,
        fixture.knowledge_graph,
        fixture.evidence_graph,
        forged,
    )

    assert any(
        check.rule == "knowledge_coverage" and not check.passed
        for check in evaluation.checks
    )
    assert any(issue.severity == "blocker" for issue in evaluation.issues)


@pytest.mark.parametrize("strength", ["partial", None])
def test_non_full_evidence_remains_a_coverage_gap(strength: str | None) -> None:
    fixture = build_fastapi_selection_fixture()
    target = next(
        edge
        for edge in fixture.evidence_graph.coverage_edges
        if edge.segment_id == "segment-b-core"
    )
    edges = [
        edge
        for edge in fixture.evidence_graph.coverage_edges
        if edge.knowledge_id != target.knowledge_id
    ]
    if strength is not None:
        edges.append(target.model_copy(update={"coverage_strength": strength}))
    evidence_graph = fixture.evidence_graph.model_copy(update={"coverage_edges": edges})

    result = ContentSelector().select(fixture.knowledge_graph, evidence_graph)
    gap = next(
        item
        for item in result.content_selection.coverage_gaps
        if item.knowledge_id == target.knowledge_id
    )

    assert gap.impact == "blocker"
    assert target.knowledge_id not in {
        item.knowledge_id for item in result.content_selection.selection_omissions
    }


def test_full_evidence_forged_as_gap_is_rejected() -> None:
    fixture, scope, graph, _, segment_b_ids = _fixture_with_importance(
        segment_b_importance="important"
    )
    selection = ContentSelector().select(
        graph,
        fixture.evidence_graph,
        scope=scope,
    ).content_selection
    target_id = sorted(segment_b_ids)[0]
    forged = selection.model_copy(
        update={
            "selection_omissions": [
                item
                for item in selection.selection_omissions
                if item.knowledge_id != target_id
            ],
            "coverage_gaps": [
                CoverageGap(
                    knowledgeId=target_id,
                    reason="forged gap",
                    impact="major",
                )
            ],
        }
    )

    with pytest.raises(LearningArtifactValidationError, match="coverage_gap_truth"):
        ContentSelectionValidator().validate_selection(
            scope,
            graph,
            fixture.evidence_graph,
            forged,
        )


def test_selected_knowledge_cannot_also_be_an_omission() -> None:
    fixture, scope, graph, segment_a_ids, _ = _fixture_with_importance(
        segment_a_importance="important"
    )
    required_id, omitted_id = sorted(segment_a_ids)
    graph = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"importance": "required"})
                if node.id == required_id
                else node
                for node in graph.nodes
            ]
        }
    )
    selection = ContentSelector().select(
        graph,
        fixture.evidence_graph,
        scope=scope,
    ).content_selection
    forged = selection.model_copy(
        update={
            "selection_omissions": [
                SelectionOmission(
                    knowledgeId=omitted_id,
                    importance="important",
                    reason="lower_priority",
                    candidateSegmentRefs=["segment-a-core"],
                    marginalDurationSeconds=0,
                    policyRuleRefs=["minimum_sufficient_selection"],
                    description="forged omission",
                )
            ]
        }
    )

    with pytest.raises(LearningArtifactValidationError, match="selection_partition"):
        ContentSelectionValidator().validate_selection(
            scope,
            graph,
            fixture.evidence_graph,
            forged,
        )


def test_budget_omission_rejects_false_marginal_duration() -> None:
    fixture, scope, graph, _, _ = _fixture_with_importance(
        segment_b_importance="important",
        maximum_minutes=10,
    )
    selection = ContentSelector().select(
        graph,
        fixture.evidence_graph,
        scope=scope,
    ).content_selection
    first = selection.selection_omissions[0]
    forged = selection.model_copy(
        update={
            "selection_omissions": [
                first.model_copy(update={"marginal_duration_seconds": 0}),
                *selection.selection_omissions[1:],
            ]
        }
    )

    with pytest.raises(LearningArtifactValidationError, match="selection_omission_duration"):
        ContentSelectionValidator().validate_selection(
            scope,
            graph,
            fixture.evidence_graph,
            forged,
        )


def test_overlapping_ranges_are_counted_once() -> None:
    fixture = build_fastapi_selection_fixture()
    segment = next(
        item for item in fixture.evidence_graph.segments if item.id == "segment-a-core"
    )
    overlap = segment.model_copy(
        update={
            "id": "segment-overlap",
            "start_seconds": 900,
            "end_seconds": 1500,
        }
    )
    evidence_graph = fixture.evidence_graph.model_copy(
        update={"segments": [*fixture.evidence_graph.segments, overlap]}
    )

    assert range_union_duration_seconds(
        evidence_graph,
        ["segment-a-core", "segment-overlap"],
    ) == 900
    assert marginal_duration_seconds(
        evidence_graph,
        ["segment-a-core"],
        ["segment-overlap"],
    ) == 300


def test_selector_contains_no_product_specific_knowledge_names() -> None:
    source = inspect.getsource(ContentSelector)

    assert "APIRouter" not in source
    assert "FastAPI" not in source


def test_quality_score_cannot_override_a_hard_rule_failure() -> None:
    report = build_fastapi_crud_learning_fixture().quality_report.model_copy(
        update={"hard_rules_passed": False, "score": 99.0}
    )

    assert report.score == 99.0
    assert report.passed is False
