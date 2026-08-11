from __future__ import annotations

import pytest

from app.learning.contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    EvidenceSourceRange,
    SegmentEvidence,
    VideoResource,
)
from app.learning.evidence.coverage import (
    CoverageAggregator,
    CoverageReportValidationError,
    CoverageReportValidator,
    EvidenceCoverageGap,
)
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture


def _source_graph():
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    resources = [
        VideoResource(
            id="video-routing-old",
            provider="fixture",
            externalId="routing-old",
            canonicalUrl="https://example.test/routing-old",
            title="Routing with FastAPI 0.110",
            durationSeconds=120,
            contentFingerprint="sha256:routing-old",
            technologyVersions={"FastAPI": "0.110"},
        ),
        VideoResource(
            id="video-routing-new",
            provider="fixture",
            externalId="routing-new",
            canonicalUrl="https://example.test/routing-new",
            title="Routing with FastAPI 0.115",
            durationSeconds=120,
            contentFingerprint="sha256:routing-new",
            technologyVersions={"FastAPI": "0.115"},
        ),
        VideoResource(
            id="video-pydantic",
            provider="fixture",
            externalId="pydantic",
            canonicalUrl="https://example.test/pydantic",
            title="Pydantic Validation",
            durationSeconds=120,
            contentFingerprint="sha256:pydantic",
            technologyVersions={"Pydantic": "2"},
        ),
    ]
    segment_specs = [
        ("segment-routing-old", resources[0], 10, 30, "旧版本路由演示"),
        ("segment-routing-new", resources[1], 20, 40, "新版本路由演示"),
        ("segment-pydantic", resources[2], 30, 50, "Pydantic 基础介绍"),
    ]
    evidence = [
        SegmentEvidence(
            id=f"evidence-{segment_id}",
            resourceId=resource.id,
            resourceFingerprint=resource.content_fingerprint,
            segmentId=segment_id,
            kind="transcript_span",
            supportedClaim=summary,
            sourceRange=EvidenceSourceRange(
                locatorType="transcript_chars",
                startOffset=0,
                endOffset=len(summary),
            ),
            sourceExcerpt=summary,
            verificationStatus="verified",
        )
        for segment_id, resource, _, _, summary in segment_specs
    ]
    segments = [
        ContentSegment(
            id=segment_id,
            resourceId=resource.id,
            resourceFingerprint=resource.content_fingerprint,
            startSeconds=start,
            endSeconds=end,
            contentSummary=summary,
            evidenceRefs=[f"evidence-{segment_id}"],
        )
        for segment_id, resource, start, end, summary in segment_specs
    ]
    edges = [
        CoverageEdge(
            id="coverage-routing-old",
            knowledgeId="knowledge-routing",
            segmentId=segments[0].id,
            evidenceRefs=[evidence[0].id],
            coverageType="explanation",
            coverageStrength="full",
            confidence=0.95,
            summary="完整解释路由。",
            reason="Verified transcript",
        ),
        CoverageEdge(
            id="coverage-routing-new",
            knowledgeId="knowledge-routing",
            segmentId=segments[1].id,
            evidenceRefs=[evidence[1].id],
            coverageType="explanation",
            coverageStrength="full",
            confidence=0.92,
            summary="完整解释路由。",
            reason="Verified transcript",
        ),
        CoverageEdge(
            id="coverage-pydantic",
            knowledgeId="knowledge-pydantic",
            segmentId=segments[2].id,
            evidenceRefs=[evidence[2].id],
            coverageType="introduction",
            coverageStrength="partial",
            confidence=0.7,
            summary="只介绍基础校验。",
            reason="Verified transcript but incomplete concepts",
        ),
    ]
    graph = EvidenceGraph(
        artifactId="evidence-graph-analysis",
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        resources=resources,
        segments=segments,
        evidence=evidence,
        coverageEdges=edges,
    )
    return knowledge_graph, graph


def _coverage(report, knowledge_id: str):
    return next(item for item in report.knowledge_coverage if item.knowledge_id == knowledge_id)


def test_multiple_videos_produce_full_knowledge_coverage() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    routing = _coverage(report, "knowledge-routing")

    assert routing.status == "sufficient"
    assert routing.coverage_strength == "FULL"
    assert set(routing.segment_refs) == {
        "segment-routing-old",
        "segment-routing-new",
    }
    assert len(routing.evidence_refs) == 2


def test_precise_but_incomplete_evidence_is_partial() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    pydantic = _coverage(report, "knowledge-pydantic")

    assert pydantic.status == "insufficient"
    assert pydantic.coverage_strength == "PARTIAL"
    assert any(
        gap.knowledge_id == "knowledge-pydantic"
        and gap.gap_type == "unsupported_required"
        for gap in report.gaps
    )


