from __future__ import annotations

from dataclasses import dataclass

from ...contracts import (
    AlternativeRejection,
    ContentSelection,
    CoverageGap,
    EvidenceGraph,
    KnowledgeGraph,
    LearningScope,
    SelectedSegment,
    SelectionFacts,
    SelectionOmission,
)
from ...evidence.validators import EvidenceValidator
from ...generators import LearningGenerationError
from ...generators.base import artifact_ref, generated_id
from ...selection_semantics import (
    explicit_budget_seconds,
    marginal_duration_seconds,
    range_union_duration_seconds,
    resolve_selected_knowledge_coverage,
)
from .coverage_analyzer import CoverageAnalyzer, KnowledgeCoverageReport
from .redundancy_analyzer import RedundancyAnalyzer, RedundancyReport


@dataclass(frozen=True)
class ContentSelectionResult:
    content_selection: ContentSelection
    coverage_report: KnowledgeCoverageReport
    redundancy_report: RedundancyReport


class ContentSelector:
    _EVIDENCE_RANK = {
        "provider_metadata": 0,
        "chapter_marker": 1,
        "manual_verified": 2,
        "caption_span": 3,
        "transcript_span": 4,
    }

    def __init__(
        self,
        coverage_analyzer: CoverageAnalyzer | None = None,
        redundancy_analyzer: RedundancyAnalyzer | None = None,
        evidence_validator: EvidenceValidator | None = None,
    ):
        self.coverage_analyzer = coverage_analyzer or CoverageAnalyzer()
        self.redundancy_analyzer = redundancy_analyzer or RedundancyAnalyzer()
        self.evidence_validator = evidence_validator or EvidenceValidator()

    def select(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        scope: LearningScope | None = None,
    ) -> ContentSelectionResult:
        self.evidence_validator.validate_graph(knowledge_graph, evidence_graph)
        coverage_report = self.coverage_analyzer.analyze(knowledge_graph, evidence_graph)
        redundancy_report = self.redundancy_analyzer.analyze(
            knowledge_graph,
            evidence_graph,
        )
        valid_edges = self.coverage_analyzer.valid_coverage_edges(evidence_graph)
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        segments = {item.id: item for item in evidence_graph.segments}
        resources = {item.id: item for item in evidence_graph.resources}
        evidence = {item.id: item for item in evidence_graph.evidence}
        redundancy = {item.segment_id: item for item in redundancy_report.segments}

        required = {
            node.id for node in knowledge_graph.nodes if node.importance == "required"
        }
        full_required_by_segment = {
            segment.id: {
                edge.knowledge_id
                for edge in valid_edges
                if edge.segment_id == segment.id
                and edge.knowledge_id in required
                and edge.coverage_strength == "full"
            }
            for segment in evidence_graph.segments
        }
        selected_ids: list[str] = []
        selected_resources: set[str] = set()
        uncovered = set(required)
        while uncovered:
            candidates = [
                segment
                for segment in evidence_graph.segments
                if segment.id not in selected_ids
                and redundancy[segment.id].classification != "REDUNDANT"
                and full_required_by_segment[segment.id] & uncovered
            ]
            if not candidates:
                break
            chosen = sorted(
                candidates,
                key=lambda segment: self._candidate_key(
                    segment.id,
                    full_required_by_segment[segment.id] & uncovered,
                    segment.resource_id,
                    selected_resources,
                    segments,
                    resources,
                    evidence,
                ),
            )[0]
            selected_ids.append(chosen.id)
            selected_resources.add(chosen.resource_id)
            uncovered -= full_required_by_segment[chosen.id]

        self._add_context_segments(selected_ids, segments)
        budget_seconds = explicit_budget_seconds(scope)
        if budget_seconds is not None:
            self._add_priority_segments(
                {
                    node.id
                    for node in knowledge_graph.nodes
                    if node.importance == "important"
                },
                budget_seconds,
                selected_ids,
                valid_edges,
                evidence_graph,
                segments,
                resources,
                evidence,
                redundancy,
            )
            self._add_priority_segments(
                {
                    node.id
                    for node in knowledge_graph.nodes
                    if node.importance == "optional"
                    and self._scope_requests_knowledge(scope, node.name)
                },
                budget_seconds,
                selected_ids,
                valid_edges,
                evidence_graph,
                segments,
                resources,
                evidence,
                redundancy,
            )
        selected = [
            self._selected_segment(
                segment_id,
                index,
                knowledge,
                valid_edges,
                segments,
                resources,
                evidence,
                redundancy_report,
            )
            for index, segment_id in enumerate(selected_ids)
        ]
        selected_knowledge = {
            knowledge_id for item in selected for knowledge_id in item.knowledge_refs
        }
        coverage_by_knowledge = {
            item.knowledge_id: item for item in coverage_report.knowledge
        }
        resource_refs = [item.id for item in evidence_graph.resources]
        gaps = [
            self._coverage_gap(node.id, node.importance, coverage_by_knowledge[node.id], resource_refs)
            for node in knowledge_graph.nodes
            if node.id not in selected_knowledge
            and not coverage_by_knowledge[node.id].sufficient
        ]
        omissions = [
            self._selection_omission(
                node.id,
                node.name,
                node.importance,
                coverage_by_knowledge[node.id],
                selected_ids,
                evidence_graph,
                segments,
                scope,
            )
            for node in knowledge_graph.nodes
            if node.id not in selected_knowledge
            and coverage_by_knowledge[node.id].sufficient
            and node.importance in {"important", "optional"}
        ]
        selection_id = generated_id(
            "content-selection",
            evidence_graph.artifact_id,
            evidence_graph.version,
            "|".join(selected_ids),
        )
        return ContentSelectionResult(
            content_selection=ContentSelection(
                artifactId=selection_id,
                scopeRef=knowledge_graph.scope_ref,
                knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
                evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
                selectedSegments=selected,
                coverageGaps=gaps,
                selectionOmissions=omissions,
                totalDurationSeconds=0,
            ),
            coverage_report=coverage_report,
            redundancy_report=redundancy_report,
        )

    def _add_priority_segments(
        self,
        target_knowledge: set[str],
        budget_seconds: int,
        selected_ids: list[str],
        valid_edges,
        evidence_graph: EvidenceGraph,
        segments,
        resources,
        evidence,
        redundancy,
    ) -> None:
        while target_knowledge:
            resolved = resolve_selected_knowledge_coverage(evidence_graph, selected_ids)
            remaining = target_knowledge - set(resolved.selected_knowledge_ids)
            if not remaining:
                return
            selected_resources = {
                segments[segment_id].resource_id for segment_id in selected_ids
            }
            candidates: list[tuple[tuple, list[str]]] = []
            for segment in evidence_graph.segments:
                if segment.id in selected_ids:
                    continue
                if redundancy[segment.id].classification == "REDUNDANT":
                    continue
                if not any(
                    edge.segment_id == segment.id
                    and edge.knowledge_id in remaining
                    and edge.coverage_strength == "full"
                    for edge in valid_edges
                ):
                    continue
                bundle = [segment.id]
                self._add_context_segments(bundle, segments)
                added = [segment_id for segment_id in bundle if segment_id not in selected_ids]
                marginal = marginal_duration_seconds(
                    evidence_graph,
                    selected_ids,
                    added,
                )
                new_resources = len(
                    {
                        segments[segment_id].resource_id
                        for segment_id in added
                        if segments[segment_id].resource_id not in selected_resources
                    }
                )
                candidates.append(
                    (
                        (
                            -self._segment_evidence_rank(segment, evidence),
                            marginal,
                            len(added),
                            new_resources,
                            segment.id,
                        ),
                        bundle,
                    )
                )
            current_duration = range_union_duration_seconds(evidence_graph, selected_ids)
            fitting = [
                candidate
                for candidate in candidates
                if current_duration + candidate[0][1] <= budget_seconds
            ]
            if not fitting:
                return
            _, chosen_bundle = sorted(fitting, key=lambda item: item[0])[0]
            for segment_id in chosen_bundle:
                if segment_id not in selected_ids:
                    selected_ids.append(segment_id)

    @staticmethod
    def _scope_requests_knowledge(scope: LearningScope | None, knowledge_name: str) -> bool:
        if scope is None:
            return False
        normalized_name = " ".join(knowledge_name.casefold().split())
        normalized_scope = " ".join(
            f"{scope.user_goal} {scope.target_result}".casefold().split()
        )
        return bool(normalized_name and normalized_name in normalized_scope)

    def _selection_omission(
        self,
        knowledge_id: str,
        knowledge_name: str,
        importance: str,
        coverage,
        selected_ids: list[str],
        evidence_graph: EvidenceGraph,
        segments,
        scope: LearningScope | None,
    ) -> SelectionOmission:
        full_candidates = sorted(
            {
                edge.segment_id
                for edge in self.coverage_analyzer.valid_coverage_edges(evidence_graph)
                if edge.knowledge_id == knowledge_id
                and edge.coverage_strength == "full"
            }
        )
        marginal_candidates: list[int] = []
        for segment_id in full_candidates:
            bundle = [segment_id]
            self._add_context_segments(bundle, segments)
            marginal_candidates.append(
                marginal_duration_seconds(evidence_graph, selected_ids, bundle)
            )
        marginal = min(marginal_candidates, default=0)
        budget_seconds = explicit_budget_seconds(scope)
        remaining_budget = (
            None
            if budget_seconds is None
            else max(
                0,
                budget_seconds
                - range_union_duration_seconds(evidence_graph, selected_ids),
            )
        )
        requested_optional = importance == "optional" and self._scope_requests_knowledge(
            scope, knowledge_name
        )
        if importance == "optional" and not requested_optional:
            reason = "not_required_by_scope"
        elif remaining_budget is not None and marginal > remaining_budget:
            reason = "budget_limit"
        else:
            reason = "lower_priority"
        descriptions = {
            "budget_limit": "Verified content exists, but its marginal viewing time exceeds the remaining content budget.",
            "lower_priority": "Verified content exists, but it was not added to the current minimum sufficient content set.",
            "not_required_by_scope": "Verified content exists, but the current learning scope does not require this optional knowledge.",
        }
        policy_refs = {
            "budget_limit": ["content_budget", "marginal_duration_union"],
            "lower_priority": ["minimum_sufficient_selection", "importance_priority"],
            "not_required_by_scope": ["minimum_sufficient_selection", "scope_relevance"],
        }
        return SelectionOmission(
            knowledgeId=knowledge_id,
            importance=importance,
            reason=reason,
            candidateSegmentRefs=full_candidates,
            marginalDurationSeconds=marginal,
            policyRuleRefs=policy_refs[reason],
            description=descriptions[reason],
        )

    def _candidate_key(
        self,
        segment_id: str,
        gained_knowledge: set[str],
        resource_id: str,
        selected_resources: set[str],
        segments,
        resources,
        evidence,
    ) -> tuple:
        segment = segments[segment_id]
        evidence_rank = self._segment_evidence_rank(segment, evidence)
        duration = segment.end_seconds - segment.start_seconds
        source_switch = 0 if not selected_resources or resource_id in selected_resources else 1
        version_compatible = bool(resources[resource_id].technology_versions)
        return (
            -len(gained_knowledge),
            -evidence_rank,
            duration,
            source_switch,
            -int(version_compatible),
            segment_id,
        )

    @staticmethod
    def _add_context_segments(
        selected_ids: list[str],
        segments,
    ) -> None:
        ordered: list[str] = []
        visiting: set[str] = set()

        def add(segment_id: str) -> None:
            if segment_id in ordered:
                return
            if segment_id in visiting:
                raise LearningGenerationError(
                    "content_selection",
                    f"context segment dependency contains a cycle at {segment_id}",
                )
            visiting.add(segment_id)
            segment = segments[segment_id]
            for context_id in segment.context_segment_refs:
                if context_id not in segments:
                    raise LearningGenerationError(
                        "content_selection",
                        f"segment {segment.id} references missing context segment {context_id}",
                    )
                add(context_id)
            visiting.remove(segment_id)
            ordered.append(segment_id)

        for selected_id in selected_ids:
            add(selected_id)
        selected_ids[:] = ordered

    def _selected_segment(
        self,
        segment_id: str,
        viewing_order: int,
        knowledge,
        valid_edges,
        segments,
        resources,
        evidence,
        redundancy_report: RedundancyReport,
    ) -> SelectedSegment:
        segment = segments[segment_id]
        usable_edges = [
            edge
            for edge in valid_edges
            if edge.segment_id == segment_id
            and edge.coverage_strength == "full"
        ]
        if not usable_edges:
            raise LearningGenerationError(
                "content_selection",
                f"context segment {segment_id} has no usable knowledge coverage",
            )
        knowledge_refs = list(dict.fromkeys(edge.knowledge_id for edge in usable_edges))
        coverage_refs = [edge.id for edge in usable_edges]
        evidence_refs = list(
            dict.fromkeys(
                evidence_id for edge in usable_edges for evidence_id in edge.evidence_refs
            )
        )
        rejected = self._rejected_alternatives(
            segment,
            segments,
            resources,
            evidence,
            redundancy_report,
        )
        duration = segment.end_seconds - segment.start_seconds
        saved_seconds = sum(
            max(0, segments[item.segment_id].end_seconds - segments[item.segment_id].start_seconds - duration)
            for item in rejected
        )
        facts = SelectionFacts(
            knowledgeCovered=knowledge_refs,
            evidenceLevel=self._evidence_level(segment, evidence),
            savedMinutes=saved_seconds // 60,
            versionCompatible=(
                segment.resource_fingerprint
                == resources[segment.resource_id].content_fingerprint
                and bool(resources[segment.resource_id].technology_versions)
            ),
            alternativeRejected=rejected,
            selectionRuleRefs=[
                "required_knowledge_coverage",
                "verified_evidence",
                "minimum_duration",
                "minimum_segments",
                "minimum_resource_switches",
                "version_compatibility",
            ],
        )
        return SelectedSegment(
            id=generated_id("selected-segment", segment_id, viewing_order, segment_id),
            segmentId=segment_id,
            knowledgeRefs=knowledge_refs,
            coverageEdgeRefs=coverage_refs,
            evidenceRefs=evidence_refs,
            viewingOrder=viewing_order,
            selectionReason=self._selection_reason(facts),
            selectionFacts=facts,
        )

    def _rejected_alternatives(
        self,
        segment,
        segments,
        resources,
        evidence,
        redundancy_report: RedundancyReport,
    ) -> list[AlternativeRejection]:
        selected_rank = self._segment_evidence_rank(segment, evidence)
        rejected: list[AlternativeRejection] = []
        for decision in redundancy_report.segments:
            if decision.classification != "REDUNDANT" or decision.duplicate_of != segment.id:
                continue
            alternative = segments[decision.segment_id]
            alternative_rank = self._segment_evidence_rank(alternative, evidence)
            reason = (
                "weaker_evidence"
                if alternative_rank < selected_rank
                else "duplicate_content"
            )
            rejected.append(
                AlternativeRejection(
                    segmentId=alternative.id,
                    resourceId=resources[alternative.resource_id].id,
                    reason=reason,
                )
            )
        return rejected

    def _segment_evidence_rank(self, segment, evidence) -> int:
        return max(
            (
                self._EVIDENCE_RANK[evidence[item_id].kind]
                for item_id in segment.evidence_refs
                if item_id in evidence
                and evidence[item_id].verification_status == "verified"
            ),
            default=-1,
        )

    def _evidence_level(self, segment, evidence) -> str:
        kinds = {
            evidence[item_id].kind
            for item_id in segment.evidence_refs
            if item_id in evidence
            and evidence[item_id].verification_status == "verified"
        }
        for kind, level in (
            ("transcript_span", "transcript"),
            ("caption_span", "caption"),
            ("manual_verified", "manual"),
            ("chapter_marker", "chapter"),
        ):
            if kind in kinds:
                return level
        return "metadata"

    @staticmethod
    def _selection_reason(facts: SelectionFacts) -> str:
        rejected_count = len(facts.alternative_rejected)
        return (
            f"Selected by verified {facts.evidence_level} evidence for "
            f"{len(facts.knowledge_covered)} knowledge item(s); "
            f"rejected {rejected_count} redundant alternative(s)."
        )

    @staticmethod
    def _coverage_gap(knowledge_id, importance, coverage, resource_refs) -> CoverageGap:
        if coverage.covered:
            reason = "available evidence is insufficient for full coverage"
        else:
            reason = "no verified content segment covers this knowledge"
        return CoverageGap(
            knowledgeId=knowledge_id,
            reason=reason,
            impact={"required": "blocker", "important": "major", "optional": "minor"}[
                importance
            ],
            searchedResourceRefs=resource_refs,
        )


__all__ = ["ContentSelectionResult", "ContentSelector"]
