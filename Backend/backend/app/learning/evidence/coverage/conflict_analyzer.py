from __future__ import annotations

from dataclasses import dataclass

from ...contracts import EvidenceGraph, KnowledgeGraph
from .coverage_report import (
    SegmentCoverageAnalysis,
    VersionConflict,
    VersionObservation,
)


@dataclass(frozen=True)
class ConflictAnalysisResult:
    conflicts: list[VersionConflict]
    redundancy: list[SegmentCoverageAnalysis]


class ConflictAnalyzer:
    def analyze(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> ConflictAnalysisResult:
        return ConflictAnalysisResult(
            conflicts=self._version_conflicts(knowledge_graph, evidence_graph),
            redundancy=self._segment_relationships(knowledge_graph, evidence_graph),
        )

    @staticmethod
    def _segment_relationships(
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> list[SegmentCoverageAnalysis]:
        segments = {item.id: item for item in evidence_graph.segments}
        relationships: list[SegmentCoverageAnalysis] = []
        for node in knowledge_graph.nodes:
            edges = [item for item in evidence_graph.coverage_edges if item.knowledge_id == node.id]
            segment_refs = list(dict.fromkeys(item.segment_id for item in edges))
            if len(segment_refs) < 2:
                continue
            segment_set = set(segment_refs)
            context_required = any(
                set(segments[segment_id].context_segment_refs) & segment_set
                for segment_id in segment_refs
            )
            coverage_types = {item.coverage_type for item in edges}
            if context_required:
                classification = "CONTEXT_REQUIRED"
                reason = "At least one covered segment explicitly depends on another segment."
            elif len(coverage_types) > 1:
                classification = "COMPLEMENTARY"
                reason = "Segments provide different coverage types for the same knowledge."
            else:
                classification = "REDUNDANT"
                reason = "Segments repeat the same coverage type for the same knowledge."
            relationships.append(
                SegmentCoverageAnalysis(
                    knowledgeId=node.id,
                    classification=classification,
                    segmentRefs=segment_refs,
                    evidenceRefs=list(
                        dict.fromkeys(
                            evidence_id for edge in edges for evidence_id in edge.evidence_refs
                        )
                    ),
                    reason=reason,
                )
            )
        return relationships

    @staticmethod
    def _version_conflicts(
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> list[VersionConflict]:
        segments = {item.id: item for item in evidence_graph.segments}
        resources = {item.id: item for item in evidence_graph.resources}
        conflicts: list[VersionConflict] = []
        for node in knowledge_graph.nodes:
            segment_refs = list(
                dict.fromkeys(
                    item.segment_id
                    for item in evidence_graph.coverage_edges
                    if item.knowledge_id == node.id
                )
            )
            technologies: dict[str, tuple[str, dict[str, dict[str, set[str]]]]] = {}
            for segment_id in segment_refs:
                segment = segments[segment_id]
                resource = resources[segment.resource_id]
                for technology, version in resource.technology_versions.items():
                    normalized = technology.strip().casefold()
                    clean_version = version.strip()
                    if not normalized or not clean_version:
                        continue
                    display, versions = technologies.setdefault(
                        normalized,
                        (technology.strip(), {}),
                    )
                    refs = versions.setdefault(
                        clean_version,
                        {"resources": set(), "segments": set()},
                    )
                    refs["resources"].add(resource.id)
                    refs["segments"].add(segment_id)
            for normalized in sorted(technologies):
                display, versions = technologies[normalized]
                if len(versions) < 2:
                    continue
                observations = [
                    VersionObservation(
                        version=version,
                        resourceRefs=sorted(refs["resources"]),
                        segmentRefs=sorted(refs["segments"]),
                    )
                    for version, refs in sorted(versions.items())
                ]
                conflicts.append(
                    VersionConflict(
                        knowledgeId=node.id,
                        technology=display,
                        observations=observations,
                        reason=f"Evidence for {node.name} uses multiple {display} versions.",
                    )
                )
        return conflicts


__all__ = ["ConflictAnalysisResult", "ConflictAnalyzer"]
