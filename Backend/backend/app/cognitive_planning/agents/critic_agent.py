from __future__ import annotations

from typing import Any

from ...services.cognitive_planning.contracts import (
    EvidencePack,
    ExecutionBlueprint,
    PlanCritiqueReport,
    PlanningLearningUpdate,
    StrategyPortfolio,
)
from ..contracts import GoalUnderstandingArtifact, RealityAssessment
from .base import AgentResult, CognitiveModelClient


CRITIC_SYSTEM = """
You are Planix Critic Agent and are independent from every generator. Reject template leakage, weak user fit,
domain mistakes, unsafe advice, unrealistic workload, invented or unusable resources, vague tasks, dependency
errors, and plans the user cannot actually execute. Compare the plan with the Goal Understanding, Reality
Assessment, and Evidence Pack. Simulate the first action, the hardest period, resource failure, task failure,
half the expected available time, and one domain-specific risk. You have Calendar veto power. Return targeted
repair instructions to the responsible agent. The half-time simulation may extend the timeline; needing that
extension is not itself a defect. It must preserve hard requirements and may remove only optional scope. Never
recommend dropping an explicit hard requirement as a fallback. A passing report may retain clearly disclosed minor
risks; request repair only for blocker/major findings that violate the explicit goal, safety, feasibility, or
deterministic execution invariants. Because Strategy was already user-approved, target execution_designer for
mitigations unless an upstream artifact directly violates a hard constraint or safety boundary. Return only JSON
and never hidden chain-of-thought. At this pre-Calendar approval stage, a truthful relative scheduleWindow is
sufficient; scheduledDate may remain null until Calendar permission. Do not block solely because exact dates are
absent. A scheduleWindow spanning multiple periods distributes its estimatedMinutes across that range unless an
explicit weeklyMinutes allocation says otherwise; sum overlapping allocations before judging a capacity violation.
A missing exact start date or preparation duration that the Goal marked non-blocking is not permission to invent a
short deadline and fail the half-time simulation; extend the relative timeline when no hard deadline forbids it.
For an explicit CNY spending cap, require budgetSummary, recompute every amountCny allocation, distinguish repeated
references from new costs, and verify that routine costs plus contingency remain within spendingLimitCny. Any repair
instruction containing numeric allocations must itself add up to no more than the stated cap; never prescribe an
arithmetically impossible repair.
Treat cost figures in the approved Strategy as planning estimates, not immutable requirements. The current
Execution may refine them when its allocations are internally consistent with the user cap and supported by the
Evidence Pack. Do not demand that Execution copy a stale Strategy estimate solely because the numbers differ;
identify concrete contrary evidence or a cap/category inconsistency before requesting a cost repair.
For numeric cost claims, use this authority order: explicit Goal facts and hard constraints first, then current
verifiable Evidence with source references, then lower-confidence model_knowledge, and finally Strategy rationale
estimates. Never create an exact price, a major/blocker finding, or a numeric repair from your own uncited memory.
When a time-sensitive price is unverified or formal artifacts contain conflicting estimates, require verification
against an official current source plus a conservative capped allocation and contingency; do not invent an exact
replacement or reverse a repaired Execution merely to restore a lower-authority Strategy estimate.
A blocked status must name at least one blocker issue. A needs_repair status must include a major/blocker
issue and a matching repair request. If neither exists and deterministic invariants hold, return passed with
calendarWritable. Before returning the first critique, complete the entire audit and report every blocker/major
finding together. Never return status=passed unless the overall score is at least 90; below 90, return needs_repair
with executable repair instructions that target the weakest dimensions. Do not reveal one issue per repair round.
Treat stated weekly availability as a capacity ceiling unless the Goal explicitly makes it a minimum or exact
commitment; unused capacity is valid. Treat "within N weeks" as a latest deadline unless the Goal explicitly says
exactly N weeks. Never elevate important/optional unknowns or accepted assumptions to blocker/major findings; use
verification and fallback instead. The ExecutionBlueprint schema permits at most 10 tasks, so never request more;
group recurring sessions. sourceRef is optional: without a verified Evidence source, selection criteria,
acquisition/verification steps, and a fallback are actionable, and you must not demand or invent a URL, venue, or
provider name. The half-time simulation evaluates contingency quality, not the primary capacity; do not downgrade
the primary plan solely because the contingency extends the timeline. When previousCritiqueReport or repairHistory
is present, use it only for continuity, not as authority: independently re-audit the current formal artifacts and
first verify every prior blocker/major repair. A new blocker/major must state in evidence whether it was
introduced_by_revision or previously_missed. Never reverse a compliant prior repair or prescribe a contradictory
repair.
""".strip()


