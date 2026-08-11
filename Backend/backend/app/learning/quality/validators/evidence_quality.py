from __future__ import annotations

from ...contracts import EvidenceGraph, KnowledgeGraph
from .base import QualityEvaluation


class EvidenceQualityValidator:
    _PRECISE_EVIDENCE_KINDS = {
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "manual_verified",
    }

    def evaluate(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> QualityEvaluation:
        result = QualityEvaluation()
        owner_id = evidence_graph.artifact_id
        resources = {item.id: item for item in evidence_graph.resources}
        segments = {item.id: item for item in evidence_graph.segments}
        evidence = {item.id: item for item in evidence_graph.evidence}

        invalid_timestamps = sorted(
            segment.id
            for segment in evidence_graph.segments
            if segment.resource_id not in resources
            or segment.start_seconds < 0
            or segment.end_seconds <= segment.start_seconds
            or segment.end_seconds > resources[segment.resource_id].duration_seconds
        )
        result.add(
            rule="unsupported_timestamp",
            passed=not invalid_timestamps,
            evidence=invalid_timestamps,
            owner_id=owner_id,
            severity="blocker",
            target_type="content_segment",
            target_id=invalid_timestamps[0] if invalid_timestamps else owner_id,
            description="segment ranges must be provider-backed and within video duration",
        )

        stale_fingerprints = sorted(
            {
                *(
                    segment.id
                    for segment in evidence_graph.segments
                    if segment.resource_id not in resources
                    or segment.resource_fingerprint
                    != resources[segment.resource_id].content_fingerprint
                ),
                *(
                    item.id
                    for item in evidence_graph.evidence
                    if item.resource_id not in resources
                    or item.resource_fingerprint
                    != resources[item.resource_id].content_fingerprint
                ),
            }
        )
        result.add(
            rule="version_compatibility",
            passed=not stale_fingerprints,
            evidence=stale_fingerprints,
            owner_id=owner_id,
            severity="blocker",
            target_type="evidence_graph",
            target_id=stale_fingerprints[0] if stale_fingerprints else owner_id,
            description="segments and evidence must match the current resource fingerprint",
        )

        invalid_evidence_segments: list[str] = []
        for segment in evidence_graph.segments:
            verified = [
                evidence[item_id]
                for item_id in segment.evidence_refs
                if item_id in evidence
                and evidence[item_id].segment_id == segment.id
                and evidence[item_id].verification_status == "verified"
                and evidence[item_id].kind in self._PRECISE_EVIDENCE_KINDS
                and evidence[item_id].source_range.end_offset
                > evidence[item_id].source_range.start_offset
            ]
            if not verified:
                invalid_evidence_segments.append(segment.id)
        result.add(
            rule="evidence_validity",
            passed=not invalid_evidence_segments,
            evidence=sorted(invalid_evidence_segments),
            owner_id=owner_id,
            severity="blocker",
            target_type="content_segment",
            target_id=invalid_evidence_segments[0] if invalid_evidence_segments else owner_id,
            description="every recommendable segment must have verified content evidence",
        )

        valid_full_coverage: set[str] = set()
        insufficient_edges: list[str] = []
        for edge in evidence_graph.coverage_edges:
            segment = segments.get(edge.segment_id)
            cited = [evidence.get(item_id) for item_id in edge.evidence_refs]
            valid = bool(
                segment is not None
                and cited
                and all(
                    item is not None
                    and item.segment_id == edge.segment_id
                    and item.verification_status == "verified"
                    and item.kind in self._PRECISE_EVIDENCE_KINDS
                    for item in cited
                )
            )
            if not valid:
                insufficient_edges.append(edge.id)
            elif edge.coverage_strength == "full":
                valid_full_coverage.add(edge.knowledge_id)
        required_ids = {
            item.id for item in knowledge_graph.nodes if item.importance == "required"
        }
        missing_required = sorted(required_ids - valid_full_coverage)
        result.add(
            rule="knowledge_coverage",
            passed=not missing_required,
            evidence=missing_required or sorted(valid_full_coverage & required_ids),
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge",
            target_id=missing_required[0] if missing_required else owner_id,
            description="required knowledge must have verified full evidence coverage",
        )
        result.add(
            rule="evidence_validity",
            passed=not insufficient_edges,
            evidence=sorted(insufficient_edges),
            owner_id=owner_id,
            severity="major",
            target_type="coverage_edge",
            target_id=insufficient_edges[0] if insufficient_edges else owner_id,
            description="metadata-only or unverified evidence cannot support precise segment recommendations",
        )
        return result


__all__ = ["EvidenceQualityValidator"]
