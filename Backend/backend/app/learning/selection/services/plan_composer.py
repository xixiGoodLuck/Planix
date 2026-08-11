from __future__ import annotations

from ...contracts import (
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContentItem,
    LearningContentPlan,
    LearningScope,
    RecommendedContent,
)
from ...generators import LearningGenerationError
from ...generators.base import artifact_ref, generated_id


class PlanComposer:
    """Deterministically projects selected Segment ids into a user-facing plan."""

    def compose(
        self,
        scope: LearningScope,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        selection: ContentSelection,
    ) -> LearningContentPlan:
        segments = {item.id: item for item in evidence_graph.segments}
        resources = {item.id: item for item in evidence_graph.resources}
        gaps = {item.knowledge_id: item for item in selection.coverage_gaps}
        items: list[LearningContentItem] = []
        for knowledge in knowledge_graph.nodes:
            selected = [
                item
                for item in selection.selected_segments
                if knowledge.id in item.knowledge_refs
            ]
            recommendations: list[RecommendedContent] = []
            for selected_item in selected:
                segment = segments.get(selected_item.segment_id)
                if segment is None:
                    raise LearningGenerationError(
                        "learning_content_plan",
                        f"selection references missing segment {selected_item.segment_id}",
                    )
                resource = resources.get(segment.resource_id)
                if resource is None:
                    raise LearningGenerationError(
                        "learning_content_plan",
                        f"segment references missing resource {segment.resource_id}",
                    )
                if selected_item.selection_facts is None:
                    raise LearningGenerationError(
                        "learning_content_plan",
                        f"selection {selected_item.id} has no recommendation facts",
                    )
                recommendations.append(
                    RecommendedContent(
                        selectionId=selected_item.id,
                        resourceId=resource.id,
                        segmentId=segment.id,
                        videoTitle=resource.title,
                        segmentSummary=segment.content_summary,
                        durationSeconds=segment.end_seconds - segment.start_seconds,
                        recommendationReason=selected_item.selection_reason,
                        selectionFacts=selected_item.selection_facts,
                    )
                )
            gap = gaps.get(knowledge.id)
            items.append(
                LearningContentItem(
                    knowledgeId=knowledge.id,
                    knowledgeName=knowledge.name,
                    knowledgeExplanation=knowledge.explanation,
                    whyRequired=knowledge.why_required,
                    recommendedContent=recommendations,
                    uncoveredReason=gap.reason if gap else None,
                )
            )
        return LearningContentPlan(
            artifactId=generated_id(
                "learning-content-plan",
                selection.artifact_id,
                selection.version,
                knowledge_graph.artifact_id,
            ),
            scopeRef=artifact_ref("learning_scope", scope),
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
            contentSelectionRef=artifact_ref("content_selection", selection),
            items=items,
            totalDurationSeconds=0,
            evidenceGaps=selection.coverage_gaps,
            deferredKnowledge=selection.selection_omissions,
        )


__all__ = ["PlanComposer"]
