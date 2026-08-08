from __future__ import annotations

from ..contracts import (
    ConstraintSet,
    ContextPack,
    PlanBlueprint,
    QualityIssue,
    RepairProposal,
    UnderstandingSnapshot,
)
from .base import AgentResult, CognitiveModelClient


PLAN_SYSTEM = """
You are Planix Plan Generator. Produce the native PlanBlueprint directly from the approved
UnderstandingSnapshot, current ConstraintSet, and current ContextPack. Select any useful approach internally;
do not output a strategy portfolio or a second execution plan. Each task must contain concrete action steps,
dependencies, a realistic effort range, a checkable deliverable, completion evidence, risks, fallback, and
lineage to current goal or constraint ids. Preserve every immutable constraint and never fabricate verified
resources. Prefer a concise executable DAG over generic phases. Return JSON only and do not reveal hidden
reasoning.
""".strip()

REPAIR_SYSTEM = """
You are Planix Plan Repair Agent. Return a RepairProposal for exactly one current QualityIssue and the current
PlanBlueprint. Use only the issue's allowedOperations, target the current artifact id/version, preserve immutable
goal and constraint semantics, keep stable task ids whenever possible, and emit at most four operations. Do not
regenerate the whole plan. Return JSON only.
""".strip()


class PlanGenerator:
    name = "Plan Generator"
    artifact_type = "plan_blueprint"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        understanding: UnderstandingSnapshot,
        constraints: ConstraintSet,
        context: ContextPack,
    ) -> AgentResult[PlanBlueprint]:
        result = self.model.complete_contract(
            stage="generate_plan",
            task_type="planning_plan",
            feature="planning_blueprint_generation",
            system=PLAN_SYSTEM,
            payload={
                "approvedUnderstanding": understanding.model_dump(by_alias=True),
                "constraints": constraints.model_dump(by_alias=True),
                "context": context.model_dump(by_alias=True),
            },
            contract_type=PlanBlueprint,
            temperature=0.2,
        )
        plan = result.artifact.model_copy(
            update={
                "version": 1,
                "goal_summary": understanding.goal_summary,
                "understanding_ref": understanding.artifact_id,
                "constraint_ref": constraints.artifact_id,
                "context_ref": context.artifact_id,
            }
        )
        return AgentResult(plan, result.model_usage)


class PlanRepairAgent:
    name = "Plan Generator"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        plan: PlanBlueprint,
        issue: QualityIssue,
        constraints: ConstraintSet,
        context: ContextPack,
    ) -> AgentResult[RepairProposal]:
        result = self.model.complete_contract(
            stage="repair_plan",
            task_type="planning_plan",
            feature="planning_plan_repair",
            system=REPAIR_SYSTEM,
            payload={
                "currentPlan": plan.model_dump(by_alias=True),
                "currentIssue": issue.model_dump(by_alias=True),
                "allowedOperations": issue.allowed_operations,
                "relevantConstraints": constraints.model_dump(by_alias=True),
                "relevantContext": context.model_dump(by_alias=True),
            },
            contract_type=RepairProposal,
            temperature=0.1,
        )
        proposal = result.artifact.model_copy(
            update={
                "artifact_id": plan.artifact_id,
                "artifact_version": plan.version,
                "issue_id": issue.issue_id,
                "operations": result.artifact.operations[:4],
            }
        )
        return AgentResult(proposal, result.model_usage)


__all__ = ["PlanGenerator", "PlanRepairAgent"]
