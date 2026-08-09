from __future__ import annotations

from ..contracts import (
    ConstraintSet,
    ContextPack,
    PlanBlueprint,
    QualityIssue,
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

Treat approved assumptions as the current planning boundary unless they contradict an explicit immutable requirement.
Do not promote confirmed non-blocking unknowns into major issues or require another clarification task. Do not infer
that a user who knows language basics already knows OOP, testing, or engineering practices; a task that advances from
confirmed basics is not a user-fit defect for that reason alone. Exact dates,
When a non-blocking skill detail is unknown, conservative prerequisite learning is allowed; do not require user
confirmation or make that learning optional merely because the approved snapshot does not confirm the skill.
week placement, capacity, and buffer checks belong to deterministic Schedule validation, not this Plan review. Plan
tasks may span multiple Schedule sessions, so a task estimate larger than a daily/session limit is not itself a
semantic defect. Inspect the entire current plan before claiming a capability is absent. A learning curve, optional polish, or an imperfect but
usable completion check is minor unless it makes a required deliverable clearly non-executable.

Every issue must bind to an existing current target id and contain evidence. blocker means the core goal changed,
safety is violated, an immutable constraint is impossible, a key success criterion is absent, or verified evidence
was fabricated. major means a missing core path, non-executable key task, obvious user mismatch, inadequate key
deliverable, missing critical fallback, or another defect that materially threatens success. Wording, noncritical
ordering, small redundancy, and optional polish are minor. Minor issues do not block. A score may be supplied only
for diagnostics and must never determine pass/fail or severity. Every blocker or major issue must include at least
one applicable operation from the supplied supportedOperations list. If there is no actionable blocker or major,
return no such issue. targetId and evidenceRefs must use only the exact strings supplied in validTargetIds and
validEvidenceRefs; JSON field names such as nextQuestion are not evidence references. Return native semantic issues
only, JSON only, and do not reveal hidden reasoning.
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
        valid_targets = {
            plan.artifact_id,
            understanding.artifact_id,
            constraints.artifact_id,
            context.artifact_id,
            *(item.id for section in (understanding.facts, understanding.constraints, understanding.preferences, understanding.success_signals, understanding.assumptions, understanding.unknowns, understanding.conflicts) for item in section),
            *constraints.source_constraint_ids,
            *(item.stable_id for item in constraints.semantic),
            *(item.id for item in context.claims),
            *(item.id for item in plan.tasks),
            *(item.id for item in plan.milestones),
        }
        valid_evidence = {
            understanding.artifact_id,
            constraints.artifact_id,
            context.artifact_id,
            plan.artifact_id,
            *(item.id for section in (understanding.facts, understanding.constraints, understanding.preferences, understanding.success_signals, understanding.assumptions, understanding.unknowns, understanding.conflicts) for item in section),
            *constraints.source_constraint_ids,
            *(item.stable_id for item in constraints.semantic),
            *(item.id for item in context.claims),
            *(item.source_ref for item in context.claims),
            *(item.id for item in plan.tasks),
            *(item.id for item in plan.milestones),
            *(item.issue_id for item in hard_report.issues),
        }
        supported_operations = {"add_task", "update_task", "remove_optional_task", "split_task", "move_task", "add_dependency", "remove_dependency", "replace_resource", "update_effort", "add_success_coverage", "add_constraint_coverage"}
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
                "validTargetIds": sorted(valid_targets),
                "validEvidenceRefs": sorted(valid_evidence),
                "supportedOperations": sorted(supported_operations),
            },
            contract_type=SemanticReviewResult,
            temperature=0.1,
        )
        review = result.artifact.model_copy(
            update={"target_artifact_id": plan.artifact_id, "target_version": plan.version}
        )
        review = review.model_copy(update={
            "issues": [
                issue.model_copy(update={"allowed_operations": [operation for operation in issue.allowed_operations if operation in supported_operations]})
                for issue in review.issues
                if not self._outside_semantic_authority(issue, understanding)
            ]
        })
        invalid = [issue for issue in review.issues if issue.target_id not in valid_targets]
        unsupported = [
            issue for issue in review.issues
            if issue.severity in {"blocker", "major"}
            and (
                not issue.evidence_refs
                or bool(set(issue.evidence_refs) - valid_evidence)
                or not issue.allowed_operations
            )
        ]
        if invalid or unsupported:
            invalid_targets = sorted({issue.target_id for issue in invalid})
            invalid_evidence = sorted({ref for issue in unsupported for ref in issue.evidence_refs if ref not in valid_evidence})
            missing_operations = sorted({issue.issue_id for issue in unsupported if not issue.allowed_operations})
            raise PlanningModelUnavailable(
                "semantic_review",
                SafePlanningError(
                    stage="semantic_review",
                    errorType="invalid_model_output",
                    message=(
                        "Semantic review returned invalid current-version bindings: "
                        f"targets={invalid_targets}, evidence={invalid_evidence}, missingOperations={missing_operations}."
                    ),
                    retryable=True,
                    attempts=result.model_usage.get("attempts", []),
                ),
            )
        return AgentResult(review, result.model_usage)

    @staticmethod
    def _outside_semantic_authority(issue: QualityIssue, understanding: UnderstandingSnapshot) -> bool:
        description = issue.description.casefold()
        nonblocking_unknowns = {
            item.id for item in understanding.unknowns
            if item.blocking_category in {"important", "optional"}
        }
        promotes_nonblocking_unknown = bool(set(issue.evidence_refs) & nonblocking_unknowns) and any(
            marker in description for marker in ("clarif", "confirm", "unknown", "澄清", "确认", "未知")
        )
        treats_array_order_as_dependency = any(
            marker in description
            for marker in ("task array", "listed after", "listed before", "forward dependency", "数组", "列在")
        )
        treats_task_as_one_schedule_session = (
            any(marker in description for marker in ("daily time", "daily limit", "per-day", "per day", "session limit"))
            and any(marker in description for marker in ("single task", "split", "exceed", "larger than"))
        )
        rejects_conservative_prerequisite = (
            any(marker in description for marker in ("does not confirm", "not confirmed", "has not confirmed"))
            and any(marker in description for marker in ("knowledge", "skill", "proficiency"))
            and any(marker in description for marker in ("learn", "learning", "prerequisite"))
        )
        recalculates_schedule_timing = (
            any(marker in description for marker in ("week five", "week six", "week 5", "week 6", "scheduled"))
            and any(marker in description for marker in ("before", "after", "start", "complete", "capacity"))
        )
        contradicts_named_existing_task = (
            "does not include any explicit task" in description
            and "task-" in description
        )
        return (
            promotes_nonblocking_unknown
            or treats_array_order_as_dependency
            or treats_task_as_one_schedule_session
            or rejects_conservative_prerequisite
            or recalculates_schedule_timing
            or contradicts_named_existing_task
        )


__all__ = ["PlanReviewer"]