def test_knowledge_without_evidence_is_missing() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    database = _coverage(report, "knowledge-database")

    assert database.status == "missing"
    assert database.coverage_strength == "MISSING"
    assert any(
        gap.knowledge_id == "knowledge-database"
        and gap.gap_type == "missing_knowledge"
        for gap in report.gaps
    )


def test_metadata_only_evidence_is_weak() -> None:
    knowledge_graph, graph = _source_graph()
    metadata = graph.evidence[2].model_copy(
        update={"kind": "provider_metadata", "verification_status": "verified"}
    )
    graph = graph.model_copy(
        update={"evidence": [*graph.evidence[:2], metadata]}
    )
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    pydantic = _coverage(report, "knowledge-pydantic")

    assert pydantic.coverage_strength == "WEAK"
    assert any(
        gap.knowledge_id == "knowledge-pydantic"
        and gap.gap_type == "weak_coverage"
        for gap in report.gaps
    )


def test_different_resource_versions_create_version_conflict() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)

    conflict = next(
        item
        for item in report.conflicts
        if item.knowledge_id == "knowledge-routing" and item.technology == "FastAPI"
    )
    assert {item.version for item in conflict.observations} == {"0.110", "0.115"}
    assert {ref for item in conflict.observations for ref in item.resource_refs} == {
        "video-routing-old",
        "video-routing-new",
    }


def test_same_coverage_type_is_reported_as_redundant() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    analysis = next(
        item for item in report.redundancy if item.knowledge_id == "knowledge-routing"
    )

    assert analysis.classification == "REDUNDANT"
    assert len(analysis.segment_refs) == 2


def test_different_coverage_types_are_complementary() -> None:
    knowledge_graph, graph = _source_graph()
    implementation = graph.coverage_edges[1].model_copy(
        update={"coverage_type": "implementation"}
    )
    graph = graph.model_copy(
        update={
            "coverage_edges": [
                graph.coverage_edges[0],
                implementation,
                graph.coverage_edges[2],
            ]
        }
    )
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    analysis = next(
        item for item in report.redundancy if item.knowledge_id == "knowledge-routing"
    )

    assert analysis.classification == "COMPLEMENTARY"


def test_explicit_segment_dependency_is_context_required() -> None:
    knowledge_graph, graph = _source_graph()
    dependent = graph.segments[1].model_copy(
        update={"context_segment_refs": [graph.segments[0].id]}
    )
    graph = graph.model_copy(
        update={"segments": [graph.segments[0], dependent, graph.segments[2]]}
    )
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    analysis = next(
        item for item in report.redundancy if item.knowledge_id == "knowledge-routing"
    )

    assert analysis.classification == "CONTEXT_REQUIRED"


def test_coverage_reference_to_missing_segment_is_rejected() -> None:
    knowledge_graph, graph = _source_graph()
    invalid = graph.coverage_edges[0].model_copy(
        update={"segment_id": "segment-missing"}
    )
    graph = graph.model_copy(
        update={"coverage_edges": [invalid, *graph.coverage_edges[1:]]}
    )

    with pytest.raises(CoverageReportValidationError, match="coverage_segment_reference"):
        CoverageAggregator().aggregate(knowledge_graph, graph)


def test_manually_forged_full_coverage_is_rejected() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    forged = [
        item.model_copy(
            update={"coverage_strength": "FULL", "status": "sufficient"}
        )
        if item.knowledge_id == "knowledge-pydantic"
        else item
        for item in report.knowledge_coverage
    ]
    forged_report = report.model_copy(update={"knowledge_coverage": forged})

    with pytest.raises(CoverageReportValidationError, match="coverage_strength_computation"):
        CoverageReportValidator().validate_report(
            knowledge_graph,
            graph,
            forged_report,
        )


def test_gap_referencing_missing_knowledge_is_rejected() -> None:
    knowledge_graph, graph = _source_graph()
    report = CoverageAggregator().aggregate(knowledge_graph, graph)
    invalid_gap = EvidenceCoverageGap(
        knowledgeId="knowledge-missing",
        gapType="missing_knowledge",
        currentStrength="MISSING",
        reason="Invalid fixture gap",
    )
    invalid_report = report.model_copy(update={"gaps": [*report.gaps, invalid_gap]})

    with pytest.raises(
        CoverageReportValidationError,
        match="coverage_gap_knowledge_reference",
    ):
        CoverageReportValidator().validate_report(
            knowledge_graph,
            graph,
            invalid_report,
        )
