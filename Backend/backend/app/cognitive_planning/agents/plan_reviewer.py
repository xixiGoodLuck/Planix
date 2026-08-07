from __future__ import annotations

from ..contracts import (
    ConstraintSet,
    ContextPack,
    PlanBlueprint,
    QualityReport,
    SafePlanningError,
    SemanticReviewResult,
    UnderstandingSnapshot,
)
from .base import AgentResult, CognitiveModelClient, PlanningModelUnavailable


REVIEW_SYSTEM = """
You are Planix Plan Reviewer. Review only the CURRENT PlanBlueprint version against its current approved
UnderstandingSnapshot, ConstraintSet, ContextPack, and deterministic hard-validation report. Code-owned hard
validation is authoritative: do not recalculate or override structural, numeric, version, provenance, dependency,
capacity, or budget facts. Review only semantic goal alignment, completeness, user fit, domain correctness, task
specificity, outcome orientation, risk resilience, resource actionability, minimality, adaptability, and evidence
sufficiency.

Every issue must bind to an existing current target id and contain evidence. blocker means the core goal changed,
safety is violated, an immutable constraint is impossible, a key success criterion is absent, or verified evidence
was fabricated. major means a missing core path, non-executable key task, obvious user mismatch, inadequate key
deliverable, missing critical fallback, or another defect that materially threatens success. Wording, noncritical
ordering, small redundancy, and optional polish are minor. Minor issues do not block. A score may be supplied only
for diagnostics and must never determine pass/fail or severity. Return native semantic issues only, JSON only, and
do not reveal hidden reasoning.
""".strip()


class PlanReviewer:
    name = "Plan Quality Reviewer"
    artifact_type = "plan_quality_report"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        understanding: UnderstandingSnapshot,
        constraints: ConstraintSet,
        context: ContextPack,
        plan: PlanBlueprint,
        hard_report: QualityReport,
    ) -> AgentResult[SemanticReviewResult]:
        result = self.model.complete_contract(
            stage="semantic_review",
            task_type="planning_review",
            feature="planning_semantic_review",
            system=REVIEW_SYSTEM,
            payload={
                "approvedUnderstanding": understanding.model_dump(by_alias=True),
                "constraints": constraints.model_dump(by_alias=True),
                "context": context.model_dump(by_alias=True),
                "currentPlan": plan.model_dump(by_alias=True),
                "hardValidation": hard_report.model_dump(by_alias=True),
            },
            contract_type=SemanticReviewResult,
            temperature=0.1,
        )
        review = result.artifact.model_copy(
            update={"target_artifact_id": plan.artifact_id, "target_version": plan.version}
        )
        valid_targets = {
            plan.artifact_id,
            understanding.artifact_id,
            constraints.artifact_id,
            context.artifact_id,
            *(item.id for item in plan.tasks),
            *(item.id for item in plan.milestones),
        }
        invalid = [issue for issue in review.issues if issue.target_id not in valid_targets]
        unsupported = [
            issue for issue in review.issues
            if issue.severity in {"blocker", "major"} and not issue.evidence_refs
        ]
        if invalid or unsupported:
            raise PlanningModelUnavailable(
                "semantic_review",
                SafePlanningError(
                    stage="semantic_review",
                    errorType="invalid_model_output",
                    message="Semantic review returned an invalid target or an unsupported blocking issue.",
                    retryable=True,
                    attempts=result.model_usage.get("attempts", []),
                ),
            )
        return AgentResult(review, result.model_usage)


__all__ = ["PlanReviewer"]
