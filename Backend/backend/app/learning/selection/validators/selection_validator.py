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
from ...selection_semantics import (
    explicit_budget_seconds,
    marginal_duration_seconds,
    range_union_duration_seconds,
    resolve_selected_knowledge_coverage,
)
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from ..services.coverage_analyzer import CoverageAnalyzer
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
        segments = {item.id: item for item in evidence_graph.segments}
        selected_segment_ids = {item.segment_id for item in normalized.selected_segments}
        selected_coverage = resolve_selected_knowledge_coverage(
            evidence_graph,
            selected_segment_ids,
        )
        selected_knowledge = set(selected_coverage.selected_knowledge_ids)
        issues: list[SelectionValidationIssue] = []
        for item in normalized.selected_segments:
            expected_edges = [
                edge
                for edge in selected_coverage.selected_coverage_edges
                if edge.segment_id == item.segment_id
            ]
            expected_edge_ids = {edge.id for edge in expected_edges}
            expected_knowledge = {edge.knowledge_id for edge in expected_edges}
            expected_evidence = {
                evidence_id
                for edge in expected_edges
                for evidence_id in edge.evidence_refs
            }
            if (
                set(item.coverage_edge_refs) != expected_edge_ids
                or set(item.knowledge_refs) != expected_knowledge
                or set(item.evidence_refs) != expected_evidence
            ):
                raise LearningArtifactValidationError(
                    "selected_coverage_projection",
                    f"contentSelection.selectedSegments.{item.id}",
                    "selected segment facts must project every verified FULL coverage edge",
                )
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

        coverage_report = CoverageAnalyzer().analyze(knowledge_graph, evidence_graph)
        coverage_by_knowledge = {
            item.knowledge_id: item for item in coverage_report.knowledge
        }
        false_gaps = sorted(
            knowledge_id
            for knowledge_id in gap_ids
            if coverage_by_knowledge[knowledge_id].sufficient
        )
        if false_gaps:
            raise LearningArtifactValidationError(
                "coverage_gap_truth",
                "contentSelection.coverageGaps",
                f"FULL verified coverage cannot be represented as a gap: {false_gaps}",
            )

        knowledge = {node.id: node for node in knowledge_graph.nodes}
        budget_seconds = explicit_budget_seconds(scope)
        selected_duration = range_union_duration_seconds(
            evidence_graph,
            selected_segment_ids,
        )
        remaining_budget = (
            None
            if budget_seconds is None
            else max(0, budget_seconds - selected_duration)
        )
        omission_ids = {
            item.knowledge_id for item in normalized.selection_omissions
        }
        redundancy = self.redundancy_analyzer.analyze(
            knowledge_graph,
            evidence_graph,
        )
        redundant_ids = {
            item.segment_id
            for item in redundancy.segments
            if item.classification == "REDUNDANT"
        }
        for omission in normalized.selection_omissions:
            node = knowledge[omission.knowledge_id]
            coverage = coverage_by_knowledge[omission.knowledge_id]
            if not coverage.sufficient:
                raise LearningArtifactValidationError(
                    "selection_omission_truth",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}",
                    "selection omission requires FULL verified coverage",
                )
            if node.importance == "required" or omission.importance != node.importance:
                raise LearningArtifactValidationError(
                    "selection_omission_truth",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}",
                    "required knowledge cannot be omitted",
                )
            full_candidates = sorted(
                {
                    edge.segment_id
                    for edge in CoverageAnalyzer.valid_coverage_edges(evidence_graph)
                    if edge.knowledge_id == omission.knowledge_id
                    and edge.coverage_strength == "full"
                }
            )
            if omission.candidate_segment_refs != full_candidates:
                raise LearningArtifactValidationError(
                    "selection_omission_truth",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.candidateSegmentRefs",
                    "omission candidates must exactly match verified FULL segment coverage",
                )
            true_marginal = min(
                (
                    marginal_duration_seconds(
                        evidence_graph,
                        selected_segment_ids,
                        self._with_context(segment_id, segments),
                    )
                    for segment_id in full_candidates
                ),
                default=0,
            )
            if omission.marginal_duration_seconds != true_marginal:
                raise LearningArtifactValidationError(
                    "selection_omission_duration",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.marginalDurationSeconds",
                    f"marginal duration must equal the time-range union delta {true_marginal}",
                )
            if omission.reason == "budget_limit" and (
                remaining_budget is None or true_marginal <= remaining_budget
            ):
                raise LearningArtifactValidationError(
                    "selection_omission_policy",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.reason",
                    "budget_limit requires marginal duration greater than remaining budget",
                )
            requested_optional = (
                node.importance == "optional"
                and self._scope_requests_knowledge(scope, node.name)
            )
            if omission.reason == "not_required_by_scope" and (
                node.importance != "optional" or requested_optional
            ):
                raise LearningArtifactValidationError(
                    "selection_omission_policy",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.reason",
                    "not_required_by_scope is only valid for optional knowledge outside the current scope",
                )
            nonredundant_candidates = set(full_candidates) - redundant_ids
            if omission.reason == "lower_priority" and (
                (node.importance == "optional" and not requested_optional)
                or (
                    remaining_budget is not None
                    and true_marginal <= remaining_budget
                    and nonredundant_candidates
                )
            ):
                raise LearningArtifactValidationError(
                    "selection_omission_policy",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.reason",
                    "lower_priority does not justify omitting in-budget selectable knowledge",
                )
            expected_policy_refs = {
                "budget_limit": ["content_budget", "marginal_duration_union"],
                "lower_priority": ["minimum_sufficient_selection", "importance_priority"],
                "not_required_by_scope": ["minimum_sufficient_selection", "scope_relevance"],
            }[omission.reason]
            if omission.policy_rule_refs != expected_policy_refs:
                raise LearningArtifactValidationError(
                    "selection_omission_policy",
                    f"contentSelection.selectionOmissions.{omission.knowledge_id}.policyRuleRefs",
                    "selection omission policy refs must match the code-owned reason",
                )
        conflicting_omissions = selected_knowledge & omission_ids
        if conflicting_omissions:
            raise LearningArtifactValidationError(
                "selection_partition",
                "contentSelection.selectionOmissions",
                f"selected knowledge cannot also be omitted: {sorted(conflicting_omissions)}",
            )

        required_ids = {
            node.id for node in knowledge_graph.nodes if node.importance == "required"
        }
        fully_selected = set(selected_coverage.selected_knowledge_ids)
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

    @staticmethod
    def _with_context(segment_id: str, segments) -> list[str]:
        ordered: list[str] = []

        def add(current_id: str) -> None:
            for context_id in segments[current_id].context_segment_refs:
                if context_id not in ordered:
                    add(context_id)
            if current_id not in ordered:
                ordered.append(current_id)

        add(segment_id)
        return ordered

    @staticmethod
    def _scope_requests_knowledge(scope: LearningScope, knowledge_name: str) -> bool:
        normalized_name = " ".join(knowledge_name.casefold().split())
        normalized_scope = " ".join(
            f"{scope.user_goal} {scope.target_result}".casefold().split()
        )
        return bool(normalized_name and normalized_name in normalized_scope)

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
