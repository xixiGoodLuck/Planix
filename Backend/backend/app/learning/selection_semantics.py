from __future__ import annotations

from dataclasses import dataclass

from .contracts import CoverageEdge, EvidenceGraph, LearningScope


_CONTENT_EVIDENCE_KINDS = {
    "transcript_span",
    "caption_span",
    "chapter_marker",
    "manual_verified",
}


@dataclass(frozen=True)
class SelectedKnowledgeCoverage:
    selected_knowledge_ids: tuple[str, ...]
    selected_coverage_edges: tuple[CoverageEdge, ...]
    selected_evidence_refs: tuple[str, ...]


def verified_coverage_edges(evidence_graph: EvidenceGraph) -> tuple[CoverageEdge, ...]:
    """Return coverage edges backed only by current, verified content evidence."""

    segment_ids = {item.id for item in evidence_graph.segments}
    evidence = {item.id: item for item in evidence_graph.evidence}
    valid: list[CoverageEdge] = []
    for edge in evidence_graph.coverage_edges:
        if edge.segment_id not in segment_ids or not edge.evidence_refs:
            continue
        if all(
            evidence_id in evidence
            and evidence[evidence_id].segment_id == edge.segment_id
            and evidence[evidence_id].verification_status == "verified"
            and evidence[evidence_id].kind in _CONTENT_EVIDENCE_KINDS
            for evidence_id in edge.evidence_refs
        ):
            valid.append(edge)
    return tuple(valid)


def resolve_selected_knowledge_coverage(
    evidence_graph: EvidenceGraph,
    selected_segment_refs: list[str] | tuple[str, ...] | set[str],
) -> SelectedKnowledgeCoverage:
    """Project every verified FULL edge carried by the final selected segments."""

    selected_ids = set(selected_segment_refs)
    edges = tuple(
        edge
        for edge in verified_coverage_edges(evidence_graph)
        if edge.segment_id in selected_ids and edge.coverage_strength == "full"
    )
    return SelectedKnowledgeCoverage(
        selected_knowledge_ids=tuple(
            dict.fromkeys(edge.knowledge_id for edge in edges)
        ),
        selected_coverage_edges=edges,
        selected_evidence_refs=tuple(
            dict.fromkeys(
                evidence_id for edge in edges for evidence_id in edge.evidence_refs
            )
        ),
    )


def range_union_duration_seconds(
    evidence_graph: EvidenceGraph,
    segment_refs: list[str] | tuple[str, ...] | set[str],
) -> int:
    """Measure the union of selected ranges independently for each resource."""

    selected_ids = set(segment_refs)
    by_resource: dict[str, list[tuple[int, int]]] = {}
    for segment in evidence_graph.segments:
        if segment.id in selected_ids:
            by_resource.setdefault(segment.resource_id, []).append(
                (segment.start_seconds, segment.end_seconds)
            )

    total = 0
    for ranges in by_resource.values():
        ordered = sorted(ranges)
        if not ordered:
            continue
        start, end = ordered[0]
        for next_start, next_end in ordered[1:]:
            if next_start <= end:
                end = max(end, next_end)
                continue
            total += end - start
            start, end = next_start, next_end
        total += end - start
    return total


def marginal_duration_seconds(
    evidence_graph: EvidenceGraph,
    selected_segment_refs: list[str] | tuple[str, ...] | set[str],
    candidate_segment_refs: list[str] | tuple[str, ...] | set[str],
) -> int:
    selected = tuple(dict.fromkeys(selected_segment_refs))
    combined = tuple(dict.fromkeys((*selected, *candidate_segment_refs)))
    return range_union_duration_seconds(
        evidence_graph, combined
    ) - range_union_duration_seconds(evidence_graph, selected)


def explicit_budget_seconds(scope: LearningScope | None) -> int | None:
    if scope is None:
        return None
    minutes = (
        scope.content_budget.maximum_total_minutes
        or scope.content_budget.target_total_minutes
    )
    return minutes * 60 if minutes is not None else None


__all__ = [
    "SelectedKnowledgeCoverage",
    "explicit_budget_seconds",
    "marginal_duration_seconds",
    "range_union_duration_seconds",
    "resolve_selected_knowledge_coverage",
    "verified_coverage_edges",
]
