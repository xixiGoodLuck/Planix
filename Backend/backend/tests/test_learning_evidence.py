from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.learning.evidence import EvidenceGenerationPipeline, EvidencePipelineError
from app.learning.evidence.validators import EvidenceValidator
from app.learning.generators import LearningModelResponse
from app.learning.validators import LearningArtifactValidationError

from learning_evidence_fixtures import (
    FastApiEvidenceFixture,
    build_fastapi_crud_evidence_fixture,
)


class ScriptedEvidenceModel:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        stage: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        response_type,
        max_tokens: int,
    ):
        self.calls.append(
            {
                "stage": stage,
                "feature": feature,
                "system": system,
                "payload": payload,
                "maxTokens": max_tokens,
            }
        )
        return LearningModelResponse(
            value=response_type.model_validate(self.response),
            model_usage={"provider": "fixture", "model": "scripted-evidence"},
        )


def _generate(
    fixture: FastApiEvidenceFixture | None = None,
    *,
    response: dict[str, Any] | None = None,
):
    fixture = fixture or build_fastapi_crud_evidence_fixture()
    model = ScriptedEvidenceModel(response or deepcopy(fixture.semantic_response))
    result = EvidenceGenerationPipeline(
        provider=fixture.provider,
        model=model,
    ).generate(fixture.knowledge_graph)
    return fixture, model, result


def test_complete_knowledge_to_evidence_chain_passes() -> None:
    fixture, model, result = _generate()
    graph = result.evidence_graph

    assert fixture.provider.search_calls == 1
    assert fixture.provider.fetch_calls == ["fastapi-course-a"]
    assert set(fixture.provider.search_queries[0].knowledge_terms) == {
        "Routing",
        "Pydantic",
        "Database",
        "CRUD",
    }
    assert len(model.calls) == 1
    assert graph.knowledge_graph_ref.artifact_id == fixture.knowledge_graph.artifact_id
    assert len(graph.resources) == 1
    assert len(graph.segments) == 3
    assert len(graph.evidence) == 3
    assert len(graph.coverage_edges) == 4
    assert graph.resources[0].technology_versions == {
        "FastAPI": "0.115",
        "Pydantic": "2",
    }


def test_segments_automatically_bind_provider_evidence() -> None:
    _, _, result = _generate()
    graph = result.evidence_graph
    evidence_by_segment = {
        segment.id: {item.id for item in graph.evidence if item.segment_id == segment.id}
        for segment in graph.segments
    }

    assert all(set(segment.evidence_refs) == evidence_by_segment[segment.id] for segment in graph.segments)
    assert all(
        any(item.verification_status == "verified" for item in graph.evidence if item.segment_id == segment.id)
        for segment in graph.segments
    )


def test_coverage_edges_bind_existing_knowledge_segment_and_evidence() -> None:
    fixture, _, result = _generate()
    graph = result.evidence_graph
    knowledge_ids = {item.id for item in fixture.knowledge_graph.nodes}
    segment_ids = {item.id for item in graph.segments}
    evidence = {item.id: item for item in graph.evidence}

    for edge in graph.coverage_edges:
        assert edge.knowledge_id in knowledge_ids
        assert edge.segment_id in segment_ids
        assert edge.evidence_refs
        assert all(evidence[item_id].segment_id == edge.segment_id for item_id in edge.evidence_refs)


def test_generated_evidence_graph_passes_phase_one_validator() -> None:
    fixture, _, result = _generate()

    EvidenceValidator().validate_graph(
        fixture.knowledge_graph,
        result.evidence_graph,
    )


def test_model_payload_does_not_receive_provider_owned_fields() -> None:
    _, model, _ = _generate()
    payload = json.dumps(model.calls[0]["payload"], ensure_ascii=False)

    assert all(
        forbidden not in payload
        for forbidden in (
            "canonicalUrl",
            "durationSeconds",
            "startSeconds",
            "endSeconds",
            "timeRangeSeconds",
            "contentFingerprint",
        )
    )


def test_segment_beyond_video_duration_fails() -> None:
    _, _, result = _generate()
    graph = result.evidence_graph
    bad_segment = graph.segments[0].model_copy(update={"end_seconds": 7201})

    with pytest.raises(LearningArtifactValidationError, match="unsupported_timestamp"):
        EvidenceValidator().validate_segments(
            graph.resources,
            [bad_segment, *graph.segments[1:]],
        )


def test_segment_without_evidence_fails() -> None:
    fixture, _, result = _generate()
    graph = result.evidence_graph
    bad_segment = graph.segments[0].model_copy(update={"evidence_refs": []})
    bad_graph = graph.model_copy(update={"segments": [bad_segment, *graph.segments[1:]]})

    with pytest.raises(LearningArtifactValidationError, match="evidence_validity"):
        EvidenceValidator().validate_graph(fixture.knowledge_graph, bad_graph)


def test_coverage_with_missing_knowledge_fails() -> None:
    fixture, _, result = _generate()
    graph = result.evidence_graph
    bad_edge = graph.coverage_edges[0].model_copy(
        update={"knowledge_id": "knowledge-does-not-exist"}
    )

    with pytest.raises(LearningArtifactValidationError, match="coverage_knowledge_reference"):
        EvidenceValidator().validate_coverage(
            fixture.knowledge_graph,
            graph.segments,
            graph.evidence,
            [bad_edge, *graph.coverage_edges[1:]],
        )


def test_evidence_with_missing_segment_fails() -> None:
    _, _, result = _generate()
    graph = result.evidence_graph
    bad_evidence = graph.evidence[0].model_copy(
        update={"segment_id": "segment-does-not-exist"}
    )

    with pytest.raises(LearningArtifactValidationError, match="evidence_reference"):
        EvidenceValidator().validate_evidence(
            graph.resources,
            graph.segments,
            [bad_evidence, *graph.evidence[1:]],
        )


def test_model_cannot_create_a_video_time_range() -> None:
    fixture = build_fastapi_crud_evidence_fixture()
    response = deepcopy(fixture.semantic_response)
    response["segmentAnnotations"][0]["startSeconds"] = 1
    response["segmentAnnotations"][0]["endSeconds"] = 2
    model = ScriptedEvidenceModel(response)

    with pytest.raises(EvidencePipelineError, match="Extra inputs are not permitted") as caught:
        EvidenceGenerationPipeline(
            provider=fixture.provider,
            model=model,
        ).generate(fixture.knowledge_graph)

    assert caught.value.stage == "evidence_build"
    assert len(model.calls) == 1


def test_resource_fingerprint_change_invalidates_old_evidence() -> None:
    _, _, result = _generate()
    graph = result.evidence_graph
    changed_resource = graph.resources[0].model_copy(
        update={"content_fingerprint": "sha256:fastapi-course-a-v2"}
    )

    with pytest.raises(LearningArtifactValidationError, match="version_compatibility"):
        EvidenceValidator().validate_evidence(
            [changed_resource],
            graph.segments,
            graph.evidence,
        )


@pytest.mark.parametrize(
    ("updates", "expected_rule"),
    [
        ({"canonical_url": ""}, "video_url"),
        ({"duration_seconds": 0}, "video_duration"),
    ],
)
def test_video_resource_metadata_must_be_valid(
    updates: dict[str, Any],
    expected_rule: str,
) -> None:
    _, _, result = _generate()
    bad_resource = result.evidence_graph.resources[0].model_copy(update=updates)

    with pytest.raises(LearningArtifactValidationError, match=expected_rule):
        EvidenceValidator().validate_resources([bad_resource])
