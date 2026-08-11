from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.learning.contracts import (
    ContentSegment,
    ContentSelection,
    EvidenceSourceRange,
    LearningContentPlan,
    LearningQualityIssue,
    LearningQualityReport,
    RecommendedContent,
    SelectedSegment,
)
from app.learning.validators import (
    LearningArtifactValidationError,
    LearningArtifactValidator,
)
from learning_fixtures import FastApiLearningFixture, build_fastapi_crud_learning_fixture


@pytest.fixture()
def artifacts() -> FastApiLearningFixture:
    return build_fastapi_crud_learning_fixture()


@pytest.fixture()
def validator() -> LearningArtifactValidator:
    return LearningArtifactValidator()


def _validate(
    validator: LearningArtifactValidator,
    artifacts: FastApiLearningFixture,
):
    return validator.validate_chain(
        scope=artifacts.scope,
        capability_graph=artifacts.capability_graph,
        knowledge_graph=artifacts.knowledge_graph,
        evidence_graph=artifacts.evidence_graph,
        content_selection=artifacts.content_selection,
        content_plan=artifacts.content_plan,
        quality_report=artifacts.quality_report,
    )


def test_complete_learning_artifact_chain_passes(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    validated = _validate(validator, artifacts)

    assert validated.scope.user_goal == "30天学习FastAPI并完成CRUD API"
    assert len(validated.capability_graph.capabilities) == 4
    assert len(validated.knowledge_graph.nodes) == 5
    assert len(validated.evidence_graph.segments) == 2
    assert len(validated.content_selection.selected_segments) == 2
    assert len(validated.content_plan.items) == 5


def test_quality_report_passed_is_code_owned(
    artifacts: FastApiLearningFixture,
) -> None:
    assert artifacts.quality_report.passed is True

    major_issue = LearningQualityIssue(
        issueId="issue-evidence-gap",
        rule="evidence_validity",
        severity="major",
        targetType="content_segment",
        targetId="segment-routing",
        description="The selected segment lacks sufficient evidence.",
    )
    failing_report = artifacts.quality_report.model_copy(update={"issues": [major_issue]})

    assert failing_report.passed is False


def test_selection_duration_is_derived_from_evidence_segments(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    validated = _validate(validator, artifacts)

    assert artifacts.content_selection.total_duration_seconds == 0
    assert validated.content_selection.total_duration_seconds == 1200
    assert validated.content_plan.total_duration_seconds == 1200


def test_knowledge_prerequisite_cycle_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    first_edge = artifacts.knowledge_graph.edges[0]
    reverse_edge = first_edge.model_copy(
        update={
            "source_knowledge_id": first_edge.target_knowledge_id,
            "target_knowledge_id": first_edge.source_knowledge_id,
        }
    )
    knowledge_graph = artifacts.knowledge_graph.model_copy(
        update={"edges": [*artifacts.knowledge_graph.edges, reverse_edge]}
    )

    with pytest.raises(LearningArtifactValidationError, match="knowledge_cycle"):
        validator.validate_knowledge_graph(
            artifacts.scope,
            artifacts.capability_graph,
            knowledge_graph,
        )


def test_segment_beyond_video_duration_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_segment = artifacts.evidence_graph.segments[0].model_copy(
        update={"end_seconds": 7201}
    )
    evidence_graph = artifacts.evidence_graph.model_copy(
        update={"segments": [bad_segment, *artifacts.evidence_graph.segments[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="unsupported_timestamp"):
        validator.validate_evidence_graph(artifacts.knowledge_graph, evidence_graph)


def test_segment_without_verified_evidence_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    unverified = artifacts.evidence_graph.evidence[0].model_copy(
        update={"verification_status": "unverified"}
    )
    evidence_graph = artifacts.evidence_graph.model_copy(
        update={"evidence": [unverified, *artifacts.evidence_graph.evidence[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="evidence_validity"):
        validator.validate_evidence_graph(artifacts.knowledge_graph, evidence_graph)


def test_coverage_with_unknown_segment_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_edge = artifacts.evidence_graph.coverage_edges[0].model_copy(
        update={"segment_id": "segment-does-not-exist"}
    )
    evidence_graph = artifacts.evidence_graph.model_copy(
        update={"coverage_edges": [bad_edge, *artifacts.evidence_graph.coverage_edges[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="coverage_segment_reference"):
        validator.validate_evidence_graph(artifacts.knowledge_graph, evidence_graph)


def test_selection_cannot_submit_a_forged_time_range(
    artifacts: FastApiLearningFixture,
) -> None:
    payload = artifacts.content_selection.model_dump(by_alias=True)
    payload["selectedSegments"][0]["startSeconds"] = 1
    payload["selectedSegments"][0]["endSeconds"] = 2

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContentSelection.model_validate(payload)


def test_plan_with_unknown_selection_reference_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_ref = artifacts.content_plan.content_selection_ref.model_copy(
        update={"artifact_id": "content-selection-does-not-exist"}
    )
    content_plan = artifacts.content_plan.model_copy(
        update={"content_selection_ref": bad_ref}
    )

    with pytest.raises(LearningArtifactValidationError, match="version_compatibility"):
        validator.validate_content_plan(
            artifacts.scope,
            artifacts.knowledge_graph,
            artifacts.evidence_graph,
            artifacts.content_selection,
            content_plan,
        )


def test_quality_report_rejects_caller_supplied_passed(
    artifacts: FastApiLearningFixture,
) -> None:
    payload = artifacts.quality_report.model_dump(by_alias=True, exclude={"passed"})
    payload["passed"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LearningQualityReport.model_validate(payload)


def test_outcome_must_reference_current_scope(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_outcome = artifacts.capability_graph.outcomes[0].model_copy(
        update={"source_goal_refs": ["another-scope"]}
    )
    capability_graph = artifacts.capability_graph.model_copy(update={"outcomes": [bad_outcome]})

    with pytest.raises(LearningArtifactValidationError, match="scope_outcome_lineage"):
        validator.validate_capability_graph(artifacts.scope, capability_graph)


def test_required_outcome_requires_a_required_capability(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    optional_capabilities = [
        capability.model_copy(update={"importance": "optional"})
        for capability in artifacts.capability_graph.capabilities
    ]
    capability_graph = artifacts.capability_graph.model_copy(
        update={"capabilities": optional_capabilities}
    )

    with pytest.raises(LearningArtifactValidationError, match="required_outcome_coverage"):
        validator.validate_capability_graph(artifacts.scope, capability_graph)


def test_capability_prerequisite_cycle_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    cycle_edge = artifacts.capability_graph.edges[1].model_copy(
        update={
            "source_capability_id": "capability-crud",
            "target_capability_id": "capability-data-validation",
        }
    )
    capability_graph = artifacts.capability_graph.model_copy(
        update={"edges": [*artifacts.capability_graph.edges, cycle_edge]}
    )

    with pytest.raises(LearningArtifactValidationError, match="capability_cycle"):
        validator.validate_capability_graph(artifacts.scope, capability_graph)


def test_knowledge_must_reference_an_existing_capability(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_node = artifacts.knowledge_graph.nodes[0].model_copy(
        update={"capability_refs": ["capability-does-not-exist"]}
    )
    knowledge_graph = artifacts.knowledge_graph.model_copy(
        update={"nodes": [bad_node, *artifacts.knowledge_graph.nodes[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="knowledge_capability_lineage"):
        validator.validate_knowledge_graph(
            artifacts.scope,
            artifacts.capability_graph,
            knowledge_graph,
        )


def test_invalid_evidence_source_range_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_range = artifacts.evidence_graph.evidence[0].source_range.model_copy(
        update={"end_offset": 0}
    )
    bad_evidence = artifacts.evidence_graph.evidence[0].model_copy(
        update={"source_range": bad_range}
    )
    evidence_graph = artifacts.evidence_graph.model_copy(
        update={"evidence": [bad_evidence, *artifacts.evidence_graph.evidence[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="evidence_validity"):
        validator.validate_evidence_graph(artifacts.knowledge_graph, evidence_graph)


def test_selection_with_unknown_segment_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_selection = artifacts.content_selection.selected_segments[0].model_copy(
        update={"segment_id": "segment-does-not-exist"}
    )
    selection = artifacts.content_selection.model_copy(
        update={
            "selected_segments": [
                bad_selection,
                *artifacts.content_selection.selected_segments[1:],
            ]
        }
    )

    with pytest.raises(LearningArtifactValidationError, match="selection_segment_reference"):
        validator.validate_content_selection(
            artifacts.scope,
            artifacts.knowledge_graph,
            artifacts.evidence_graph,
            selection,
        )


def test_selection_knowledge_requires_a_coverage_edge(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    unsupported = artifacts.content_selection.selected_segments[0].model_copy(
        update={"coverage_edge_refs": ["coverage-http-routing"]}
    )
    selection = artifacts.content_selection.model_copy(
        update={
            "selected_segments": [
                unsupported,
                *artifacts.content_selection.selected_segments[1:],
            ]
        }
    )

    with pytest.raises(LearningArtifactValidationError, match="selection_coverage_reference"):
        validator.validate_content_selection(
            artifacts.scope,
            artifacts.knowledge_graph,
            artifacts.evidence_graph,
            selection,
        )


def test_plan_with_unknown_knowledge_fails(
    artifacts: FastApiLearningFixture,
    validator: LearningArtifactValidator,
) -> None:
    bad_item = artifacts.content_plan.items[0].model_copy(
        update={"knowledge_id": "knowledge-does-not-exist"}
    )
    plan = artifacts.content_plan.model_copy(
        update={"items": [bad_item, *artifacts.content_plan.items[1:]]}
    )

    with pytest.raises(LearningArtifactValidationError, match="knowledge_coverage"):
        validator.validate_content_plan(
            artifacts.scope,
            artifacts.knowledge_graph,
            artifacts.evidence_graph,
            artifacts.content_selection,
            plan,
        )


def test_only_content_segment_owns_video_timestamp_fields() -> None:
    timestamp_fields = {"start_seconds", "end_seconds"}

    assert timestamp_fields <= set(ContentSegment.model_fields)
    for model in (
        ContentSelection,
        SelectedSegment,
        LearningContentPlan,
        RecommendedContent,
        EvidenceSourceRange,
    ):
        assert timestamp_fields.isdisjoint(model.model_fields)
