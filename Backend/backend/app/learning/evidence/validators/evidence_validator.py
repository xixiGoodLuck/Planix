from __future__ import annotations

from urllib.parse import urlsplit

from ...contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    KnowledgeGraph,
    SegmentEvidence,
    VideoResource,
)
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from ..providers import ProviderVideoDocument


class EvidenceValidator:
    _CONTENT_EVIDENCE_KINDS = {
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "manual_verified",
    }

    def __init__(self, artifact_validator: LearningArtifactValidator | None = None):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()

    def validate_provider_documents(
        self,
        documents: list[ProviderVideoDocument],
    ) -> None:
        if not documents:
            self._fail("video_resource_required", "providerDocuments", "no video resources found")
        identities: set[tuple[str, str]] = set()
        for document_index, document in enumerate(documents):
            metadata = document.metadata
            identity = (metadata.provider, metadata.external_id)
            if identity in identities:
                self._fail(
                    "duplicate_video_resource",
                    f"providerDocuments.{document_index}",
                    "provider returned the same video more than once",
                )
            identities.add(identity)
            self._validate_url(metadata.canonical_url, f"providerDocuments.{document_index}.canonicalUrl")
            if metadata.duration_seconds <= 0:
                self._fail(
                    "video_duration",
                    f"providerDocuments.{document_index}.durationSeconds",
                    "video duration must be greater than zero",
                )
            if not document.segments:
                self._fail(
                    "content_segment_required",
                    f"providerDocuments.{document_index}.segments",
                    "video resource has no provider-backed content segments",
                )
            for segment_index, segment in enumerate(document.segments):
                start, end = segment.time_range_seconds
                path = f"providerDocuments.{document_index}.segments.{segment_index}"
                if start < 0 or end <= start or end > metadata.duration_seconds:
                    self._fail(
                        "unsupported_timestamp",
                        path,
                        "provider segment range is outside the video duration",
                    )
                if not any(
                    item.verification_status == "verified"
                    and item.kind in self._CONTENT_EVIDENCE_KINDS
                    for item in segment.evidence
                ):
                    self._fail(
                        "evidence_validity",
                        path,
                        "provider segment has no verified content evidence",
                    )

    def validate_resources(self, resources: list[VideoResource]) -> None:
        if not resources:
            self._fail("video_resource_required", "evidenceGraph.resources", "no video resources found")
        self._unique((item.id for item in resources), "resource_id", "evidenceGraph.resources")
        for resource in resources:
            if resource.duration_seconds <= 0:
                self._fail(
                    "video_duration",
                    f"evidenceGraph.resources.{resource.id}.durationSeconds",
                    "video duration must be greater than zero",
                )
            self._validate_url(
                resource.canonical_url,
                f"evidenceGraph.resources.{resource.id}.canonicalUrl",
            )

    def validate_segments(
        self,
        resources: list[VideoResource],
        segments: list[ContentSegment],
    ) -> None:
        if not segments:
            self._fail(
                "content_segment_required",
                "evidenceGraph.segments",
                "no content segments found",
            )
        self._unique((item.id for item in segments), "segment_id", "evidenceGraph.segments")
        resource_map = {item.id: item for item in resources}
        segment_ids = {item.id for item in segments}
        context_edges: list[tuple[str, str]] = []
        for segment in segments:
            resource = resource_map.get(segment.resource_id)
            if resource is None:
                self._fail(
                    "segment_resource_reference",
                    f"evidenceGraph.segments.{segment.id}.resourceId",
                    "segment references a missing resource",
                )
            if segment.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "version_compatibility",
                    f"evidenceGraph.segments.{segment.id}.resourceFingerprint",
                    "segment fingerprint does not match the current resource",
                )
            if (
                segment.start_seconds < 0
                or segment.end_seconds <= segment.start_seconds
                or segment.end_seconds > resource.duration_seconds
            ):
                self._fail(
                    "unsupported_timestamp",
                    f"evidenceGraph.segments.{segment.id}",
                    "segment range is outside the video duration",
                )
            missing_context = set(segment.context_segment_refs) - segment_ids
            if missing_context:
                self._fail(
                    "context_segment_reference",
                    f"evidenceGraph.segments.{segment.id}.contextSegmentRefs",
                    f"segment references missing context: {sorted(missing_context)}",
                )
            context_edges.extend(
                (context_id, segment.id) for context_id in segment.context_segment_refs
            )
        self._assert_context_acyclic(segment_ids, context_edges)

    def validate_evidence(
        self,
        resources: list[VideoResource],
        segments: list[ContentSegment],
        evidence: list[SegmentEvidence],
    ) -> None:
        if not evidence:
            self._fail("evidence_required", "evidenceGraph.evidence", "no segment evidence found")
        self._unique((item.id for item in evidence), "evidence_id", "evidenceGraph.evidence")
        resource_map = {item.id: item for item in resources}
        segment_map = {item.id: item for item in segments}
        for item in evidence:
            resource = resource_map.get(item.resource_id)
            segment = segment_map.get(item.segment_id)
            if resource is None or segment is None:
                self._fail(
                    "evidence_reference",
                    f"evidenceGraph.evidence.{item.id}",
                    "evidence references a missing resource or segment",
                )
            if segment.resource_id != resource.id:
                self._fail(
                    "evidence_reference",
                    f"evidenceGraph.evidence.{item.id}.resourceId",
                    "evidence and segment belong to different resources",
                )
            if item.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "version_compatibility",
                    f"evidenceGraph.evidence.{item.id}.resourceFingerprint",
                    "evidence fingerprint does not match the current resource",
                )
            if item.source_range.end_offset <= item.source_range.start_offset:
                self._fail(
                    "evidence_validity",
                    f"evidenceGraph.evidence.{item.id}.sourceRange",
                    "evidence source range is invalid",
                )
        evidence_map = {item.id: item for item in evidence}
        for segment in segments:
            missing = set(segment.evidence_refs) - set(evidence_map)
            if missing:
                self._fail(
                    "segment_evidence_reference",
                    f"evidenceGraph.segments.{segment.id}.evidenceRefs",
                    f"segment references missing evidence: {sorted(missing)}",
                )
            valid = [
                evidence_map[item_id]
                for item_id in segment.evidence_refs
                if item_id in evidence_map
                and evidence_map[item_id].segment_id == segment.id
                and evidence_map[item_id].verification_status == "verified"
                and evidence_map[item_id].kind in self._CONTENT_EVIDENCE_KINDS
            ]
            if not valid:
                self._fail(
                    "evidence_validity",
                    f"evidenceGraph.segments.{segment.id}",
                    "segment has no verified content evidence",
                )

    def validate_coverage(
        self,
        knowledge_graph: KnowledgeGraph,
        segments: list[ContentSegment],
        evidence: list[SegmentEvidence],
        coverage_edges: list[CoverageEdge],
    ) -> None:
        if not coverage_edges:
            self._fail(
                "coverage_required",
                "evidenceGraph.coverageEdges",
                "no knowledge coverage was generated",
            )
        self._unique(
            (item.id for item in coverage_edges),
            "coverage_edge_id",
            "evidenceGraph.coverageEdges",
        )
        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        segment_ids = {item.id for item in segments}
        evidence_map = {item.id: item for item in evidence}
        for edge in coverage_edges:
            if edge.knowledge_id not in knowledge_ids:
                self._fail(
                    "coverage_knowledge_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.knowledgeId",
                    "coverage references missing knowledge",
                )
            if edge.segment_id not in segment_ids:
                self._fail(
                    "coverage_segment_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.segmentId",
                    "coverage references missing segment",
                )
            if not edge.evidence_refs:
                self._fail(
                    "coverage_evidence_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.evidenceRefs",
                    "coverage must reference evidence",
                )
            for evidence_id in edge.evidence_refs:
                item = evidence_map.get(evidence_id)
                if (
                    item is None
                    or item.segment_id != edge.segment_id
                    or item.verification_status != "verified"
                    or item.kind not in self._CONTENT_EVIDENCE_KINDS
                ):
                    self._fail(
                        "coverage_evidence_reference",
                        f"evidenceGraph.coverageEdges.{edge.id}.evidenceRefs",
                        "coverage references missing or invalid segment evidence",
                    )

    def validate_graph(
        self,
        knowledge_graph: KnowledgeGraph,
        graph: EvidenceGraph,
    ) -> None:
        self.validate_resources(graph.resources)
        self.validate_segments(graph.resources, graph.segments)
        self.validate_evidence(graph.resources, graph.segments, graph.evidence)
        self.validate_coverage(
            knowledge_graph,
            graph.segments,
            graph.evidence,
            graph.coverage_edges,
        )
        self.artifact_validator.validate_evidence_graph(knowledge_graph, graph)

    @staticmethod
    def _validate_url(value: str, path: str) -> None:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LearningArtifactValidationError(
                "video_url",
                path,
                "video canonical URL must be an absolute HTTP(S) URL",
            )

    @staticmethod
    def _unique(values, label: str, path: str) -> None:
        seen: set[str] = set()
        for value in values:
            if value in seen:
                raise LearningArtifactValidationError(
                    f"duplicate_{label}",
                    path,
                    f"duplicate id: {value}",
                )
            seen.add(value)

    @staticmethod
    def _assert_context_acyclic(
        segment_ids: set[str],
        edges: list[tuple[str, str]],
    ) -> None:
        outgoing = {item: [] for item in segment_ids}
        indegree = {item: 0 for item in segment_ids}
        for source, target in edges:
            outgoing[source].append(target)
            indegree[target] += 1
        ready = [item for item, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            source = ready.pop()
            visited += 1
            for target in outgoing[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(segment_ids):
            raise LearningArtifactValidationError(
                "context_segment_cycle",
                "evidenceGraph.segments.contextSegmentRefs",
                "context segment dependencies contain a cycle",
            )

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise LearningArtifactValidationError(rule, path, message)


__all__ = ["EvidenceValidator"]
