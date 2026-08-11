from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.learning.contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    EvidenceSourceRange,
    SegmentEvidence,
    VideoResource,
)
from app.learning.evidence.mapping import (
    CoverageMapper,
    CoverageMappingError,
    CoverageMappingValidationError,
    CoverageMappingValidator,
)
from app.learning.generators import LearningModelResponse
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


class ScriptedCoverageModel:
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
            model_usage={"provider": "fixture", "model": "scripted-coverage"},
        )


def _source_graph():
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    resource = VideoResource(
        id="video-mapping",
        provider="fixture",
        externalId="mapping-a",
        canonicalUrl="https://example.test/video/mapping-a",
        title="FastAPI Mapping Fixture",
        durationSeconds=100,
        contentFingerprint="sha256:mapping-a-v1",
    )
    evidence = [
        SegmentEvidence(
            id="evidence-routing",
            resourceId=resource.id,
            resourceFingerprint=resource.content_fingerprint,
            segmentId="segment-routing-pydantic",
            kind="transcript_span",
            supportedClaim="FastAPI 路由将 GET 请求绑定到处理函数。",
            sourceRange=EvidenceSourceRange(
                locatorType="transcript_chars",
                startOffset=0,
                endOffset=24,
            ),
            sourceExcerpt="FastAPI 路由将 GET 请求绑定到处理函数。",
            verificationStatus="verified",
        ),
        SegmentEvidence(
            id="evidence-pydantic",
            resourceId=resource.id,
            resourceFingerprint=resource.content_fingerprint,
            segmentId="segment-routing-pydantic",
            kind="transcript_span",
            supportedClaim="Pydantic 在请求进入路由前校验字段。",
            sourceRange=EvidenceSourceRange(
                locatorType="transcript_chars",
                startOffset=25,
                endOffset=48,
            ),
            sourceExcerpt="Pydantic 在请求进入路由前校验字段。",
            verificationStatus="verified",
        ),
    ]
    segment = ContentSegment(
        id="segment-routing-pydantic",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        startSeconds=10,
        endSeconds=30,
        contentSummary="字幕演示 FastAPI 路由并解释 Pydantic 请求校验。",
        topics=[],
        evidenceRefs=[item.id for item in evidence],
    )
    graph = EvidenceGraph(
        artifactId="evidence-graph-mapping",
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        resources=[resource],
        segments=[segment],
        evidence=evidence,
        coverageEdges=[],
    )
    return knowledge_graph, graph


def _response() -> dict[str, Any]:
    return {
        "segments": [
            {
                "mappings": [
                    {
                        "knowledgeId": "knowledge-routing",
                        "coverageType": "demonstration",
                        "summary": "字幕演示 GET 路由定义。",
                        "confidence": 0.96,
                        "reason": "字幕直接说明请求方法与处理函数的映射。",
                    },
                    {
                        "knowledgeId": "knowledge-pydantic",
                        "coverageType": "explanation",
                        "summary": "字幕解释 Pydantic 请求校验。",
                        "confidence": 0.93,
                        "reason": "字幕直接说明请求进入路由前的字段校验。",
                    },
                ]
            }
        ]
    }


def _map(response: dict[str, Any] | None = None):
    knowledge_graph, graph = _source_graph()
    model = ScriptedCoverageModel(response or _response())
    edges = CoverageMapper(model=model).map(knowledge_graph, graph)
    return knowledge_graph, graph, model, edges


def test_routing_transcript_maps_to_existing_knowledge() -> None:
    _, graph, model, edges = _map()
    routing = next(item for item in edges if item.knowledge_id == "knowledge-routing")

    assert len(model.calls) == 1
    assert routing.segment_id == graph.segments[0].id
    assert routing.coverage_type == "demonstration"
    assert routing.coverage_strength == "full"
    assert routing.summary == "字幕演示 GET 路由定义。"


def test_one_segment_can_cover_multiple_knowledge_nodes() -> None:
    _, graph, _, edges = _map()

    assert {item.knowledge_id for item in edges} == {
        "knowledge-routing",
        "knowledge-pydantic",
    }
    assert {item.segment_id for item in edges} == {graph.segments[0].id}


def test_code_binds_existing_segment_and_verified_evidence() -> None:
    _, graph, model, edges = _map()
    expected_evidence = set(graph.segments[0].evidence_refs)

    assert all(set(item.evidence_refs) == expected_evidence for item in edges)
    payload = json.dumps(model.calls[0]["payload"], ensure_ascii=False)
    assert all(
        forbidden not in payload
        for forbidden in (
            "segmentId",
            "evidenceId",
            "startSeconds",
            "endSeconds",
            "durationSeconds",
            "canonicalUrl",
            "fingerprint",
        )
    )


def test_missing_knowledge_from_model_is_rejected() -> None:
    response = _response()
    response["segments"][0]["mappings"][0]["knowledgeId"] = "knowledge-missing"

    with pytest.raises(CoverageMappingError, match="coverage_knowledge_reference"):
        _map(response)


def test_missing_segment_is_rejected() -> None:
    knowledge_graph, graph = _source_graph()
    edge = CoverageEdge(
        id="coverage-missing-segment",
        knowledgeId="knowledge-routing",
        segmentId="segment-missing",
        evidenceRefs=[graph.evidence[0].id],
        coverageType="explanation",
        coverageStrength="full",
        confidence=0.9,
        summary="Invalid segment binding",
        reason="Fixture",
    )

    with pytest.raises(CoverageMappingValidationError, match="coverage_segment_reference"):
        CoverageMappingValidator().validate_edges(knowledge_graph, graph, [edge])


@pytest.mark.parametrize("forbidden_field", ["startSeconds", "url"])
def test_model_cannot_return_provider_owned_fields(forbidden_field: str) -> None:
    response = deepcopy(_response())
    response["segments"][0]["mappings"][0][forbidden_field] = (
        10 if forbidden_field == "startSeconds" else "https://forbidden.test/video"
    )

    with pytest.raises(CoverageMappingError, match="Extra inputs are not permitted"):
        _map(response)


def test_metadata_evidence_cannot_claim_high_confidence() -> None:
    knowledge_graph, graph = _source_graph()
    metadata = graph.evidence[0].model_copy(update={"kind": "provider_metadata"})
    segment = graph.segments[0].model_copy(update={"evidence_refs": [metadata.id]})
    metadata_graph = graph.model_copy(
        update={"segments": [segment], "evidence": [metadata]}
    )
    edge = CoverageEdge(
        id="coverage-metadata-high",
        knowledgeId="knowledge-routing",
        segmentId=segment.id,
        evidenceRefs=[metadata.id],
        coverageType="introduction",
        coverageStrength="full",
        confidence=0.9,
        summary="Metadata-only claim",
        reason="Fixture",
    )

    with pytest.raises(CoverageMappingValidationError, match="evidence_confidence"):
        CoverageMappingValidator().validate_edges(
            knowledge_graph,
            metadata_graph,
            [edge],
        )


def test_missing_evidence_reference_is_rejected() -> None:
    knowledge_graph, graph = _source_graph()
    edge = CoverageEdge(
        id="coverage-missing-evidence",
        knowledgeId="knowledge-routing",
        segmentId=graph.segments[0].id,
        evidenceRefs=["evidence-missing"],
        coverageType="explanation",
        coverageStrength="partial",
        confidence=0.7,
        summary="Missing evidence",
        reason="Fixture",
    )

    with pytest.raises(CoverageMappingValidationError, match="coverage_evidence_reference"):
        CoverageMappingValidator().validate_edges(knowledge_graph, graph, [edge])
