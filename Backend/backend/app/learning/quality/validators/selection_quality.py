from __future__ import annotations

from ...contracts import ContentSelection, EvidenceGraph, KnowledgeGraph, LearningScope
from ...selection.services.redundancy_analyzer import RedundancyAnalyzer
from ...selection.validators import ContentSelectionValidator
from ...selection_semantics import (
    range_union_duration_seconds,
    resolve_selected_knowledge_coverage,
)
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from .base import QualityEvaluation


class SelectionQualityValidator:
    def __init__(
        self,
        artifact_validator: LearningArtifactValidator | None = None,
        redundancy_analyzer: RedundancyAnalyzer | None = None,
        selection_validator: ContentSelectionValidator | None = None,
    ):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()
        self.redundancy_analyzer = redundancy_analyzer or RedundancyAnalyzer()
        self.selection_validator = selection_validator or ContentSelectionValidator(
            artifact_validator=self.artifact_validator,
            redundancy_analyzer=self.redundancy_analyzer,
        )

    def evaluate(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
    ) -> QualityEvaluation:
        result = QualityEvaluation()
        owner_id = selection.artifact_id
        segments = {item.id: item for item in evidence_graph.segments}
        edges = {item.id: item for item in evidence_graph.coverage_edges}
        selected_ids = [item.segment_id for item in selection.selected_segments]
        selected_id_set = set(selected_ids)

        structural_error: LearningArtifactValidationError | None = None
        try:
            self.selection_validator.validate_selection(
                scope,
                knowledge_graph,
                evidence_graph,
                selection,
            )
        except LearningArtifactValidationError as exc:
            structural_error = exc
        result.add(
            rule="evidence_validity",
            passed=structural_error is None,
            evidence=[] if structural_error is None else [structural_error.path],
            owner_id=owner_id,
            severity="blocker",
            target_type="content_selection",
            target_id=owner_id,
            description=(
                "selection references must resolve to the current EvidenceGraph"
                if structural_error is None
                else structural_error.message
            ),
        )

        resolved_coverage = resolve_selected_knowledge_coverage(
            evidence_graph,
            selected_id_set,
        )
        full_selected = set(resolved_coverage.selected_knowledge_ids)
        required_ids = {
            item.id for item in knowledge_graph.nodes if item.importance == "required"
        }
        missing_required = sorted(required_ids - full_selected)
        result.add(
            rule="knowledge_coverage",
            passed=not missing_required,
            evidence=missing_required or sorted(full_selected & required_ids),
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge",
            target_id=missing_required[0] if missing_required else owner_id,
            description="ContentSelection must fully cover every required knowledge item",
        )

        redundant_selected: list[str] = []
        try:
            redundancy = self.redundancy_analyzer.analyze(knowledge_graph, evidence_graph)
            redundant = {
                item.segment_id
                for item in redundancy.segments
                if item.classification == "REDUNDANT"
            }
            redundant_selected = sorted(redundant & selected_id_set)
        except (KeyError, LearningArtifactValidationError, ValueError):
            redundant_selected = sorted(selected_id_set)
        result.add(
            rule="content_redundancy",
            passed=not redundant_selected,
            evidence=redundant_selected,
            owner_id=owner_id,
            severity="major",
            target_type="content_segment",
            target_id=redundant_selected[0] if redundant_selected else owner_id,
            description="ContentSelection must not contain removable redundant segments",
        )

        missing_context = sorted(
            {
                context_id
                for segment_id in selected_id_set
                if segment_id in segments
                for context_id in segments[segment_id].context_segment_refs
                if context_id not in selected_id_set
            }
        )
        result.add(
            rule="evidence_validity",
            passed=not missing_context,
            evidence=missing_context,
            owner_id=owner_id,
            severity="major",
            target_type="content_segment",
            target_id=missing_context[0] if missing_context else owner_id,
            description="all CONTEXT_REQUIRED segments must be included",
        )

        unique_segments = [segments[item] for item in dict.fromkeys(selected_ids) if item in segments]
        expected_duration = range_union_duration_seconds(evidence_graph, selected_id_set)
        duration_valid = (
            len(selected_ids) == len(selected_id_set)
            and len(unique_segments) == len(selected_id_set)
            and selection.total_duration_seconds == expected_duration
        )
        result.add(
            rule="evidence_validity",
            passed=duration_valid,
            evidence=[
                f"declared={selection.total_duration_seconds}",
                f"derived={expected_duration}",
            ],
            owner_id=owner_id,
            severity="blocker",
            target_type="content_selection",
            target_id=owner_id,
            description="selection duration must equal the union of selected video ranges",
        )

        resource_count = len({item.resource_id for item in unique_segments})
        resource_limit = scope.content_budget.maximum_video_count or 5
        result.add(
            rule="content_redundancy",
            passed=resource_count <= resource_limit,
            evidence=[f"resources={resource_count}", f"limit={resource_limit}"],
            owner_id=owner_id,
            severity="major",
            target_type="content_selection",
            target_id=owner_id,
            description="selected resource count must respect the configured content budget",
        )

        selected_knowledge = set(resolved_coverage.selected_knowledge_ids)
        gap_ids = {item.knowledge_id for item in selection.coverage_gaps}
        evidence = {item.id: item for item in evidence_graph.evidence}
        fully_available = {
            edge.knowledge_id
            for edge in evidence_graph.coverage_edges
            if edge.coverage_strength == "full"
            and edge.evidence_refs
            and all(
                item_id in evidence
                and evidence[item_id].verification_status == "verified"
                and evidence[item_id].segment_id == edge.segment_id
                and evidence[item_id].kind
                in {
                    "transcript_span",
                    "caption_span",
                    "chapter_marker",
                    "manual_verified",
                }
                for item_id in edge.evidence_refs
            )
        }
        false_gaps = sorted(
            (selected_knowledge & gap_ids) | (gap_ids & fully_available)
        )
        result.add(
            rule="knowledge_coverage",
            passed=not false_gaps,
            evidence=false_gaps,
            owner_id=owner_id,
            severity="blocker",
            target_type="coverage_gap",
            target_id=false_gaps[0] if false_gaps else owner_id,
            description="CoverageGap must represent genuinely unavailable verified coverage",
        )
        return result

__all__ = ["SelectionQualityValidator"]
