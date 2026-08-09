from __future__ import annotations

import re

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
resources. sourceGoalRefs must use current successSignals ids (never fact ids), and sourceConstraintRefs must use
current constraint ids. resourceRefs may contain only ids listed in validResourceRefs; when validResourceRefs is empty,
every task must use resourceRefs: [] even if its action text mentions official documentation or another free resource.
Every approved successSignal id must appear in sourceGoalRefs on at least one concrete task, including an ultimate or
umbrella success signal in addition to its more specific signals.
Prefer a concise executable DAG over generic phases. Return JSON only and do not reveal hidden
reasoning. Assumptions must be conservative and internally consistent with every task, deliverable, action step, and
completion check. A confirmed non-blocking unknown may be resolved by such an assumption; do not add a clarification
task unless the approved Understanding marks it blocking. Exact dates, week placement, and buffers belong to the later
Schedule stage rather than PlanBlueprint.
When the project type is a non-blocking unknown, choose one small concrete default project that fits the approved goal,
record that choice in assumptions, and make every implementation/demo task specific to it; never leave "choose a
project/interface" as an execution task.
Keep each task concrete and incrementally checkable. Decompose monolithic implementation or study work instead of
creating broad tasks such as "learn best practices" or "implement core features", and tailor learning tasks to the
confirmed current skill level.
Mark every task required to the approved success signal as required; use important/optional only for work that can be
skipped without weakening that signal. Define "demonstrable" outcomes with a runnable user flow and checkable demo
evidence. Knowing language basics does not imply that the user already knows OOP, testing, or engineering practices.
When the approved goal names testing across a multi-layer deliverable, include verification for every relevant layer
and their integration. Every fallback must address the actual stated risk; an external model/embedding dependency
needs a concrete local, cached, manual, or reduced-scope path rather than an unrelated storage substitute. Keep work
that must be ready before a timed activity independent of unrelated downstream tasks whenever the goal permits.
""".strip()

REPAIR_SYSTEM = """
You are Planix Plan Repair Agent. Return a RepairProposal for exactly one current QualityIssue and the current
PlanBlueprint. Use only the issue's allowedOperations, target the current artifact id/version, preserve immutable
goal and constraint semantics, keep stable task ids whenever possible, and emit at most four operations. Do not
regenerate the whole plan. Return JSON only.
For update_effort, target a task id and provide the complete effort estimate payload with minMinutes,
expectedMinutes, maxMinutes, confidence, and estimationBasis. For a capacity issue, use the numeric workload and
usable horizon capacity in the issue description and reduce enough total expected effort to remove that exact issue.
When repairBudget is present, minimumReductionMinutes is a hard lower bound: verify the arithmetic across all
update_effort operations and leave total expected effort at or below maximumExpectedMinutes.
For split_task, payload.tasks must contain at least two complete PlanTask objects with every required field
(including milestoneId and whyNow); the first part must preserve the original task id and all parts together must
preserve the original expected effort.
For update_task, never include id, milestoneId, sourceConstraintRefs, or other immutable identity/lineage fields in
the payload; include only the mutable fields that must change for the current issue.
For remove_dependency, dependencyId must be one of the target task's current dependencies. For add_dependency, it must
be an existing current task id. Never invent dependency targets.
For add_success_coverage, targetId must be one existing task id and payload must be
{"sourceGoalRefs": ["the-missing-success-signal-id"]}; never target the success signal itself or use taskIds.
For add_constraint_coverage, targetId must be one existing task id and payload must be
{"sourceConstraintRefs": ["the-missing-constraint-id"]}; never target the constraint itself.
""".strip()


def _repair_budget(plan: PlanBlueprint, issue: QualityIssue) -> dict[str, int] | None:
    if issue.rule_id != "capacity_order":
        return None
    values = [int(value) for value in re.findall(r"\d+", issue.description)]
    if len(values) < 2:
        return None
    current = sum(task.effort_estimate.expected_minutes for task in plan.tasks)
    maximum = values[-1]
    return {
        "currentExpectedMinutes": current,
        "maximumExpectedMinutes": maximum,
        "minimumReductionMinutes": max(0, current - maximum),
    }


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
                "validResourceRefs": [
                    claim.id for claim in context.claims if claim.verification_status == "verified"
                ],
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
                "repairBudget": _repair_budget(plan, issue),
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
