from __future__ import annotations

from ...contracts import (
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContentPlan,
    LearningScope,
)
from ...validators import LearningArtifactValidationError, LearningArtifactValidator
from .base import QualityEvaluation


class PlanQualityValidator:
    def __init__(self, artifact_validator: LearningArtifactValidator | None = None):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()

    def evaluate(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
        plan: LearningContentPlan,
    ) -> QualityEvaluation:
        result = QualityEvaluation()
        owner_id = plan.artifact_id
        knowledge = {item.id: item for item in knowledge_graph.nodes}
        resources = {item.id: item for item in evidence_graph.resources}
        segments = {item.id: item for item in evidence_graph.segments}
        selected = {item.id: item for item in selection.selected_segments}

        invalid_selection_refs: list[str] = []
        invalid_knowledge_refs: list[str] = []
        missing_facts: list[str] = []
        unsupported_claims: list[str] = []
        for item in plan.items:
            node = knowledge.get(item.knowledge_id)
            if node is None:
                invalid_knowledge_refs.append(item.knowledge_id)
                continue
            if (
                item.knowledge_name != node.name
                or item.knowledge_explanation != node.explanation
                or item.why_required != node.why_required
            ):
                unsupported_claims.append(item.knowledge_id)
            for recommendation in item.recommended_content:
                selected_item = selected.get(recommendation.selection_id)
                segment = segments.get(recommendation.segment_id)
                resource = resources.get(recommendation.resource_id)
                if (
                    selected_item is None
                    or segment is None
                    or selected_item.segment_id != recommendation.segment_id
                    or item.knowledge_id not in selected_item.knowledge_refs
                ):
                    invalid_selection_refs.append(recommendation.selection_id)
                    continue
                expected_resource = resources.get(segment.resource_id)
                if resource is None or expected_resource is None or resource.id != expected_resource.id:
                    invalid_selection_refs.append(recommendation.selection_id)
                    continue
                if recommendation.selection_facts is None or selected_item.selection_facts is None:
                    missing_facts.append(recommendation.selection_id)
                elif recommendation.selection_facts != selected_item.selection_facts:
                    unsupported_claims.append(recommendation.selection_id)
                if (
                    recommendation.video_title != resource.title
                    or recommendation.segment_summary != segment.content_summary
                    or recommendation.duration_seconds
                    != segment.end_seconds - segment.start_seconds
                    or recommendation.recommendation_reason
                    != selected_item.selection_reason
                ):
                    unsupported_claims.append(recommendation.selection_id)

        result.add(
            rule="evidence_validity",
            passed=not invalid_selection_refs,
            evidence=sorted(set(invalid_selection_refs)),
            owner_id=owner_id,
            severity="blocker",
            target_type="learning_content_plan",
            target_id=invalid_selection_refs[0] if invalid_selection_refs else owner_id,
            description="LearningContentPlan may only reference current ContentSelection items",
        )
        result.add(
            rule="knowledge_coverage",
            passed=not invalid_knowledge_refs,
            evidence=sorted(set(invalid_knowledge_refs)),
            owner_id=owner_id,
            severity="blocker",
            target_type="knowledge",
            target_id=invalid_knowledge_refs[0] if invalid_knowledge_refs else owner_id,
            description="all plan knowledge references must exist in KnowledgeGraph",
        )
        result.add(
            rule="evidence_validity",
            passed=not missing_facts,
            evidence=sorted(set(missing_facts)),
            owner_id=owner_id,
            severity="major",
            target_type="recommended_content",
            target_id=missing_facts[0] if missing_facts else owner_id,
            description="every recommendation must include code-owned Recommendation Facts",
        )
        result.add(
            rule="evidence_validity",
            passed=not unsupported_claims,
            evidence=sorted(set(unsupported_claims)),
            owner_id=owner_id,
            severity="major",
            target_type="learning_content_plan",
            target_id=unsupported_claims[0] if unsupported_claims else owner_id,
            description="user-visible claims must be exact projections of validated artifacts",
        )

        structural_error: LearningArtifactValidationError | None = None
        try:
            self.artifact_validator.validate_content_plan(
                scope,
                knowledge_graph,
                evidence_graph,
                selection,
                plan,
            )
        except LearningArtifactValidationError as exc:
            structural_error = exc
        result.add(
            rule="version_compatibility",
            passed=structural_error is None,
            evidence=[] if structural_error is None else [structural_error.path],
            owner_id=owner_id,
            severity="blocker",
            target_type="learning_content_plan",
            target_id=owner_id,
            description=(
                "plan references and projections must bind current artifact versions"
                if structural_error is None
                else structural_error.message
            ),
        )
        return result


__all__ = ["PlanQualityValidator"]
