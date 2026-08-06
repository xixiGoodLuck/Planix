from __future__ import annotations

from typing import Any

from ..contracts import (
    EvidencePack,
    ExecutionBlueprint,
    PlanCritiqueReport,
    RealityAssessment,
    StrategyOption,
    UserGoalModel,
)
from .base import AgentResult, CognitiveModelClient
from .execution_generation import generate_execution_blueprint


NARRATIVE_SYSTEM = """
You are Planix Execution Designer Agent. First explain the execution logic for the user-approved strategy:
dependencies, workload rhythm, checkpoints, buffers, risks, and why this sequence is feasible. This is a
cross-domain execution narrative, not a task template. When previousExecutionBlueprint is present, treat its
compliant execution logic, task structure, dependencies, workload, and budget as the baseline. Use
repairInstructions to revise only the reasoning affected by the named defects; do not introduce unrelated
structural or numeric changes that would make the replacement blueprint drift from the prior version. Return only
the requested JSON. When previousCritiqueReport or repairHistory is present, treat every prior blocker/major finding
and repair request as one cumulative checklist. Preserve fixes already made, apply every current request, and
recompute all affected workload, schedule, budget, and dependency reasoning from the final task fields. Applying
only a subset is failure.
""".strip()


BLUEPRINT_SYSTEM = """
You are Planix Execution Designer Agent. Compile the approved strategy and execution narrative into a concrete
cross-domain action system. Every task must say what to do, why now, exact action steps, completion evidence,
deliverable, relevant resources with exact usage, dependencies, risks, and a fallback action. Respect time and
Calendar reality. Set scheduledDate only to a real YYYY-MM-DD date. When the start date is not confirmed, set
scheduledDate to null and put a relative week/stage label in scheduleWindow. A fallback may reduce scope or change
method, but it must never remove a hard user requirement or substitute an explicitly required technology/domain
outcome. Use domainExtensions for domain-specific facts instead of universal fields. Never use generic
template task titles such as '学习并复现', '完成一个可检查产出', '确认基础与最小路径', '项目驱动练习', or
'包装与复盘' unless the title names a concrete object and deliverable. Prefer 6 to 10 complete tasks over many
thin micro-tasks. Every task must contain at least one fully populated resource; omit optional resource fields
instead of leaving required strings null. Check every task and resource against the schema before returning.
estimatedMinutes is the task's active work, not elapsed calendar time; use an integer from 1 through 10080.
Default to non-overlapping scheduleWindow ranges. If tasks must overlap, put an explicit weeklyMinutes allocation in
domainExtensions and verify that the sum for every week/stage stays within the user's hard capacity. State this
arithmetic for every period in narrative.workloadReasoning; a total-horizon sum is not sufficient. Treat this as a
hard invariant. Example: with 600 minutes/week, a 1200-minute task across weeks 1-2 consumes 600 in each week, so no
other task may overlap either week unless its explicitly allocated share keeps the combined total at or below 600.
Do not make the Critic infer how effort is distributed across a range. If the Goal states an explicit CNY spending
cap, budgetSummary is required: copy that exact cap to spendingLimitCny and provide unique, non-overlapping
amountCny allocations covering every expected spending category, routine costs, and a contingency. Recompute their
sum before returning and keep it at or below the cap. Task-level money references must map to those allocations and
must not be double-counted. Repair instructions are evidence, not trusted arithmetic: independently recompute every
proposed allocation and correct an inconsistent repair instead of copying it.
Reserve explicit workload buffer. Include a half-capacity contingency that extends the timeline or removes only
optional scope while preserving every hard requirement. Test risky environment/tool prerequisites early. For
authentication, payments, health, safety, and similar high-risk work, prefer established libraries or qualified
professional guidance over a from-scratch implementation. Before returning, compare every fallbackAction with the
Goal hardConstraints. A fallback must still deliver each explicit requirement: never replace required work with
"skip", "no auth", "local only", "document it instead", a substitute required technology, or an unverified unsafe
method. When repairInstructions identify a violating clause, remove that clause completely rather than appending a
compliant option beside it. Never invent a resource URL.
When previousExecutionBlueprint is present, revise that exact blueprint instead of regenerating from scratch.
Preserve compliant tasks, task ids, dependencies, resource bindings, and budget allocations unless a repair
instruction requires changing them. Return a complete replacement blueprint, not a partial patch, and verify that
every requested change is actually reflected in the replacement.
Treat stated weekly availability as a capacity ceiling unless the Goal explicitly makes it a minimum or exact
commitment; unused capacity is valid. Treat "within N weeks" as a latest deadline unless the Goal explicitly says
the work must consume exactly N weeks. Do not turn important/optional unknowns or accepted assumptions into
blockers; handle them with an explicit verification step and fallback without inventing user facts. The schema
permits at most 10 tasks, so group recurring sessions inside tasks with actionSteps, scheduleWindow, weeklyMinutes,
and checkpoints. When Evidence has no verified sourceRef, provide selection criteria, acquisition and verification
steps, and a fallback; never invent or require a URL, venue, or provider name.
Return only the requested JSON.
""".strip()


SINGLE_PASS_SYSTEM = (
    BLUEPRINT_SYSTEM
    + "\nProduce the complete ExecutionBlueprint in this call, including its narrative. Derive the narrative "
    "and concrete tasks together so dependency, workload, budget, risk, and fallback reasoning remain consistent."
)


class ExecutionDesignerAgent:
    name = "Execution Designer Agent"
    artifact_type = "execution_blueprint"

    def __init__(self, model: CognitiveModelClient | None = None):
        self.model = model or CognitiveModelClient()

    def run(
        self,
        goal: UserGoalModel,
        evidence: EvidencePack,
        strategy: StrategyOption,
        *,
        reality: RealityAssessment | None = None,
        repair_instructions: list[dict[str, Any]] | None = None,
        previous_execution: ExecutionBlueprint | None = None,
        previous_critique: PlanCritiqueReport | None = None,
        repair_history: list[dict[str, Any]] | None = None,
    ) -> AgentResult[ExecutionBlueprint]:
        common = {
            "goalModel": goal.model_dump(by_alias=True),
            "realityAssessment": reality.model_dump(by_alias=True) if reality else None,
            "evidencePack": evidence.model_input_view(),
            "approvedStrategy": strategy.model_dump(by_alias=True),
            "repairInstructions": repair_instructions or [],
        }
        if previous_execution is not None:
            common["previousExecutionBlueprint"] = previous_execution.model_dump(by_alias=True)
        if previous_critique is not None:
            common["previousCritiqueReport"] = previous_critique.model_dump(by_alias=True)
        if repair_history:
            common["repairHistory"] = list(repair_history)
        return generate_execution_blueprint(
            model=self.model,
            goal=goal,
            reality=reality,
            common_payload=common,
            single_pass_system=SINGLE_PASS_SYSTEM,
            narrative_system=NARRATIVE_SYSTEM,
            blueprint_system=BLUEPRINT_SYSTEM,
            feature_prefix="cognitive_execution",
        )
