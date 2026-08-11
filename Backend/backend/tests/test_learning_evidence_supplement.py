from __future__ import annotations

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
from app.learning.evidence.mapping import CoverageMapper
from app.learning.evidence.qualification import CandidateQualifier
from app.learning.evidence.retrieval import CandidateRetrievalSource, EvidenceCandidate
from app.learning.evidence.supplement import (
    EvidenceSupplementError,
    EvidenceSupplementValidationError,
    EvidenceSupplementValidator,
    EvidenceSupplementer,
)
from app.learning.evidence.transcript import TranscriptDocument, TranscriptSegment
from app.learning.generators import LearningModelResponse
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


class ScriptedCoverageModel:
    def __init__(self):
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
        self.calls.append(payload)
        return LearningModelResponse(
            value=response_type.model_validate(
                {
                    "segments": [
                        {
                            "mappings": [
                                {
                                    "knowledgeId": "knowledge-routing",
                                    "coverageType": "demonstration",
                                    "summary": "字幕完整演示 FastAPI 路由定义。",
                                    "confidence": 0.96,
                                    "reason": "已验证字幕直接演示路由与处理函数绑定。",
                                }
                            ]
                        }
                    ]
                }
            ),
            model_usage={"provider": "fixture", "model": "scripted-coverage"},
        )


def _existing_graph():
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    resource = VideoResource(
        id="video-existing-pydantic",
        provider="fixture",
        externalId="existing-pydantic",
        canonicalUrl="https://example.test/video/existing-pydantic",
        title="Existing Pydantic Evidence",
        durationSeconds=300,
        contentFingerprint="sha256:existing-pydantic-v1",
        technologyVersions={"Pydantic": "2"},
    )
    evidence = SegmentEvidence(
        id="evidence-existing-pydantic",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        segmentId="segment-existing-pydantic",
        kind="transcript_span",
        supportedClaim="Pydantic validates request fields.",
        sourceRange=EvidenceSourceRange(
            locatorType="transcript_chars",
            startOffset=0,
            endOffset=34,
        ),
        sourceExcerpt="Pydantic validates request fields.",
        verificationStatus="verified",
    )
    segment = ContentSegment(
        id="segment-existing-pydantic",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        startSeconds=20,
        endSeconds=80,
        contentSummary="Pydantic request validation",
        evidenceRefs=[evidence.id],
    )
    edge = CoverageEdge(
        id="coverage-existing-pydantic",
        knowledgeId="knowledge-pydantic",
        segmentId=segment.id,
        evidenceRefs=[evidence.id],
        coverageType="explanation",
        coverageStrength="full",
        confidence=0.95,
        summary="Complete Pydantic validation explanation.",
        reason="Verified transcript evidence.",
    )
    graph = EvidenceGraph(
        artifactId="evidence-graph-supplement",
        version=2,
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        resources=[resource],
        segments=[segment],
        evidence=[evidence],
        coverageEdges=[edge],
    )
    return knowledge_graph, graph


def _qualified_candidate():
    candidate = EvidenceCandidate(
        candidateId="candidate-routing-supplement",
        provider="bilibili",
        externalId="BV1ROUTING",
        url="https://www.bilibili.com/video/BV1ROUTING",
        title="FastAPI Routing Transcript",
        durationSeconds=600,
        contentFingerprint=f"sha256:{'c' * 64}",
        technologyVersions={"FastAPI": "0.115"},
        retrievalSource=CandidateRetrievalSource(
            retrievalPlanId="retrieval-plan-routing",
            knowledgeId="knowledge-routing",
            query="FastAPI Routing tutorial",
        ),
    )
    return CandidateQualifier().qualify(candidate)


def _transcript(qualified):
    assert qualified.resource is not None
    return TranscriptDocument(
        resourceId=qualified.resource.id,
        fingerprint=qualified.resource.content_fingerprint,
        language="zh-CN",
        segments=[
            TranscriptSegment(
                id="cue-routing-1",
                startSeconds=100,
                endSeconds=150,
                text="使用 app.get 定义 GET 路由并绑定处理函数。",
            ),
            TranscriptSegment(
                id="cue-routing-2",
                startSeconds=150,
                endSeconds=220,
                text="请求到达后 FastAPI 调用对应的路由处理函数。",
            ),
        ],
    )


def _supplement():
    knowledge_graph, existing_graph = _existing_graph()
    qualified = _qualified_candidate()
    transcript = _transcript(qualified)
    model = ScriptedCoverageModel()
    supplementer = EvidenceSupplementer(
        coverage_mapper=CoverageMapper(model=model)
    )
    result = supplementer.supplement(
        qualified,
        transcript,
        knowledge_graph,
        existing_graph,
    )
    return knowledge_graph, existing_graph, qualified, transcript, model, result


def _coverage(report, knowledge_id: str):
    return next(
        item for item in report.knowledge_coverage if item.knowledge_id == knowledge_id
    )


