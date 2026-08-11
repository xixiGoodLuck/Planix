from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, computed_field

from ...contracts import (
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContentPlan,
    LearningContract,
    LearningScope,
)
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from ..services.redundancy_analyzer import RedundancyAnalyzer


class SelectionValidationIssue(LearningContract):
    rule: str = Field(min_length=1)
    severity: Literal["blocker", "major", "minor"]
    target_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SelectionValidationReport(LearningContract):
    required_coverage_complete: bool
    hard_rules_passed: bool
    issues: list[SelectionValidationIssue] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        return bool(
            self.required_coverage_complete
            and self.hard_rules_passed
            and not any(item.severity in {"blocker", "major"} for item in self.issues)
        )


@dataclass(frozen=True)
class ValidatedContentSelection:
    content_selection: ContentSelection
    report: SelectionValidationReport


class ContentSelectionValidator:
    def __init__(
        self,
        artifact_validator: LearningArtifactValidator | None = None,
        redundancy_analyzer: RedundancyAnalyzer | None = None,
    ):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()
        self.redundancy_analyzer = redundancy_analyzer or RedundancyAnalyzer()

    def validate_selection(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
    ) -> ValidatedContentSelection:
        normalized = self.artifact_validator.validate_content_selection(
            scope,
            knowledge_graph,
            evidence_graph,
            selection,
        )
        edges = {item.id: item for item in evidence_graph.coverage_edges}
        segments = {item.id: item for item in evidence_graph.segments}
        selected_segment_ids = {item.segment_id for item in normalized.selected_segments}
        selected_knowledge = {
            knowledge_id
            for item in normalized.selected_segments
            for knowledge_id in item.knowledge_refs
        }
        issues: list[SelectionValidationIssue] = []
        for item in normalized.selected_segments:
            facts = item.selection_facts
            if facts is None:
                issues.append(
                    SelectionValidationIssue(
                        rule="recommendation_facts",
                        severity="major",
                        targetId=item.id,
                        message="selected segment has no code-owned recommendation facts",
                    )
                )
            elif set(facts.knowledge_covered) != set(item.knowledge_refs):
                issues.append(
                    SelectionValidationIssue(
                        rule="recommendation_facts",
                        severity="major",
                        targetId=item.id,
                        message="recommendation facts do not match selected knowledge refs",
                    )
                )
            for context_id in segments[item.segment_id].context_segment_refs:
                if context_id not in selected_segment_ids:
                    issues.append(
                        SelectionValidationIssue(
                            rule="context_required",
                            severity="major",
                            targetId=item.segment_id,
                            message=f"required context segment is not selected: {context_id}",
                        )
                    )

        gap_ids = {item.knowledge_id for item in normalized.coverage_gaps}
        overlapping = selected_knowledge & gap_ids
        if overlapping:
            raise LearningArtifactValidationError(
                "coverage_gap_truth",
                "contentSelection.coverageGaps",
                f"selected knowledge cannot also be a coverage gap: {sorted(overlapping)}",
            )

        required_ids = {
            node.id for node in knowledge_graph.nodes if node.importance == "required"
        }
        fully_selected: set[str] = set()
        for item in normalized.selected_segments:
            for edge_id in item.coverage_edge_refs:
                edge = edges[edge_id]
                if edge.coverage_strength == "full":
                    fully_selected.add(edge.knowledge_id)
        missing_required = required_ids - fully_selected
        for knowledge_id in sorted(missing_required):
            issues.append(
                SelectionValidationIssue(
                    rule="required_knowledge_coverage",
                    severity="blocker",
                    targetId=knowledge_id,
                    message="required knowledge does not have selected full coverage",
                )
            )

        redundancy = self.redundancy_analyzer.analyze(
            knowledge_graph,
            evidence_graph,
        )
        redundant_ids = {
            item.segment_id
            for item in redundancy.segments
            if item.classification == "REDUNDANT"
        }
        for segment_id in sorted(selected_segment_ids & redundant_ids):
            issues.append(
                SelectionValidationIssue(
                    rule="content_redundancy",
                    severity="major",
                    targetId=segment_id,
                    message="selection contains a removable redundant segment",
                )
            )
        report = SelectionValidationReport(
            requiredCoverageComplete=not missing_required,
            hardRulesPassed=True,
            issues=issues,
        )
        return ValidatedContentSelection(content_selection=normalized, report=report)

    def validate_plan(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
        plan: LearningContentPlan,
    ) -> LearningContentPlan:
        validated = self.validate_selection(
            scope,
            knowledge_graph,
            evidence_graph,
            selection,
        )
        if not validated.report.passed:
            issue = next(
                (
                    item
                    for item in validated.report.issues
                    if item.severity in {"blocker", "major"}
                ),
                None,
            )
            raise LearningArtifactValidationError(
                issue.rule if issue else "selection_validation",
                "contentSelection",
                issue.message if issue else "content selection did not pass validation",
            )
        normalized = self.artifact_validator.validate_content_plan(
            scope,
            knowledge_graph,
            evidence_graph,
            validated.content_selection,
            plan,
        )
        for item in normalized.items:
            for recommendation in item.recommended_content:
                if recommendation.selection_facts is None:
                    raise LearningArtifactValidationError(
                        "recommendation_facts",
                        f"learningContentPlan.items.{item.knowledge_id}",
                        "recommended content has no structured selection facts",
                    )
        return normalized


__all__ = [
    "ContentSelectionValidator",
    "SelectionValidationIssue",
    "SelectionValidationReport",
    "ValidatedContentSelection",
]
