from __future__ import annotations

from ...contracts import ContentSelection, EvidenceGraph, KnowledgeGraph, LearningScope
from ...selection.services.redundancy_analyzer import RedundancyAnalyzer
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from .base import QualityEvaluation


class SelectionQualityValidator:
    def __init__(
        self,
        artifact_validator: LearningArtifactValidator | None = None,
        redundancy_analyzer: RedundancyAnalyzer | None = None,
    ):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()
        self.redundancy_analyzer = redundancy_analyzer or RedundancyAnalyzer()

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
            self.artifact_validator.validate_content_selection(
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

        full_selected = {
            edge.knowledge_id
            for item in selection.selected_segments
            for edge_id in item.coverage_edge_refs
            if (edge := edges.get(edge_id)) is not None
            and edge.segment_id == item.segment_id
            and edge.coverage_strength == "full"
        }
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
        expected_duration = sum(item.end_seconds - item.start_seconds for item in unique_segments)
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
            description="selection duration must equal the sum of unique selected segments",
        )

        overlaps = self._overlapping_segments(unique_segments)
        result.add(
            rule="content_redundancy",
            passed=not overlaps,
            evidence=overlaps,
            owner_id=owner_id,
            severity="major",
            target_type="content_segment",
            target_id=overlaps[0] if overlaps else owner_id,
            description="overlapping video time must not be counted twice",
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

        selected_knowledge = {
            knowledge_id
            for item in selection.selected_segments
            for knowledge_id in item.knowledge_refs
        }
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

    @staticmethod
    def _overlapping_segments(segments) -> list[str]:
        overlaps: list[str] = []
        for index, left in enumerate(segments):
            for right in segments[index + 1 :]:
                if left.resource_id != right.resource_id:
                    continue
                if max(left.start_seconds, right.start_seconds) < min(
                    left.end_seconds,
                    right.end_seconds,
                ):
                    overlaps.append(f"{left.id}:{right.id}")
        return sorted(overlaps)


__all__ = ["SelectionQualityValidator"]
