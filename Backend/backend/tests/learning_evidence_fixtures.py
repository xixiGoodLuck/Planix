from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.learning.contracts import KnowledgeGraph
from app.learning.evidence.providers import (
    MockVideoProvider,
    ProviderEvidenceSource,
    ProviderSegmentSource,
    ProviderVideoDocument,
    ProviderVideoMetadata,
)

from learning_fixtures import build_fastapi_crud_learning_fixture


@dataclass(frozen=True)
class FastApiEvidenceFixture:
    knowledge_graph: KnowledgeGraph
    provider: MockVideoProvider
    document: ProviderVideoDocument
    semantic_response: dict[str, Any]


def _transcript_evidence(
    claim: str,
    excerpt: str,
    start_offset: int,
    end_offset: int,
) -> ProviderEvidenceSource:
    return ProviderEvidenceSource(
        kind="transcript_span",
        supportedClaim=claim,
        sourceRange={
            "locatorType": "transcript_chars",
            "startOffset": start_offset,
            "endOffset": end_offset,
        },
        sourceExcerpt=excerpt,
        verificationStatus="verified",
    )


def build_fastapi_crud_evidence_fixture() -> FastApiEvidenceFixture:
    phase_one = build_fastapi_crud_learning_fixture()
    included_ids = {
        "knowledge-routing",
        "knowledge-pydantic",
        "knowledge-database",
        "knowledge-crud",
    }
    knowledge_graph = phase_one.knowledge_graph.model_copy(
        update={
            "artifact_id": "knowledge-graph-fastapi-evidence",
            "nodes": [
                node for node in phase_one.knowledge_graph.nodes if node.id in included_ids
            ],
            "edges": [
                edge
                for edge in phase_one.knowledge_graph.edges
                if edge.source_knowledge_id in included_ids
                and edge.target_knowledge_id in included_ids
            ],
        }
    )
    document = ProviderVideoDocument(
        metadata=ProviderVideoMetadata(
            provider="fixture",
            externalId="fastapi-course-a",
            canonicalUrl="https://example.test/videos/fastapi-course-a",
            title="FastAPI Course A",
            author="Planix Evidence Fixture",
            language="zh-CN",
            durationSeconds=7200,
            publishedAt="2026-01-15T00:00:00Z",
            contentFingerprint="sha256:fastapi-course-a-v1",
            technologyVersions={"FastAPI": "0.115", "Pydantic": "2"},
        ),
        segments=[
            ProviderSegmentSource(
                sourceKey="routing",
                timeRangeSeconds=(600, 1200),
                evidence=[
                    _transcript_evidence(
                        "The segment demonstrates FastAPI routing and HTTP endpoint mapping.",
                        "Define a route, choose its HTTP method, and bind the handler.",
                        100,
                        840,
                    )
                ],
            ),
            ProviderSegmentSource(
                sourceKey="pydantic",
                timeRangeSeconds=(1200, 1800),
                evidence=[
                    _transcript_evidence(
                        "The segment demonstrates Pydantic request and response validation.",
                        "The request schema validates input before it enters the handler.",
                        841,
                        1710,
                    )
                ],
            ),
            ProviderSegmentSource(
                sourceKey="database",
                timeRangeSeconds=(3000, 4000),
                evidence=[
                    _transcript_evidence(
                        "The segment demonstrates persistence and database CRUD operations.",
                        "Create, query, update, and delete the stored record.",
                        1711,
                        2860,
                    )
                ],
            ),
        ],
    )
    semantic_response = {
        "segmentAnnotations": [
            {
                "segmentIndex": 0,
                "contentSummary": "FastAPI Routing与HTTP端点映射。",
                "topics": ["Routing", "HTTP"],
            },
            {
                "segmentIndex": 1,
                "contentSummary": "Pydantic请求与响应数据校验。",
                "topics": ["Pydantic", "Data Validation"],
            },
            {
                "segmentIndex": 2,
                "contentSummary": "数据库持久化与CRUD操作。",
                "topics": ["Database", "CRUD"],
            },
        ],
        "coverage": [
            {
                "knowledgeIndex": 0,
                "segmentIndex": 0,
                "evidenceIndexes": [0],
                "coverageType": "demonstration",
                "coverageStrength": "full",
                "confidence": 0.97,
                "reason": "字幕和示例直接演示路由定义。",
            },
            {
                "knowledgeIndex": 1,
                "segmentIndex": 1,
                "evidenceIndexes": [1],
                "coverageType": "demonstration",
                "coverageStrength": "full",
                "confidence": 0.97,
                "reason": "字幕和代码直接演示Pydantic校验。",
            },
            {
                "knowledgeIndex": 2,
                "segmentIndex": 2,
                "evidenceIndexes": [2],
                "coverageType": "demonstration",
                "coverageStrength": "full",
                "confidence": 0.95,
                "reason": "字幕和代码直接演示数据库持久化。",
            },
            {
                "knowledgeIndex": 3,
                "segmentIndex": 2,
                "evidenceIndexes": [2],
                "coverageType": "demonstration",
                "coverageStrength": "partial",
                "confidence": 0.9,
                "reason": "同一片段展示完整数据库CRUD数据流。",
            },
        ],
    }
    return FastApiEvidenceFixture(
        knowledge_graph=knowledge_graph,
        provider=MockVideoProvider([document]),
        document=document,
        semantic_response=semantic_response,
    )


__all__ = ["FastApiEvidenceFixture", "build_fastapi_crud_evidence_fixture"]
