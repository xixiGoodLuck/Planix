from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field

from ...contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContract,
    SegmentEvidence,
    VideoResource,
)
from ...generators import LearningSemanticModel, RouterLearningModel
from ...generators.base import artifact_ref, generated_id, require_index
from ..providers import ProviderSegmentSource, ProviderVideoDocument


EVIDENCE_SYSTEM = """
You map provider-supplied video content evidence to an existing KnowledgeGraph. The provider, not you, owns every
URL, video duration, fingerprint, source range, and video time range. You may only summarize each supplied segment,
name its topics, and describe evidence-backed coverage using the supplied zero-based indexes. Never invent or return
a URL, duration, start time, end time, timestamp, source range, external id, fingerprint, artifact id, or version.
Coverage must cite verified evidence indexes belonging to the same segment. Return JSON only and do not reveal hidden
reasoning.
""".strip()


NonNegativeIndex = Annotated[int, Field(ge=0)]


class SegmentAnnotationDraft(LearningContract):
    segment_index: NonNegativeIndex
    content_summary: str = Field(min_length=1)
    topics: list[str] = Field(min_length=1, max_length=12)


class CoverageDraft(LearningContract):
    knowledge_index: NonNegativeIndex
    segment_index: NonNegativeIndex
    evidence_indexes: list[NonNegativeIndex] = Field(min_length=1)
    coverage_type: Literal[
        "introduction",
        "explanation",
        "demonstration",
        "practice",
        "review",
    ]
    coverage_strength: Literal["full", "partial", "supplementary"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class EvidenceSemanticDraft(LearningContract):
    segment_annotations: list[SegmentAnnotationDraft] = Field(min_length=1, max_length=60)
    coverage: list[CoverageDraft] = Field(min_length=1, max_length=160)


@dataclass(frozen=True)
class EvidenceBuildResult:
    evidence_graph: EvidenceGraph
    model_usage: dict[str, Any]


@dataclass(frozen=True)
class _PreparedSegment:
    resource: VideoResource
    source: ProviderSegmentSource
    segment_id: str
    evidence: list[SegmentEvidence]


class EvidenceBuilder:
    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model or RouterLearningModel()

    def build(
        self,
        knowledge_graph: KnowledgeGraph,
        documents: list[ProviderVideoDocument],
    ) -> EvidenceBuildResult:
        resources = self._build_resources(documents)
        prepared_segments = self._prepare_segments(documents, resources)
        evidence = [item for segment in prepared_segments for item in segment.evidence]
        evidence_index = {item.id: index for index, item in enumerate(evidence)}

        response = self.model.complete(
            stage="learning_evidence_semantics",
            feature="learning_evidence_generation",
            system=EVIDENCE_SYSTEM,
            payload={
                "knowledge": [
                    {
                        "index": index,
                        "name": node.name,
                        "explanation": node.explanation,
                        "whyRequired": node.why_required,
                        "importance": node.importance,
                    }
                    for index, node in enumerate(knowledge_graph.nodes)
                ],
                "segments": [
                    {
                        "index": index,
                        "videoTitle": segment.resource.title,
                        "evidence": [
                            {
                                "index": evidence_index[persisted.id],
                                "kind": source.kind,
                                "supportedClaim": source.supported_claim,
                                "sourceExcerpt": source.source_excerpt,
                                "verificationStatus": source.verification_status,
                            }
                            for source, persisted in zip(
                                segment.source.evidence,
                                segment.evidence,
                                strict=True,
                            )
                        ],
                    }
                    for index, segment in enumerate(prepared_segments)
                ],
            },
            response_type=EvidenceSemanticDraft,
            max_tokens=3800,
        )
        annotations = self._annotations_by_segment(
            response.value,
            len(prepared_segments),
        )
        raw_transcript = [
            value
            for segment in prepared_segments
            for source in segment.source.evidence
            for value in (source.supported_claim, source.source_excerpt)
            if value
        ]
        annotations = {
            index: annotation.model_copy(
                update={
                    "content_summary": self._safe_generated_text(
                        annotation.content_summary,
                        raw_transcript,
                        "Verified transcript segment.",
                    ),
                    "topics": self._safe_topics(
                        annotation.topics,
                        raw_transcript,
                    ),
                }
            )
            for index, annotation in annotations.items()
        }
        segments = [
            self._build_segment(prepared, annotations[index])
            for index, prepared in enumerate(prepared_segments)
        ]
        coverage_edges = self._build_coverage(
            response.value,
            knowledge_graph,
            segments,
            evidence,
            raw_transcript,
        )
        graph_id = generated_id(
            "evidence-graph",
            knowledge_graph.artifact_id,
            knowledge_graph.version,
            "|".join(item.content_fingerprint for item in resources),
        )
        return EvidenceBuildResult(
            evidence_graph=EvidenceGraph(
                artifactId=graph_id,
                knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
                resources=resources,
                segments=segments,
                evidence=evidence,
                coverageEdges=coverage_edges,
            ),
            model_usage=response.model_usage,
        )

    @staticmethod
    def _build_resources(documents: list[ProviderVideoDocument]) -> list[VideoResource]:
        resources: list[VideoResource] = []
        for index, document in enumerate(documents):
            metadata = document.metadata
            resource_id = generated_id(
                "video",
                f"{metadata.provider}:{metadata.external_id}",
                index,
                metadata.content_fingerprint,
            )
            resources.append(
                VideoResource(
                    id=resource_id,
                    provider=metadata.provider,
                    externalId=metadata.external_id,
                    canonicalUrl=metadata.canonical_url,
                    title=metadata.title,
                    author=metadata.author,
                    language=metadata.language,
                    technologyVersions=metadata.technology_versions,
                    durationSeconds=metadata.duration_seconds,
                    publishedAt=metadata.published_at,
                    contentFingerprint=metadata.content_fingerprint,
                )
            )
        return resources

    @staticmethod
    def _prepare_segments(
        documents: list[ProviderVideoDocument],
        resources: list[VideoResource],
    ) -> list[_PreparedSegment]:
        prepared: list[_PreparedSegment] = []
        for document, resource in zip(documents, resources, strict=True):
            for segment_index, source in enumerate(document.segments):
                segment_id = generated_id(
                    "segment",
                    resource.id,
                    segment_index,
                    source.source_key,
                )
                evidence = [
                    SegmentEvidence(
                        id=generated_id(
                            "evidence",
                            segment_id,
                            evidence_index,
                            item.supported_claim,
                        ),
                        resourceId=resource.id,
                        resourceFingerprint=resource.content_fingerprint,
                        segmentId=segment_id,
                        kind=item.kind,
                        supportedClaim="Verified transcript evidence for this segment.",
                        sourceRange=item.source_range,
                        sourceExcerpt=None,
                        verificationStatus=item.verification_status,
                    )
                    for evidence_index, item in enumerate(source.evidence)
                ]
                prepared.append(
                    _PreparedSegment(
                        resource=resource,
                        source=source,
                        segment_id=segment_id,
                        evidence=evidence,
                    )
                )
        return prepared

    @staticmethod
    def _annotations_by_segment(
        draft: EvidenceSemanticDraft,
        segment_count: int,
    ) -> dict[int, SegmentAnnotationDraft]:
        annotations: dict[int, SegmentAnnotationDraft] = {}
        for annotation in draft.segment_annotations:
            index = require_index(
                annotation.segment_index,
                segment_count,
                stage="learning_evidence_semantics",
                field="segmentAnnotations.segmentIndex",
            )
            if index in annotations:
                raise ValueError(f"duplicate semantic annotation for segment index {index}")
            annotations[index] = annotation
        missing = set(range(segment_count)) - set(annotations)
        if missing:
            raise ValueError(f"missing semantic annotations for segment indexes {sorted(missing)}")
        return annotations

    @staticmethod
    def _build_segment(
        prepared: _PreparedSegment,
        annotation: SegmentAnnotationDraft,
    ) -> ContentSegment:
        start_seconds, end_seconds = prepared.source.time_range_seconds
        return ContentSegment(
            id=prepared.segment_id,
            resourceId=prepared.resource.id,
            resourceFingerprint=prepared.resource.content_fingerprint,
            startSeconds=start_seconds,
            endSeconds=end_seconds,
            contentSummary=annotation.content_summary,
            topics=annotation.topics,
            evidenceRefs=[item.id for item in prepared.evidence],
        )

    @staticmethod
    def _build_coverage(
        draft: EvidenceSemanticDraft,
        knowledge_graph: KnowledgeGraph,
        segments: list[ContentSegment],
        evidence: list[SegmentEvidence],
        raw_transcript: list[str],
    ) -> list[CoverageEdge]:
        coverage: list[CoverageEdge] = []
        for index, item in enumerate(draft.coverage):
            knowledge_index = require_index(
                item.knowledge_index,
                len(knowledge_graph.nodes),
                stage="learning_evidence_semantics",
                field=f"coverage[{index}].knowledgeIndex",
            )
            segment_index = require_index(
                item.segment_index,
                len(segments),
                stage="learning_evidence_semantics",
                field=f"coverage[{index}].segmentIndex",
            )
            evidence_refs = [
                evidence[
                    require_index(
                        evidence_index,
                        len(evidence),
                        stage="learning_evidence_semantics",
                        field=f"coverage[{index}].evidenceIndexes",
                    )
                ].id
                for evidence_index in item.evidence_indexes
            ]
            knowledge_id = knowledge_graph.nodes[knowledge_index].id
            segment_id = segments[segment_index].id
            coverage.append(
                CoverageEdge(
                    id=generated_id(
                        "coverage",
                        segment_id,
                        index,
                        knowledge_id,
                    ),
                    knowledgeId=knowledge_id,
                    segmentId=segment_id,
                    evidenceRefs=list(dict.fromkeys(evidence_refs)),
                    coverageType=item.coverage_type,
                    coverageStrength=item.coverage_strength,
                    confidence=item.confidence,
                    reason=EvidenceBuilder._safe_generated_text(
                        item.reason,
                        raw_transcript,
                        "Coverage is supported by verified transcript evidence.",
                    ),
                )
            )
        return coverage

    @staticmethod
    def _safe_generated_text(
        value: str,
        raw_transcript: list[str],
        fallback: str,
    ) -> str:
        normalized = value.strip()
        for source in raw_transcript:
            raw = source.strip()
            if raw and (raw in normalized or normalized in raw):
                return fallback
        return normalized

    @classmethod
    def _safe_topics(
        cls,
        topics: list[str],
        raw_transcript: list[str],
    ) -> list[str]:
        safe = [
            value
            for topic in topics
            if (
                value := cls._safe_generated_text(
                    topic,
                    raw_transcript,
                    "",
                )
            )
        ]
        return safe or ["Verified content"]


__all__ = [
    "CoverageDraft",
    "EvidenceBuildResult",
    "EvidenceBuilder",
    "EvidenceSemanticDraft",
    "SegmentAnnotationDraft",
]
