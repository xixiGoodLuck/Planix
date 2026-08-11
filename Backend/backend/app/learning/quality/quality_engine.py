from __future__ import annotations

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ..generators.base import artifact_ref, generated_id
from ..validators import LearningArtifactValidator
from .validators import (
    EvidenceQualityValidator,
    KnowledgeQualityValidator,
    PlanQualityValidator,
    QualityEvaluation,
    SelectionQualityValidator,
)


class LearningQualityEngine:
    """Deterministic, model-independent quality gate for Learning artifacts."""

    def __init__(
        self,
        *,
        knowledge_validator: KnowledgeQualityValidator | None = None,
        evidence_validator: EvidenceQualityValidator | None = None,
        selection_validator: SelectionQualityValidator | None = None,
        plan_validator: PlanQualityValidator | None = None,
        artifact_validator: LearningArtifactValidator | None = None,
    ):
        self.knowledge_validator = knowledge_validator or KnowledgeQualityValidator()
        self.evidence_validator = evidence_validator or EvidenceQualityValidator()
        self.selection_validator = selection_validator or SelectionQualityValidator()
        self.plan_validator = plan_validator or PlanQualityValidator()
        self.artifact_validator = artifact_validator or LearningArtifactValidator()

    def evaluate(
        self,
        *,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        content_selection: ContentSelection,
        learning_content_plan: LearningContentPlan,
    ) -> LearningQualityReport:
        evaluation = QualityEvaluation()
        evaluation.extend(
            self.knowledge_validator.evaluate(scope, capability_graph, knowledge_graph)
        )
        evaluation.extend(self.evidence_validator.evaluate(knowledge_graph, evidence_graph))
        evaluation.extend(
            self.selection_validator.evaluate(
                scope,
                knowledge_graph,
                evidence_graph,
                content_selection,
            )
        )
        evaluation.extend(
            self.plan_validator.evaluate(
                scope,
                knowledge_graph,
                evidence_graph,
                content_selection,
                learning_content_plan,
            )
        )
        passed_count = sum(item.passed for item in evaluation.checks)
        score = 100.0 * passed_count / len(evaluation.checks) if evaluation.checks else 0.0
        report = LearningQualityReport(
            artifactId=generated_id(
                "learning-quality",
                learning_content_plan.artifact_id,
                learning_content_plan.version,
                content_selection.artifact_id,
            ),
            targetRef=artifact_ref("learning_content_plan", learning_content_plan),
            scopeRef=artifact_ref("learning_scope", scope),
            capabilityGraphRef=artifact_ref("capability_graph", capability_graph),
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
            contentSelectionRef=artifact_ref("content_selection", content_selection),
            hardRulesPassed=all(item.passed for item in evaluation.checks),
            qualityChecks=evaluation.checks,
            issues=evaluation.issues,
            remainingGaps=content_selection.coverage_gaps,
            score=score,
        )
        self.artifact_validator.validate_quality_report(
            scope,
            capability_graph,
            knowledge_graph,
            evidence_graph,
            content_selection,
            learning_content_plan,
            report,
        )
        return report


__all__ = ["LearningQualityEngine"]