LEARNING_SYSTEM = """
You are Planix Critic Agent learning from user feedback. Diagnose the failed assumption and responsible stage,
repair the current artifact, and create a user-model memory only when the observation is useful beyond this
single plan. A single observation remains tentative and must retain its evidence and confidence. Approval
phrases are not feedback. Return only the requested JSON and never hidden chain-of-thought.
""".strip()


class CriticAgent:
    name = "Critic Agent"
    critique_artifact_type = "critique_report"
    learning_artifact_type = "planning_learning_update"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def critique(
        self,
        goal: GoalUnderstandingArtifact,
        evidence: EvidencePack,
        strategy: StrategyPortfolio,
        execution: ExecutionBlueprint,
        *,
        reality: RealityAssessment | None = None,
        previous_critique: PlanCritiqueReport | None = None,
        repair_history: list[dict[str, Any]] | None = None,
        critic_policy: dict[str, Any] | None = None,
        critic_policy_violations: list[dict[str, Any]] | None = None,
    ) -> AgentResult[PlanCritiqueReport]:
        policy_payload = dict(critic_policy or {})
        policy_payload.update(
            {
                "enforcement": "deterministic_semantic_policy",
                "reviewMode": (
                    "policy_repair" if critic_policy_violations else "initial_review"
                ),
                "violationsToCorrect": list(critic_policy_violations or []),
                "mustReviewSameExecution": True,
            }
        )
        payload = {
            "goalUnderstanding": goal.model_dump(by_alias=True),
            "realityAssessment": reality.model_dump(by_alias=True) if reality else None,
            "evidencePack": evidence.model_input_view(),
            "strategyProposal": strategy.model_dump(by_alias=True),
            "strategyApproval": {
                "status": strategy.status,
                "recommendedStrategyId": strategy.recommended_strategy_id,
                "approvedStrategyId": strategy.approved_strategy_id,
            },
            "executionPlan": execution.model_dump(by_alias=True),
            "criticPolicy": policy_payload,
        }
        if previous_critique is not None:
            payload["previousCritiqueReport"] = previous_critique.model_dump(by_alias=True)
        if repair_history:
            payload["repairHistory"] = list(repair_history)
        if critic_policy_violations:
            payload["criticPolicyViolations"] = list(critic_policy_violations)
        return self.model.complete_contract(
            stage="independent_critique",
            task_type="planning_critique",
            feature="cognitive_os_independent_critique",
            system=CRITIC_SYSTEM,
            payload=payload,
            contract_type=PlanCritiqueReport,
            temperature=0.05,
        )

    def learn(
        self,
        feedback: str,
        *,
        goal: GoalUnderstandingArtifact | None,
        evidence: EvidencePack | None,
        strategy: StrategyPortfolio | None,
        execution: ExecutionBlueprint | None,
        critique: PlanCritiqueReport | None,
        reality: RealityAssessment | None = None,
    ) -> AgentResult[PlanningLearningUpdate]:
        return self.model.complete_contract(
            stage="feedback_learning",
            task_type="planning_learning",
            feature="cognitive_os_feedback_learning",
            system=LEARNING_SYSTEM,
            payload={
                "feedback": feedback,
                "goalUnderstanding": goal.model_dump(by_alias=True) if goal else None,
                "realityAssessment": reality.model_dump(by_alias=True) if reality else None,
                "evidencePack": evidence.model_input_view() if evidence else None,
                "strategyProposal": strategy.model_dump(by_alias=True) if strategy else None,
                "executionPlan": execution.model_dump(by_alias=True) if execution else None,
                "criticReport": critique.model_dump(by_alias=True) if critique else None,
            },
            contract_type=PlanningLearningUpdate,
            temperature=0.15,
        )