def test_missing_knowledge_is_completed_by_verified_transcript() -> None:
    _, _, _, transcript, model, result = _supplement()

    assert _coverage(result.coverage_before, "knowledge-routing").coverage_strength == "MISSING"
    assert _coverage(result.coverage_after, "knowledge-routing").coverage_strength == "FULL"
    assert result.new_segments[0].start_seconds == transcript.segments[0].start_seconds
    assert result.new_segments[0].end_seconds == transcript.segments[-1].end_seconds
    assert len(model.calls) == 1


def test_supplement_adds_coverage_bound_to_new_evidence() -> None:
    _, _, _, _, _, result = _supplement()

    assert result.new_coverage_edges
    edge = result.new_coverage_edges[0]
    assert edge.knowledge_id == "knowledge-routing"
    assert edge.segment_id == result.new_segments[0].id
    assert set(edge.evidence_refs) == set(result.new_segments[0].evidence_refs)
    assert set(edge.evidence_refs) <= {item.id for item in result.new_evidence}


def test_coverage_refresh_reduces_real_gaps() -> None:
    _, _, _, _, _, result = _supplement()

    assert len(result.coverage_after.gaps) < len(result.coverage_before.gaps)
    assert any(
        item.knowledge_id == "knowledge-routing"
        and item.gap_type == "missing_knowledge"
        for item in result.resolved_gaps
    )
    assert result.remaining_gaps == result.coverage_after.gaps


def test_existing_evidence_is_unchanged_and_lineage_advances() -> None:
    _, existing, _, _, _, result = _supplement()

    assert result.source_graph_ref == artifact_ref("evidence_graph", existing)
    assert result.supplemented_graph.artifact_id == existing.artifact_id
    assert result.supplemented_graph.version == existing.version + 1
    assert result.supplemented_graph.resources[:1] == existing.resources
    assert result.supplemented_graph.segments[:1] == existing.segments
    assert result.supplemented_graph.evidence[:1] == existing.evidence
    assert result.supplemented_graph.coverage_edges[:1] == existing.coverage_edges


def test_forged_transcript_timestamp_is_rejected() -> None:
    knowledge_graph, existing = _existing_graph()
    qualified = _qualified_candidate()
    transcript = _transcript(qualified)
    forged_cue = transcript.segments[-1].model_copy(update={"end_seconds": 700})
    forged = transcript.model_copy(
        update={"segments": [transcript.segments[0], forged_cue]}
    )

    with pytest.raises(EvidenceSupplementError, match="transcript_timestamp"):
        EvidenceSupplementer(
            coverage_mapper=CoverageMapper(model=ScriptedCoverageModel())
        ).supplement(qualified, forged, knowledge_graph, existing)


def test_validator_rejects_modified_existing_segment() -> None:
    knowledge_graph, existing, qualified, _, _, result = _supplement()
    changed_old = result.supplemented_graph.segments[0].model_copy(
        update={"start_seconds": 21}
    )
    forged_graph = result.supplemented_graph.model_copy(
        update={"segments": [changed_old, *result.supplemented_graph.segments[1:]]}
    )
    forged_result = result.model_copy(update={"supplemented_graph": forged_graph})

    with pytest.raises(EvidenceSupplementValidationError, match="append_only_evidence"):
        EvidenceSupplementValidator().validate_result(
            knowledge_graph,
            existing,
            qualified,
            forged_result,
        )


def test_transcript_fingerprint_mismatch_is_rejected() -> None:
    knowledge_graph, existing = _existing_graph()
    qualified = _qualified_candidate()
    transcript = _transcript(qualified).model_copy(
        update={"fingerprint": f"sha256:{'d' * 64}"}
    )

    with pytest.raises(EvidenceSupplementError, match="transcript_fingerprint"):
        EvidenceSupplementer(
            coverage_mapper=CoverageMapper(model=ScriptedCoverageModel())
        ).supplement(qualified, transcript, knowledge_graph, existing)


def test_validator_rejects_coverage_without_evidence() -> None:
    knowledge_graph, existing, qualified, _, _, result = _supplement()
    forged_edge = result.new_coverage_edges[0].model_copy(
        update={"evidence_refs": []}
    )
    forged_graph = result.supplemented_graph.model_copy(
        update={
            "coverage_edges": [*existing.coverage_edges, forged_edge],
        }
    )
    forged_result = result.model_copy(
        update={
            "supplemented_graph": forged_graph,
            "new_coverage_edges": [forged_edge],
        }
    )

    with pytest.raises(
        EvidenceSupplementValidationError,
        match="coverage_evidence_reference",
    ):
        EvidenceSupplementValidator().validate_result(
            knowledge_graph,
            existing,
            qualified,
            forged_result,
        )


def test_validator_rejects_deleted_existing_evidence() -> None:
    knowledge_graph, existing, qualified, _, _, result = _supplement()
    forged_graph = result.supplemented_graph.model_copy(
        update={"evidence": list(result.new_evidence)}
    )
    forged_result = result.model_copy(update={"supplemented_graph": forged_graph})

    with pytest.raises(EvidenceSupplementValidationError, match="append_only_evidence"):
        EvidenceSupplementValidator().validate_result(
            knowledge_graph,
            existing,
            qualified,
            forged_result,
        )
