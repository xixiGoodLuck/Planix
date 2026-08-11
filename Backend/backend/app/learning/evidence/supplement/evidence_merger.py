from __future__ import annotations

from ...contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    SegmentEvidence,
    VideoResource,
)


class EvidenceGraphMergeError(ValueError):
    pass


class EvidenceGraphMerger:
    """Creates the next EvidenceGraph version by appending only new objects."""

    def merge(
        self,
        existing: EvidenceGraph,
        *,
        resource: VideoResource,
        segments: list[ContentSegment],
        evidence: list[SegmentEvidence],
        coverage_edges: list[CoverageEdge],
    ) -> EvidenceGraph:
        self._reject_collisions(existing, resource, segments, evidence, coverage_edges)
        return EvidenceGraph(
            artifactId=existing.artifact_id,
            version=existing.version + 1,
            schemaVersion=existing.schema_version,
            knowledgeGraphRef=existing.knowledge_graph_ref.model_copy(deep=True),
            resources=[
                *(item.model_copy(deep=True) for item in existing.resources),
                resource.model_copy(deep=True),
            ],
            segments=[
                *(item.model_copy(deep=True) for item in existing.segments),
                *(item.model_copy(deep=True) for item in segments),
            ],
            evidence=[
                *(item.model_copy(deep=True) for item in existing.evidence),
                *(item.model_copy(deep=True) for item in evidence),
            ],
            coverageEdges=[
                *(item.model_copy(deep=True) for item in existing.coverage_edges),
                *(item.model_copy(deep=True) for item in coverage_edges),
            ],
        )

    @staticmethod
    def _reject_collisions(
        existing: EvidenceGraph,
        resource: VideoResource,
        segments: list[ContentSegment],
        evidence: list[SegmentEvidence],
        coverage_edges: list[CoverageEdge],
    ) -> None:
        existing_resource_ids = {item.id for item in existing.resources}
        existing_identities = {
            (item.provider, item.external_id) for item in existing.resources
        }
        existing_fingerprints = {
            item.content_fingerprint for item in existing.resources
        }
        if (
            resource.id in existing_resource_ids
            or (resource.provider, resource.external_id) in existing_identities
            or resource.content_fingerprint in existing_fingerprints
        ):
            raise EvidenceGraphMergeError(
                "supplement resource duplicates an existing EvidenceGraph resource"
            )
        EvidenceGraphMerger._unique_new_ids(
            {item.id for item in existing.segments},
            [item.id for item in segments],
            "segment",
        )
        EvidenceGraphMerger._unique_new_ids(
            {item.id for item in existing.evidence},
            [item.id for item in evidence],
            "evidence",
        )
        EvidenceGraphMerger._unique_new_ids(
            {item.id for item in existing.coverage_edges},
            [item.id for item in coverage_edges],
            "coverage edge",
        )

    @staticmethod
    def _unique_new_ids(existing_ids: set[str], new_ids: list[str], label: str) -> None:
        if len(new_ids) != len(set(new_ids)) or existing_ids.intersection(new_ids):
            raise EvidenceGraphMergeError(
                f"supplement {label} ids must be new and unique"
            )


__all__ = ["EvidenceGraphMergeError", "EvidenceGraphMerger"]
