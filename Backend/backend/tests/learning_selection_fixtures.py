from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.learning.contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    EvidenceSourceRange,
    KnowledgeGraph,
    LearningScope,
    SegmentEvidence,
    VideoResource,
)
from app.learning.generators.base import artifact_ref

from learning_evidence_fixtures import build_fastapi_crud_evidence_fixture
from learning_fixtures import build_fastapi_crud_learning_fixture


@dataclass(frozen=True)
class FastApiSelectionFixture:
    scope: LearningScope
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph


def _evidence(
    evidence_id: str,
    resource: VideoResource,
    segment_id: str,
    claim: str,
    start_offset: int,
    end_offset: int,
    *,
    kind: Literal["transcript_span", "caption_span"] = "transcript_span",
) -> SegmentEvidence:
    return SegmentEvidence(
        id=evidence_id,
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        segmentId=segment_id,
        kind=kind,
        supportedClaim=claim,
        sourceRange=EvidenceSourceRange(
            locatorType="transcript_chars" if kind == "transcript_span" else "caption_cues",
            startOffset=start_offset,
            endOffset=end_offset,
        ),
        sourceExcerpt=claim,
        verificationStatus="verified",
    )


def build_fastapi_selection_fixture() -> FastApiSelectionFixture:
    scope = build_fastapi_crud_learning_fixture().scope
    knowledge_graph = build_fastapi_crud_evidence_fixture().knowledge_graph
    video_a = VideoResource(
        id="video-a",
        provider="fixture",
        externalId="fastapi-course-a",
        canonicalUrl="https://example.test/videos/fastapi-course-a",
        title="FastAPI Course A",
        author="Planix Fixture",
        language="zh-CN",
        technologyVersions={"FastAPI": "0.115", "Pydantic": "2"},
        durationSeconds=7200,
        publishedAt="2026-01-15T00:00:00Z",
        contentFingerprint="sha256:fastapi-course-a-selection-v1",
    )
    video_b = VideoResource(
        id="video-b",
        provider="fixture",
        externalId="fastapi-course-b",
        canonicalUrl="https://example.test/videos/fastapi-course-b",
        title="FastAPI Persistence Course B",
        author="Planix Fixture",
        language="zh-CN",
        technologyVersions={"FastAPI": "0.115", "SQLAlchemy": "2"},
        durationSeconds=5400,
        publishedAt="2026-02-10T00:00:00Z",
        contentFingerprint="sha256:fastapi-course-b-selection-v1",
    )
    evidence_a = _evidence(
        "evidence-a-core",
        video_a,
        "segment-a-core",
        "The transcript demonstrates FastAPI Routing and Pydantic validation.",
        100,
        980,
    )
    evidence_a_duplicate = _evidence(
        "evidence-a-duplicate",
        video_a,
        "segment-a-duplicate",
        "The transcript repeats the same Routing and Pydantic validation workflow.",
        981,
        2060,
    )
    evidence_b = _evidence(
        "evidence-b-core",
        video_b,
        "segment-b-core",
        "The transcript demonstrates database persistence and CRUD operations.",
        100,
        1040,
    )
    segment_a = ContentSegment(
        id="segment-a-core",
        resourceId=video_a.id,
        resourceFingerprint=video_a.content_fingerprint,
        startSeconds=600,
        endSeconds=1200,
        contentSummary="FastAPI Routing与Pydantic数据校验。",
        topics=["Routing", "Pydantic"],
        evidenceRefs=[evidence_a.id],
    )
    segment_a_duplicate = ContentSegment(
        id="segment-a-duplicate",
        resourceId=video_a.id,
        resourceFingerprint=video_a.content_fingerprint,
        startSeconds=1800,
        endSeconds=2700,
        contentSummary="重复讲解Routing与Pydantic校验。",
        topics=["Routing", "Pydantic"],
        evidenceRefs=[evidence_a_duplicate.id],
    )
    segment_b = ContentSegment(
        id="segment-b-core",
        resourceId=video_b.id,
        resourceFingerprint=video_b.content_fingerprint,
        startSeconds=300,
        endSeconds=900,
        contentSummary="数据库持久化与完整CRUD操作。",
        topics=["Database", "CRUD"],
        evidenceRefs=[evidence_b.id],
    )
    knowledge_ids = {item.name: item.id for item in knowledge_graph.nodes}
    coverage_edges = [
        CoverageEdge(
            id="coverage-routing-a",
            knowledgeId=knowledge_ids["Routing"],
            segmentId=segment_a.id,
            evidenceRefs=[evidence_a.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.97,
            reason="Verified transcript demonstrates routing.",
        ),
        CoverageEdge(
            id="coverage-pydantic-a",
            knowledgeId=knowledge_ids["Pydantic"],
            segmentId=segment_a.id,
            evidenceRefs=[evidence_a.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.97,
            reason="Verified transcript demonstrates Pydantic validation.",
        ),
        CoverageEdge(
            id="coverage-routing-a-duplicate",
            knowledgeId=knowledge_ids["Routing"],
            segmentId=segment_a_duplicate.id,
            evidenceRefs=[evidence_a_duplicate.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.9,
            reason="Verified transcript repeats routing coverage.",
        ),
        CoverageEdge(
            id="coverage-pydantic-a-duplicate",
            knowledgeId=knowledge_ids["Pydantic"],
            segmentId=segment_a_duplicate.id,
            evidenceRefs=[evidence_a_duplicate.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.9,
            reason="Verified transcript repeats Pydantic coverage.",
        ),
        CoverageEdge(
            id="coverage-database-b",
            knowledgeId=knowledge_ids["Database"],
            segmentId=segment_b.id,
            evidenceRefs=[evidence_b.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.96,
            reason="Verified transcript demonstrates persistence.",
        ),
        CoverageEdge(
            id="coverage-crud-b",
            knowledgeId=knowledge_ids["CRUD"],
            segmentId=segment_b.id,
            evidenceRefs=[evidence_b.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.95,
            reason="Verified transcript demonstrates complete CRUD operations.",
        ),
    ]
    evidence_graph = EvidenceGraph(
        artifactId="evidence-graph-fastapi-selection",
        knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
        resources=[video_a, video_b],
        segments=[segment_a, segment_a_duplicate, segment_b],
        evidence=[evidence_a, evidence_a_duplicate, evidence_b],
        coverageEdges=coverage_edges,
    )
    return FastApiSelectionFixture(
        scope=scope,
        knowledge_graph=knowledge_graph,
        evidence_graph=evidence_graph,
    )


__all__ = ["FastApiSelectionFixture", "build_fastapi_selection_fixture"]
