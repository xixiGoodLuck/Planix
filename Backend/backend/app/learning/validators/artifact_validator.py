from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningArtifact,
    LearningArtifactRef,
    LearningContentPlan,
    LearningOutcome,
    LearningQualityReport,
    LearningScope,
)
from ..scope_anchor_semantics import text_matches_concept_anchor
from ..selection_semantics import range_union_duration_seconds


class LearningArtifactValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


@dataclass(frozen=True)
class ValidatedLearningArtifacts:
    scope: LearningScope
    capability_graph: CapabilityGraph
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph
    content_selection: ContentSelection
    content_plan: LearningContentPlan
    quality_report: LearningQualityReport


class LearningArtifactValidator:
    """Code-owned referential, provenance, duration, and version validation."""

    _CONTENT_EVIDENCE_KINDS = {
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "manual_verified",
    }

    def validate_chain(
        self,
        *,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        content_selection: ContentSelection,
        content_plan: LearningContentPlan,
        quality_report: LearningQualityReport,
    ) -> ValidatedLearningArtifacts:
        self.validate_capability_graph(scope, capability_graph)
        self.validate_knowledge_graph(scope, capability_graph, knowledge_graph)
        self.validate_evidence_graph(knowledge_graph, evidence_graph)
        normalized_selection = self.validate_content_selection(
            scope,
            knowledge_graph,
            evidence_graph,
            content_selection,
        )
        normalized_plan = self.validate_content_plan(
            scope,
            knowledge_graph,
            evidence_graph,
            normalized_selection,
            content_plan,
        )
        self.validate_quality_report(
            scope,
            capability_graph,
            knowledge_graph,
            evidence_graph,
            normalized_selection,
            normalized_plan,
            quality_report,
        )
        return ValidatedLearningArtifacts(
            scope=scope,
            capability_graph=capability_graph,
            knowledge_graph=knowledge_graph,
            evidence_graph=evidence_graph,
            content_selection=normalized_selection,
            content_plan=normalized_plan,
            quality_report=quality_report,
        )

    def validate_capability_graph(self, scope: LearningScope, graph: CapabilityGraph) -> None:
        self._assert_ref(graph.scope_ref, scope, "learning_scope", "capabilityGraph.scopeRef")
        self.validate_outcomes(scope, graph.outcomes)
        self._assert_unique((item.id for item in graph.capabilities), "capability_id", "capabilityGraph.capabilities")

        outcome_ids = {item.id for item in graph.outcomes}
        capability_ids = {item.id for item in graph.capabilities}
        scope_anchor_ids = self._required_scope_anchor_ids(scope)
        for capability in graph.capabilities:
            missing = set(capability.outcome_refs) - outcome_ids
            if missing:
                self._fail(
                    "outcome_capability_lineage",
                    f"capabilityGraph.capabilities.{capability.id}.outcomeRefs",
                    f"unknown outcome refs: {sorted(missing)}",
                )
            if (
                scope_anchor_ids
                and capability.importance == "required"
                and not set(capability.scope_anchor_refs) & scope_anchor_ids
            ):
                self._fail(
                    "required_scope_anchor",
                    f"capabilityGraph.capabilities.{capability.id}.scopeAnchorRefs",
                    "required capability must reference a code-owned explicit scope anchor",
                )
            self._assert_required_concept_text(
                scope,
                capability.importance,
                capability.scope_anchor_refs,
                capability.name,
                f"capabilityGraph.capabilities.{capability.id}.scopeAnchorRefs",
            )
        self._assert_unique_required_concept_anchors(
            scope,
            (
                (
                    capability.id,
                    capability.importance,
                    capability.scope_anchor_refs,
                    f"capabilityGraph.capabilities.{capability.id}.scopeAnchorRefs",
                )
                for capability in graph.capabilities
            ),
        )

        for outcome in graph.outcomes:
            if outcome.importance != "required":
                continue
            if not any(
                capability.importance == "required" and outcome.id in capability.outcome_refs
                for capability in graph.capabilities
            ):
                self._fail(
                    "required_outcome_coverage",
                    f"capabilityGraph.outcomes.{outcome.id}",
                    "required outcome has no required capability",
                )

        prerequisite_edges: list[tuple[str, str]] = []
        for index, edge in enumerate(graph.edges):
            if edge.source_capability_id not in capability_ids or edge.target_capability_id not in capability_ids:
                self._fail(
                    "capability_edge_reference",
                    f"capabilityGraph.edges.{index}",
                    "capability edge references a missing node",
                )
            if edge.source_capability_id == edge.target_capability_id:
                self._fail(
                    "capability_cycle",
                    f"capabilityGraph.edges.{index}",
                    "capability cannot depend on itself",
                )
            if edge.relation == "prerequisite":
                prerequisite_edges.append((edge.source_capability_id, edge.target_capability_id))
        self._assert_acyclic(capability_ids, prerequisite_edges, "capability_cycle", "capabilityGraph.edges")

    def validate_outcomes(
        self,
        scope: LearningScope,
        outcomes: list[LearningOutcome],
    ) -> None:
        if not outcomes:
            self._fail(
                "outcome_required",
                "learningOutcomes",
                "at least one learning outcome is required",
            )
        self._assert_unique((item.id for item in outcomes), "outcome_id", "learningOutcomes")
        allowed_scope_refs = {scope.artifact_id, *scope.source_refs}
        scope_anchor_ids = self._required_scope_anchor_ids(scope)
        for outcome in outcomes:
            if not set(outcome.source_goal_refs) & allowed_scope_refs:
                self._fail(
                    "scope_outcome_lineage",
                    f"learningOutcomes.{outcome.id}.sourceGoalRefs",
                    "outcome does not reference the current LearningScope",
                )
            if (
                scope_anchor_ids
                and outcome.importance == "required"
                and not set(outcome.scope_anchor_refs) & scope_anchor_ids
            ):
                self._fail(
                    "required_scope_anchor",
                    f"learningOutcomes.{outcome.id}.scopeAnchorRefs",
                    "required outcome must reference a code-owned explicit scope anchor",
                )
            self._assert_required_concept_text(
                scope,
                outcome.importance,
                outcome.scope_anchor_refs,
                outcome.statement,
                f"learningOutcomes.{outcome.id}.scopeAnchorRefs",
            )
        self._assert_unique_required_concept_anchors(
            scope,
            (
                (
                    outcome.id,
                    outcome.importance,
                    outcome.scope_anchor_refs,
                    f"learningOutcomes.{outcome.id}.scopeAnchorRefs",
                )
                for outcome in outcomes
            ),
        )

    def validate_knowledge_graph(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        graph: KnowledgeGraph,
    ) -> None:
        self._assert_ref(graph.scope_ref, scope, "learning_scope", "knowledgeGraph.scopeRef")
        self._assert_ref(
            graph.capability_graph_ref,
            capability_graph,
            "capability_graph",
            "knowledgeGraph.capabilityGraphRef",
        )
        self._assert_unique((item.id for item in graph.nodes), "knowledge_id", "knowledgeGraph.nodes")

        capability_ids = {item.id for item in capability_graph.capabilities}
        outcome_ids = {item.id for item in capability_graph.outcomes}
        knowledge_ids = {item.id for item in graph.nodes}
        knowledge_by_id = {item.id: item for item in graph.nodes}
        scope_anchor_ids = self._required_scope_anchor_ids(scope)
        normalized_names: dict[str, str] = {}
        for node in graph.nodes:
            normalized_name = re.sub(
                r"[^\w]+",
                "",
                node.name.casefold(),
                flags=re.UNICODE,
            )
            previous_id = normalized_names.get(normalized_name)
            if normalized_name and previous_id is not None:
                self._fail(
                    "duplicate_knowledge_name",
                    f"knowledgeGraph.nodes.{node.id}.name",
                    f"normalized name duplicates knowledge node {previous_id}",
                )
            normalized_names[normalized_name] = node.id
            missing_capabilities = set(node.capability_refs) - capability_ids
            missing_outcomes = set(node.outcome_refs) - outcome_ids
            if missing_capabilities:
                self._fail(
                    "knowledge_capability_lineage",
                    f"knowledgeGraph.nodes.{node.id}.capabilityRefs",
                    f"unknown capability refs: {sorted(missing_capabilities)}",
                )
            if missing_outcomes:
                self._fail(
                    "knowledge_outcome_lineage",
                    f"knowledgeGraph.nodes.{node.id}.outcomeRefs",
                    f"unknown outcome refs: {sorted(missing_outcomes)}",
                )
            if node.importance == "required" and (
                not node.capability_refs or not node.outcome_refs
            ):
                self._fail(
                    "required_knowledge_source",
                    f"knowledgeGraph.nodes.{node.id}",
                    "required knowledge has no capability/outcome source",
                )
            if (
                scope_anchor_ids
                and node.importance == "required"
                and not set(node.scope_anchor_refs) & scope_anchor_ids
            ):
                self._fail(
                    "required_scope_anchor",
                    f"knowledgeGraph.nodes.{node.id}.scopeAnchorRefs",
                    "required knowledge must reference a code-owned explicit scope anchor",
                )
            self._assert_required_concept_text(
                scope,
                node.importance,
                node.scope_anchor_refs,
                node.name,
                f"knowledgeGraph.nodes.{node.id}.scopeAnchorRefs",
            )
            if node.coverage_requirements:
                self._assert_unique(
                    (item.id for item in node.coverage_requirements),
                    "coverage_requirement_id",
                    f"knowledgeGraph.nodes.{node.id}.coverageRequirements",
                )
        self._assert_unique_required_concept_anchors(
            scope,
            (
                (
                    node.id,
                    node.importance,
                    node.scope_anchor_refs,
                    f"knowledgeGraph.nodes.{node.id}.scopeAnchorRefs",
                )
                for node in graph.nodes
            ),
        )

        required_capabilities = {
            item.id
            for item in capability_graph.capabilities
            if item.importance == "required"
        }
        required_knowledge_coverage = {
            capability_id
            for node in graph.nodes
            if node.importance == "required"
            for capability_id in node.capability_refs
        }
        missing_required_capabilities = sorted(
            required_capabilities - required_knowledge_coverage
        )
        if missing_required_capabilities:
            target = missing_required_capabilities[0]
            self._fail(
                "required_capability_coverage",
                f"capabilityGraph.capabilities.{target}",
                f"required capability {target} has no required knowledge",
            )

        prerequisite_edges: list[tuple[str, str]] = []
        containment_edges: list[tuple[str, str]] = []
        for index, edge in enumerate(graph.edges):
            if edge.source_knowledge_id not in knowledge_ids or edge.target_knowledge_id not in knowledge_ids:
                self._fail(
                    "knowledge_edge_reference",
                    f"knowledgeGraph.edges.{index}",
                    "knowledge edge references a missing node",
                )
            if edge.source_knowledge_id == edge.target_knowledge_id:
                rule = (
                    "knowledge_cycle_prerequisite"
                    if edge.relation == "prerequisite"
                    else "knowledge_cycle_containment"
                    if edge.relation == "part_of"
                    else "invalid_relation_semantics"
                )
                self._fail(
                    rule,
                    f"knowledgeGraph.edges.{index}",
                    f"{edge.relation} cannot reference the same knowledge node",
                )
            source = knowledge_by_id[edge.source_knowledge_id]
            target = knowledge_by_id[edge.target_knowledge_id]
            if (
                edge.relation == "prerequisite"
                and source.importance == "optional"
                and target.importance == "required"
            ):
                self._fail(
                    "invalid_relation_semantics",
                    f"knowledgeGraph.edges.{index}",
                    "required knowledge cannot depend on an optional prerequisite",
                )
            if edge.relation == "optional_extension" and target.importance != "optional":
                self._fail(
                    "invalid_relation_semantics",
                    f"knowledgeGraph.edges.{index}",
                    "optional_extension must target optional knowledge",
                )
            if edge.relation == "prerequisite":
                prerequisite_edges.append(
                    (edge.source_knowledge_id, edge.target_knowledge_id)
                )
            elif edge.relation == "part_of":
                containment_edges.append(
                    (edge.source_knowledge_id, edge.target_knowledge_id)
                )
        self._assert_acyclic(
            knowledge_ids,
            prerequisite_edges,
            "knowledge_cycle_prerequisite",
            "knowledgeGraph.edges.prerequisite",
        )
        self._assert_acyclic(
            knowledge_ids,
            containment_edges,
            "knowledge_cycle_containment",
            "knowledgeGraph.edges.part_of",
        )

    def validate_evidence_graph(self, knowledge_graph: KnowledgeGraph, graph: EvidenceGraph) -> None:
        self._assert_ref(
            graph.knowledge_graph_ref,
            knowledge_graph,
            "knowledge_graph",
            "evidenceGraph.knowledgeGraphRef",
        )
        self._assert_unique((item.id for item in graph.resources), "resource_id", "evidenceGraph.resources")
        self._assert_unique((item.id for item in graph.segments), "segment_id", "evidenceGraph.segments")
        self._assert_unique((item.id for item in graph.evidence), "evidence_id", "evidenceGraph.evidence")
        self._assert_unique((item.id for item in graph.coverage_edges), "coverage_edge_id", "evidenceGraph.coverageEdges")

        resources = {item.id: item for item in graph.resources}
        segments = {item.id: item for item in graph.segments}
        evidence = {item.id: item for item in graph.evidence}
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        knowledge_ids = set(knowledge)

        for segment in graph.segments:
            resource = resources.get(segment.resource_id)
            if resource is None:
                self._fail(
                    "segment_resource_reference",
                    f"evidenceGraph.segments.{segment.id}.resourceId",
                    "segment references a missing video resource",
                )
            if segment.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "version_compatibility",
                    f"evidenceGraph.segments.{segment.id}.resourceFingerprint",
                    "segment resource fingerprint is stale",
                )
            if segment.start_seconds < 0 or segment.end_seconds <= segment.start_seconds:
                self._fail(
                    "unsupported_timestamp",
                    f"evidenceGraph.segments.{segment.id}",
                    "segment timestamp range is invalid",
                )
            if segment.end_seconds > resource.duration_seconds:
                self._fail(
                    "unsupported_timestamp",
                    f"evidenceGraph.segments.{segment.id}.endSeconds",
                    "segment extends beyond the video duration",
                )
            missing_evidence = set(segment.evidence_refs) - set(evidence)
            if missing_evidence:
                self._fail(
                    "segment_evidence_reference",
                    f"evidenceGraph.segments.{segment.id}.evidenceRefs",
                    f"unknown evidence refs: {sorted(missing_evidence)}",
                )

            verified = [
                evidence[item_id]
                for item_id in segment.evidence_refs
                if evidence[item_id].segment_id == segment.id
                and evidence[item_id].verification_status == "verified"
                and evidence[item_id].kind in self._CONTENT_EVIDENCE_KINDS
            ]
            if not verified:
                self._fail(
                    "evidence_validity",
                    f"evidenceGraph.segments.{segment.id}",
                    "segment has no verified content evidence",
                )

        for item in graph.evidence:
            resource = resources.get(item.resource_id)
            segment = segments.get(item.segment_id)
            if resource is None or segment is None:
                self._fail(
                    "evidence_reference",
                    f"evidenceGraph.evidence.{item.id}",
                    "evidence references a missing resource or segment",
                )
            if segment.resource_id != item.resource_id:
                self._fail(
                    "evidence_reference",
                    f"evidenceGraph.evidence.{item.id}.resourceId",
                    "evidence and segment belong to different resources",
                )
            if item.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "version_compatibility",
                    f"evidenceGraph.evidence.{item.id}.resourceFingerprint",
                    "evidence resource fingerprint is stale",
                )
            if item.source_range.end_offset <= item.source_range.start_offset:
                self._fail(
                    "evidence_validity",
                    f"evidenceGraph.evidence.{item.id}.sourceRange",
                    "evidence source range is invalid",
                )

        for edge in graph.coverage_edges:
            segment = segments.get(edge.segment_id)
            if edge.knowledge_id not in knowledge_ids:
                self._fail(
                    "coverage_knowledge_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.knowledgeId",
                    "coverage edge references missing knowledge",
                )
            requirement_ids = {
                item.id for item in knowledge[edge.knowledge_id].coverage_requirements
            }
            supported = set(edge.supported_requirement_refs)
            if len(supported) != len(edge.supported_requirement_refs) or not supported <= requirement_ids:
                self._fail(
                    "coverage_requirement_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.supportedRequirementRefs",
                    "coverage must reference unique requirements owned by its knowledge node",
                )
            if requirement_ids:
                expected_strength = (
                    "full"
                    if supported == requirement_ids
                    else "partial" if supported else "supplementary"
                )
                if edge.coverage_strength != expected_strength:
                    self._fail(
                        "coverage_requirement_strength",
                        f"evidenceGraph.coverageEdges.{edge.id}.coverageStrength",
                        "coverage strength must be computed from supported requirements",
                    )
            if segment is None:
                self._fail(
                    "coverage_segment_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.segmentId",
                    "coverage edge references missing segment",
                )
            missing = set(edge.evidence_refs) - set(evidence)
            if missing:
                self._fail(
                    "coverage_evidence_reference",
                    f"evidenceGraph.coverageEdges.{edge.id}.evidenceRefs",
                    f"coverage edge references missing evidence: {sorted(missing)}",
                )
            invalid = [
                item_id
                for item_id in edge.evidence_refs
                if evidence[item_id].segment_id != edge.segment_id
                or evidence[item_id].verification_status != "verified"
                or evidence[item_id].kind not in self._CONTENT_EVIDENCE_KINDS
            ]
            if invalid:
                self._fail(
                    "evidence_validity",
                    f"evidenceGraph.coverageEdges.{edge.id}.evidenceRefs",
                    f"coverage edge uses invalid evidence: {sorted(invalid)}",
                )

    def validate_content_selection(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
    ) -> ContentSelection:
        self._assert_ref(selection.scope_ref, scope, "learning_scope", "contentSelection.scopeRef")
        self._assert_ref(
            selection.knowledge_graph_ref,
            knowledge_graph,
            "knowledge_graph",
            "contentSelection.knowledgeGraphRef",
        )
        self._assert_ref(
            selection.evidence_graph_ref,
            evidence_graph,
            "evidence_graph",
            "contentSelection.evidenceGraphRef",
        )
        self._assert_unique((item.id for item in selection.selected_segments), "selection_id", "contentSelection.selectedSegments")
        self._assert_unique((item.segment_id for item in selection.selected_segments), "selected_segment", "contentSelection.selectedSegments")

        segments = {item.id: item for item in evidence_graph.segments}
        evidence = {item.id: item for item in evidence_graph.evidence}
        coverage = {item.id: item for item in evidence_graph.coverage_edges}
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        knowledge_ids = set(knowledge)
        selected_knowledge: set[str] = set()

        for item in selection.selected_segments:
            segment = segments.get(item.segment_id)
            if segment is None:
                self._fail(
                    "selection_segment_reference",
                    f"contentSelection.selectedSegments.{item.id}.segmentId",
                    "selection references a segment outside EvidenceGraph",
                )
            missing_knowledge = set(item.knowledge_refs) - knowledge_ids
            if missing_knowledge:
                self._fail(
                    "selection_knowledge_reference",
                    f"contentSelection.selectedSegments.{item.id}.knowledgeRefs",
                    f"selection references missing knowledge: {sorted(missing_knowledge)}",
                )
            missing_edges = set(item.coverage_edge_refs) - set(coverage)
            if missing_edges:
                self._fail(
                    "selection_coverage_reference",
                    f"contentSelection.selectedSegments.{item.id}.coverageEdgeRefs",
                    f"selection references missing coverage edges: {sorted(missing_edges)}",
                )
            missing_evidence = set(item.evidence_refs) - set(evidence)
            if missing_evidence:
                self._fail(
                    "selection_evidence_reference",
                    f"contentSelection.selectedSegments.{item.id}.evidenceRefs",
                    f"selection references missing evidence: {sorted(missing_evidence)}",
                )
            invalid_edges = [
                edge_id
                for edge_id in item.coverage_edge_refs
                if coverage[edge_id].segment_id != item.segment_id
                or coverage[edge_id].knowledge_id not in item.knowledge_refs
            ]
            if invalid_edges:
                self._fail(
                    "selection_coverage_reference",
                    f"contentSelection.selectedSegments.{item.id}.coverageEdgeRefs",
                    f"coverage edges do not support the selected segment: {sorted(invalid_edges)}",
                )
            covered_knowledge = {
                coverage[edge_id].knowledge_id for edge_id in item.coverage_edge_refs
            }
            unsupported_knowledge = set(item.knowledge_refs) - covered_knowledge
            if unsupported_knowledge:
                self._fail(
                    "selection_coverage_reference",
                    f"contentSelection.selectedSegments.{item.id}.knowledgeRefs",
                    f"knowledge has no referenced coverage edge: {sorted(unsupported_knowledge)}",
                )
            coverage_evidence = {
                evidence_id
                for edge_id in item.coverage_edge_refs
                for evidence_id in coverage[edge_id].evidence_refs
            }
            omitted_evidence = coverage_evidence - set(item.evidence_refs)
            if omitted_evidence:
                self._fail(
                    "selection_evidence_reference",
                    f"contentSelection.selectedSegments.{item.id}.evidenceRefs",
                    f"selection omits coverage evidence: {sorted(omitted_evidence)}",
                )
            invalid_evidence = [
                evidence_id
                for evidence_id in item.evidence_refs
                if evidence[evidence_id].segment_id != item.segment_id
                or evidence[evidence_id].verification_status != "verified"
            ]
            if invalid_evidence:
                self._fail(
                    "evidence_validity",
                    f"contentSelection.selectedSegments.{item.id}.evidenceRefs",
                    f"selection uses invalid evidence: {sorted(invalid_evidence)}",
                )
            selected_knowledge.update(item.knowledge_refs)

        self._assert_unique(
            (item.knowledge_id for item in selection.coverage_gaps),
            "coverage_gap_knowledge",
            "contentSelection.coverageGaps",
        )
        gap_knowledge = {item.knowledge_id for item in selection.coverage_gaps}
        missing_gap_refs = gap_knowledge - knowledge_ids
        if missing_gap_refs:
            self._fail(
                "knowledge_coverage",
                "contentSelection.coverageGaps",
                f"coverage gaps reference missing knowledge: {sorted(missing_gap_refs)}",
            )
        self._assert_unique(
            (item.knowledge_id for item in selection.selection_omissions),
            "selection_omission_knowledge",
            "contentSelection.selectionOmissions",
        )
        omission_knowledge = {
            item.knowledge_id for item in selection.selection_omissions
        }
        missing_omission_refs = omission_knowledge - knowledge_ids
        if missing_omission_refs:
            self._fail(
                "selection_omission_truth",
                "contentSelection.selectionOmissions",
                f"selection omissions reference missing knowledge: {sorted(missing_omission_refs)}",
            )
        segment_ids = set(segments)
        for omission in selection.selection_omissions:
            node = knowledge.get(omission.knowledge_id)
            if node is None:
                continue
            if node.importance != omission.importance or node.importance == "required":
                self._fail(
                    "selection_omission_truth",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}",
                    "selection omission importance must match a non-required knowledge node",
                )
            missing_candidates = set(omission.candidate_segment_refs) - segment_ids
            if missing_candidates:
                self._fail(
                    "selection_omission_truth",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.candidateSegmentRefs",
                    f"selection omission references missing candidate segments: {sorted(missing_candidates)}",
                )
        conflicting = (
            (selected_knowledge & gap_knowledge)
            | (selected_knowledge & omission_knowledge)
            | (gap_knowledge & omission_knowledge)
        )
        if conflicting:
            self._fail(
                "selection_partition",
                "contentSelection",
                f"knowledge cannot be selected, a coverage gap, and an omission at once: {sorted(conflicting)}",
            )
        unaccounted = (
            knowledge_ids - selected_knowledge - gap_knowledge - omission_knowledge
        )
        if unaccounted:
            self._fail(
                "knowledge_coverage",
                "contentSelection",
                f"knowledge is neither selected, a coverage gap, nor a selection omission: {sorted(unaccounted)}",
            )
        total_duration = range_union_duration_seconds(
            evidence_graph,
            [item.segment_id for item in selection.selected_segments],
        )
        if selection.total_duration_seconds not in {0, total_duration}:
            self._fail(
                "selection_duration",
                "contentSelection.totalDurationSeconds",
                f"duration must be derived from segments and equal {total_duration}",
            )
        return selection.model_copy(update={"total_duration_seconds": total_duration})

    def validate_content_plan(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
        plan: LearningContentPlan,
    ) -> LearningContentPlan:
        self._assert_ref(plan.scope_ref, scope, "learning_scope", "learningContentPlan.scopeRef")
        self._assert_ref(
            plan.knowledge_graph_ref,
            knowledge_graph,
            "knowledge_graph",
            "learningContentPlan.knowledgeGraphRef",
        )
        self._assert_ref(
            plan.evidence_graph_ref,
            evidence_graph,
            "evidence_graph",
            "learningContentPlan.evidenceGraphRef",
        )
        self._assert_ref(
            plan.content_selection_ref,
            selection,
            "content_selection",
            "learningContentPlan.contentSelectionRef",
        )
        self._assert_unique((item.knowledge_id for item in plan.items), "plan_knowledge_id", "learningContentPlan.items")

        knowledge = {item.id: item for item in knowledge_graph.nodes}
        resources = {item.id: item for item in evidence_graph.resources}
        segments = {item.id: item for item in evidence_graph.segments}
        selected = {item.id: item for item in selection.selected_segments}
        gap_knowledge = {item.knowledge_id for item in selection.coverage_gaps}
        omission_knowledge = {
            item.knowledge_id for item in selection.selection_omissions
        }

        if plan.evidence_gaps != selection.coverage_gaps:
            self._fail(
                "knowledge_coverage",
                "learningContentPlan.evidenceGaps",
                "plan evidence gaps must exactly project ContentSelection coverage gaps",
            )
        if plan.deferred_knowledge != selection.selection_omissions:
            self._fail(
                "selection_omission_truth",
                "learningContentPlan.deferredKnowledge",
                "plan deferred knowledge must exactly project ContentSelection omissions",
            )

        plan_knowledge = {item.knowledge_id for item in plan.items}
        if plan_knowledge != set(knowledge):
            self._fail(
                "knowledge_coverage",
                "learningContentPlan.items",
                "plan must contain exactly the current KnowledgeGraph nodes",
            )

        for item in plan.items:
            node = knowledge[item.knowledge_id]
            if (
                item.knowledge_name != node.name
                or item.knowledge_explanation != node.explanation
                or item.why_required != node.why_required
            ):
                self._fail(
                    "version_compatibility",
                    f"learningContentPlan.items.{item.knowledge_id}",
                    "plan knowledge text is not bound to the current KnowledgeGraph version",
                )
            if not item.recommended_content:
                if item.knowledge_id in gap_knowledge:
                    if not (item.uncovered_reason or "").strip():
                        self._fail(
                            "knowledge_coverage",
                            f"learningContentPlan.items.{item.knowledge_id}",
                            "knowledge without evidence must disclose a coverage gap",
                        )
                elif item.knowledge_id in omission_knowledge:
                    if item.uncovered_reason is not None:
                        self._fail(
                            "selection_omission_truth",
                            f"learningContentPlan.items.{item.knowledge_id}",
                            "selection omission must not be presented as missing evidence",
                        )
                else:
                    self._fail(
                        "knowledge_coverage",
                        f"learningContentPlan.items.{item.knowledge_id}",
                        "knowledge without selected content must disclose a gap or omission",
                    )
                continue
            if item.knowledge_id in gap_knowledge or item.knowledge_id in omission_knowledge:
                self._fail(
                    "selection_partition",
                    f"learningContentPlan.items.{item.knowledge_id}",
                    "selected plan content cannot also be a gap or omission",
                )
            for recommendation in item.recommended_content:
                selected_item = selected.get(recommendation.selection_id)
                segment = segments.get(recommendation.segment_id)
                if selected_item is None or segment is None:
                    self._fail(
                        "plan_selection_reference",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent",
                        "plan references content outside the current ContentSelection",
                    )
                if (
                    selected_item.segment_id != recommendation.segment_id
                    or item.knowledge_id not in selected_item.knowledge_refs
                ):
                    self._fail(
                        "plan_selection_reference",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent",
                        "selected content does not cover this knowledge item",
                    )
                resource = resources.get(segment.resource_id)
                expected_duration = segment.end_seconds - segment.start_seconds
                if resource is None or recommendation.resource_id != resource.id:
                    self._fail(
                        "plan_selection_reference",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent",
                        "recommended content references the wrong video resource",
                    )
                if recommendation.video_title != resource.title:
                    self._fail(
                        "version_compatibility",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent.videoTitle",
                        "video title is not bound to the current resource version",
                    )
                if recommendation.segment_summary != segment.content_summary:
                    self._fail(
                        "version_compatibility",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent.segmentSummary",
                        "segment summary is not bound to the current EvidenceGraph version",
                    )
                if recommendation.duration_seconds != expected_duration:
                    self._fail(
                        "selection_duration",
                        f"learningContentPlan.items.{item.knowledge_id}.recommendedContent.durationSeconds",
                        "recommended duration must be derived from the selected segment",
                    )

        if plan.total_duration_seconds not in {0, selection.total_duration_seconds}:
            self._fail(
                "selection_duration",
                "learningContentPlan.totalDurationSeconds",
                "plan duration must equal the current ContentSelection duration",
            )
        return plan.model_copy(update={"total_duration_seconds": selection.total_duration_seconds})

    def validate_quality_report(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
        plan: LearningContentPlan,
        report: LearningQualityReport,
    ) -> None:
        self._assert_ref(report.target_ref, plan, "learning_content_plan", "learningQualityReport.targetRef")
        self._assert_ref(report.scope_ref, scope, "learning_scope", "learningQualityReport.scopeRef")
        self._assert_ref(
            report.capability_graph_ref,
            capability_graph,
            "capability_graph",
            "learningQualityReport.capabilityGraphRef",
        )
        self._assert_ref(
            report.knowledge_graph_ref,
            knowledge_graph,
            "knowledge_graph",
            "learningQualityReport.knowledgeGraphRef",
        )
        self._assert_ref(
            report.evidence_graph_ref,
            evidence_graph,
            "evidence_graph",
            "learningQualityReport.evidenceGraphRef",
        )
        self._assert_ref(
            report.content_selection_ref,
            selection,
            "content_selection",
            "learningQualityReport.contentSelectionRef",
        )
        if report.remaining_gaps != selection.coverage_gaps:
            self._fail(
                "knowledge_coverage",
                "learningQualityReport.remainingGaps",
                "quality remaining gaps must exactly project ContentSelection coverage gaps",
            )

    def _scope_anchor_ids(self, scope: LearningScope) -> set[str]:
        self._assert_unique(
            (item.id for item in scope.explicit_scope_anchors),
            "scope_anchor_id",
            "learningScope.explicitScopeAnchors",
        )
        source_refs = set(scope.source_refs)
        for anchor in scope.explicit_scope_anchors:
            if anchor.source_ref not in source_refs:
                self._fail(
                    "scope_anchor_source",
                    f"learningScope.explicitScopeAnchors.{anchor.id}.sourceRef",
                    "scope anchor must reference explicit user lineage in the LearningScope",
                )
        return {item.id for item in scope.explicit_scope_anchors}

    def _required_scope_anchor_ids(self, scope: LearningScope) -> set[str]:
        all_ids = self._scope_anchor_ids(scope)
        concept_ids = {
            item.id for item in scope.explicit_scope_anchors if item.kind == "concept"
        }
        return concept_ids or all_ids

    def _assert_required_concept_text(
        self,
        scope: LearningScope,
        importance: str,
        anchor_refs: list[str],
        semantic_text: str,
        field_path: str,
    ) -> None:
        concept_by_id = {
            item.id: item.text
            for item in scope.explicit_scope_anchors
            if item.kind == "concept"
        }
        if importance != "required" or not concept_by_id:
            return
        if not any(
            reference in concept_by_id
            and text_matches_concept_anchor(semantic_text, concept_by_id[reference])
            for reference in anchor_refs
        ):
            self._fail(
                "required_scope_anchor_semantics",
                field_path,
                "required content must be directly about a cited explicit concept anchor",
            )

    def _assert_unique_required_concept_anchors(
        self,
        scope: LearningScope,
        items,
    ) -> None:
        concept_ids = {
            item.id for item in scope.explicit_scope_anchors if item.kind == "concept"
        }
        if not concept_ids:
            return
        owner_by_anchor: dict[str, str] = {}
        for item_id, importance, anchor_refs, field_path in items:
            if importance != "required":
                continue
            for anchor_ref in set(anchor_refs) & concept_ids:
                previous_owner = owner_by_anchor.get(anchor_ref)
                if previous_owner is not None and previous_owner != item_id:
                    self._fail(
                        "required_scope_anchor_cardinality",
                        field_path,
                        "one explicit concept anchor may support at most one required item",
                    )
                owner_by_anchor[anchor_ref] = item_id

    @staticmethod
    def _assert_ref(
        ref: LearningArtifactRef,
        artifact: LearningArtifact,
        expected_type: str,
        path: str,
    ) -> None:
        if (
            ref.artifact_type != expected_type
            or ref.artifact_id != artifact.artifact_id
            or ref.version != artifact.version
        ):
            raise LearningArtifactValidationError(
                "version_compatibility",
                path,
                f"expected {expected_type}:{artifact.artifact_id}@{artifact.version}",
            )

    @staticmethod
    def _assert_unique(values: Iterable[str], label: str, path: str) -> None:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        if duplicates:
            raise LearningArtifactValidationError(
                f"duplicate_{label}",
                path,
                f"duplicate ids: {sorted(duplicates)}",
            )

    @staticmethod
    def _assert_acyclic(
        nodes: set[str],
        edges: list[tuple[str, str]],
        rule: str,
        path: str,
    ) -> None:
        outgoing = {node: [] for node in nodes}
        indegree = {node: 0 for node in nodes}
        for source, target in edges:
            outgoing[source].append(target)
            indegree[target] += 1
        ready = [node for node, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            node = ready.pop()
            visited += 1
            for target in outgoing[node]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(nodes):
            cycle = LearningArtifactValidator._find_cycle(nodes, edges)
            cycle_path = " -> ".join(cycle) if cycle else "unknown"
            raise LearningArtifactValidationError(
                rule,
                path,
                f"directed dependency graph contains cycle path: {cycle_path}",
            )

    @staticmethod
    def _find_cycle(
        nodes: set[str],
        edges: list[tuple[str, str]],
    ) -> list[str]:
        outgoing = {node: [] for node in nodes}
        for source, target in edges:
            outgoing[source].append(target)
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(node: str) -> list[str]:
            state[node] = 1
            stack.append(node)
            for target in sorted(outgoing[node]):
                if state.get(target, 0) == 0:
                    cycle = visit(target)
                    if cycle:
                        return cycle
                elif state.get(target) == 1:
                    start = stack.index(target)
                    return [*stack[start:], target]
            stack.pop()
            state[node] = 2
            return []

        for node in sorted(nodes):
            if state.get(node, 0) == 0:
                cycle = visit(node)
                if cycle:
                    return cycle
        return []

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise LearningArtifactValidationError(rule, path, message)


__all__ = [
    "LearningArtifactValidationError",
    "LearningArtifactValidator",
    "ValidatedLearningArtifacts",
]
