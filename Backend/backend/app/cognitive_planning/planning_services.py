from __future__ import annotations

import copy
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from ..services.cognitive_planning.contracts import (
    EvidencePack,
    ExecutionBlueprint,
    PlanCritiqueReport,
    RealityAssessment,
    StrategyPortfolio,
    UserGoalModel,
)
from .contracts.planning import (
    CalendarEventProposal,
    CalendarProposal,
    CapacitySummary,
    ConstraintSet,
    ContextClaim,
    ContextPack,
    CoreConstraints,
    EffortEstimate,
    FeedbackRoute,
    FinalApprovalBundle,
    PlanBlueprint,
    PlanMilestone,
    PlanTask,
    QualityIssue,
    QualityReport,
    ExecutionOutcome,
    LearningObservation,
    MemoryCandidateDraft,
    PromotionAudit,
    ReplanProposal,
    RepairOperation,
    RepairProposal,
    RepairResult,
    ScheduleBlueprint,
    ScheduleSession,
    SemanticConstraint,
    SemanticItem,
    SemanticOperation,
    UnderstandingContext,
    UnderstandingPatch,
    UnderstandingQuestion,
    UnderstandingReadiness,
    UnderstandingSnapshot,
    UserAdaptation,
    new_artifact_id,
)


QUESTION_BUDGET = {"quick": 1, "standard": 2, "complex": 4}
EXTERNAL_FACT_PATTERNS = (
    re.compile(r"https?://", re.I),
    re.compile(r"(?:price|policy|version|deadline|价格|政策|版本|截止日期)", re.I),
)


def _item(
    *,
    key: str,
    statement: str,
    source_ref: str,
    source_type: str = "user_confirmed",
    confidence: float = 1,
    mutation_policy: str = "user_confirmation_required",
) -> SemanticItem:
    return SemanticItem(
        id=f"semantic-{key}",
        key=key,
        statement=statement,
        sourceType=source_type,
        sourceRef=source_ref,
        confidence=confidence,
        mutationPolicy=mutation_policy,
    )


class UnderstandingAdapter:
    """Build the formal understanding snapshot from the model-owned goal artifact."""

    @staticmethod
    def from_goal_model(
        goal: UserGoalModel,
        *,
        previous: UnderstandingSnapshot | None = None,
        source_ref: str = "current-user-turn",
        question_rounds_used: int = 0,
        confirmed: bool = False,
    ) -> UnderstandingSnapshot:
        version = (previous.version + 1) if previous else 1
        facts = [
            _item(
                key=fact.key,
                statement=fact.statement,
                source_ref=fact.source_text or source_ref,
                confidence=fact.confidence,
            )
            for fact in goal.known_facts
        ]
        constraints = [
            _item(
                key=f"constraint:{index}",
                statement=value.statement,
                source_ref=value.source_text or source_ref,
                mutation_policy="immutable",
            )
            for index, value in enumerate(goal.hard_constraints, start=1)
        ]
        preferences = [
            _item(
                key=f"preference:{index}",
                statement=value.statement,
                source_ref=value.source_text or source_ref,
                confidence=value.confidence,
                mutation_policy="auto_adjust",
            )
            for index, value in enumerate(goal.soft_preferences, start=1)
        ]
        success_signals = [
            _item(
                key=f"success:{index}",
                statement=value,
                source_ref=source_ref,
                mutation_policy="immutable" if confirmed else "user_confirmation_required",
            )
            for index, value in enumerate(goal.success_model.measurable_signals, start=1)
        ]
        assumptions = [
            _item(
                key=f"assumption:{index}",
                statement=value.statement,
                source_ref=f"{source_ref}:assumption",
                source_type="model_assumption",
                confidence=value.confidence,
                mutation_policy="user_confirmation_required",
            )
            for index, value in enumerate(goal.assumptions, start=1)
        ]
        unknowns = [
            _item(
                key=value.key,
                statement=value.description,
                source_ref=f"{source_ref}:unknown",
                source_type="model_assumption",
                confidence=0.5,
            )
            for value in goal.decision_relevant_unknowns
        ]
        conflicts = [
            _item(
                key=f"conflict:{index}",
                statement=value,
                source_ref=source_ref,
                confidence=1,
                mutation_policy="immutable",
            )
            for index, value in enumerate(goal.consistency_warnings, start=1)
        ]
        question = goal.questions[0] if goal.questions else None
        next_question = (
            UnderstandingQuestion(
                question=question.question,
                whyThisQuestionMatters=question.why_this_question_matters,
                expectedDecisionImpact=question.expected_decision_impact,
                priority=(
                    next(
                        (
                            item.priority
                            for item in goal.decision_relevant_unknowns
                            if item.key == (unknowns[0].key if unknowns else "")
                        ),
                        "important",
                    )
                ),
                answerOptions=question.answer_options,
            )
            if question
            else None
        )
        complexity = UnderstandingReadinessService.classify_complexity(goal)
        return UnderstandingSnapshot(
            version=version,
            goalSummary=goal.goal_statement,
            facts=facts,
            constraints=constraints,
            preferences=preferences,
            successSignals=success_signals,
            assumptions=assumptions,
            unknowns=unknowns,
            conflicts=conflicts,
            nextQuestion=next_question,
            readiness=UnderstandingReadiness(
                confirmed=confirmed,
                questionRoundsUsed=question_rounds_used,
                questionBudget=QUESTION_BUDGET[complexity],
                complexity=complexity,
            ),
            sourceRefs=sorted(
                {
                    item.source_ref
                    for section in (facts, constraints, preferences, success_signals)
                    for item in section
                }
            ),
        )


class SemanticMergeService:
    """Apply stable-key changes without leaking superseded values downstream."""

    SECTIONS = {
        "facts",
        "constraints",
        "preferences",
        "success_signals",
        "assumptions",
        "unknowns",
        "conflicts",
    }

    def apply(
        self,
        current: UnderstandingSnapshot,
        patch: UnderstandingPatch,
    ) -> tuple[UnderstandingSnapshot, UnderstandingPatch]:
        if patch.base_artifact_id != current.artifact_id or patch.base_version != current.version:
            raise ValueError("understanding patch is stale")
        update = current.model_dump()
        archive_operations: list[SemanticOperation] = []
        for operation in patch.operations:
            if operation.operation == "replace_goal_summary":
                if not (operation.value or "").strip():
                    raise ValueError("replacement goal summary cannot be blank")
                update["goal_summary"] = operation.value.strip()
                archive_operations.append(operation)
                continue
            section = operation.section
            if section not in self.SECTIONS:
                raise ValueError("semantic operation requires a valid section")
            values = list(update[section])
            index = next(
                (position for position, item in enumerate(values) if item["key"] == operation.key),
                None,
            )
            if operation.operation in {"add_item", "replace_success_signal"}:
                if operation.item is None:
                    raise ValueError("semantic add requires an item")
                if index is not None:
                    raise ValueError(f"semantic key already exists: {operation.item.key}")
                values.append(operation.item.model_dump())
            elif operation.operation == "replace_item":
                if index is None or operation.item is None:
                    raise ValueError("semantic replacement target is missing")
                old = values[index]
                replacement = operation.item.model_copy(update={"supersedes": old["id"]})
                values[index] = replacement.model_dump()
            elif operation.operation in {"remove_item", "reject_assumption"}:
                if index is None:
                    raise ValueError("semantic removal target is missing")
                values.pop(index)
            elif operation.operation == "confirm_assumption":
                if section != "assumptions" or index is None:
                    raise ValueError("assumption confirmation target is missing")
                values[index] = {
                    **values[index],
                    "source_type": "user_confirmed",
                    "confidence": 1,
                    "mutation_policy": "immutable",
                }
            else:
                raise ValueError(f"unsupported semantic operation: {operation.operation}")
            update[section] = values
            archive_operations.append(operation)

        update.update(
            {
                "artifact_id": new_artifact_id("understanding"),
                "version": current.version + 1,
                "created_at": current.created_at,
                "readiness": {
                    **current.readiness.model_dump(),
                    "confirmed": False,
                },
            }
        )
        return UnderstandingSnapshot.model_validate(update), patch.model_copy(
            update={"operations": archive_operations}
        )


class UnderstandingReadinessService:
    @staticmethod
    def classify_complexity(goal: UserGoalModel) -> str:
        count = len(goal.hard_constraints) + len(goal.known_facts) + len(goal.decision_relevant_unknowns)
        if any(item.impact in {"safety", "feasibility"} for item in goal.decision_relevant_unknowns):
            return "complex"
        if count <= 2:
            return "quick"
        return "complex" if count >= 8 else "standard"

    def assess(
        self,
        snapshot: UnderstandingSnapshot,
        *,
        blocking_unknown_keys: Iterable[str] = (),
    ) -> UnderstandingSnapshot:
        blocking_keys = set(blocking_unknown_keys)
        reasons: list[str] = []
        if not snapshot.goal_summary.strip():
            reasons.append("core goal is missing")
        if not snapshot.success_signals:
            reasons.append("success criteria are not verifiable")
        if snapshot.conflicts:
            reasons.append("understanding contains unresolved conflicts")
        blocking = [item for item in snapshot.unknowns if item.key in blocking_keys]
        if blocking:
            reasons.extend(f"blocking unknown: {item.key}" for item in blocking)
        budget_exhausted = (
            snapshot.readiness.question_rounds_used >= snapshot.readiness.question_budget
        )
        safety_blocked = any(
            token in item.key.casefold() or token in item.statement.casefold()
            for item in blocking
            for token in ("safety", "feasibility", "安全", "可行")
        )
        ready = not reasons
        if budget_exhausted and blocking and not safety_blocked:
            ready = not snapshot.conflicts and bool(snapshot.goal_summary and snapshot.success_signals)
            reasons = [reason for reason in reasons if not reason.startswith("blocking unknown:")]
        readiness = snapshot.readiness.model_copy(
            update={
                "ready_for_confirmation": ready,
                "blocking_reasons": reasons,
            }
        )
        assumptions = list(snapshot.assumptions)
        unknowns = list(snapshot.unknowns)
        if budget_exhausted and ready:
            existing = {item.key for item in assumptions}
            for item in blocking:
                if item.key not in existing:
                    assumptions.append(
                        item.model_copy(
                            update={
                                "id": f"assumed-{item.id}",
                                "source_type": "model_assumption",
                                "mutation_policy": "user_confirmation_required",
                            }
                        )
                    )
            unknowns = [item for item in unknowns if item.key not in blocking_keys]
        return snapshot.model_copy(
            update={"readiness": readiness, "assumptions": assumptions, "unknowns": unknowns}
        )


class UnderstandingContextCompactor:
    def compact(
        self,
        snapshot: UnderstandingSnapshot,
        *,
        latest_user_message: str,
        recent_messages: Sequence[str] = (),
        memory_top_k: Sequence[SemanticItem] = (),
        tool_results: Sequence[dict] = (),
    ) -> UnderstandingContext:
        return UnderstandingContext(
            currentSnapshot=snapshot,
            latestUserMessage=latest_user_message,
            recentMessages=list(recent_messages[-4:]),
            unresolvedUnknowns=[item for item in snapshot.unknowns if item.status == "active"],
            memoryTopK=list(memory_top_k),
            toolResults=list(tool_results),
        )


class ConstraintCompiler:
    _minutes = re.compile(r"(?P<value>\d+)\s*(?:分钟|minutes?|mins?)", re.I)
    _hours = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:小时|hours?|hrs?)", re.I)
    _budget = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:元|CNY|RMB)", re.I)

    def compile(self, snapshot: UnderstandingSnapshot) -> ConstraintSet:
        core = CoreConstraints()
        semantic: list[SemanticConstraint] = []
        for item in snapshot.constraints:
            statement = item.statement
            hours = self._hours.search(statement)
            minutes = self._minutes.search(statement)
            budget = self._budget.search(statement)
            lower = statement.casefold()
            lower = (
                lower.replace("weekdays", "days")
                .replace("weekday", "day")
                .replace("weekends", "days")
                .replace("weekend", "day")
            )
            if hours and any(token in lower for token in ("每周", "week")):
                weekly = int(float(hours.group("value")) * 60)
                core.weekday_capacity_minutes = weekly
            elif minutes and any(token in lower for token in ("每周", "week")):
                core.weekday_capacity_minutes = int(minutes.group("value"))
            elif budget:
                core.budget_limit = float(budget.group("value"))
            else:
                semantic.append(
                    SemanticConstraint(
                        stableId=item.id,
                        statement=statement,
                        sourceRef=item.source_ref,
                        constraintType="semantic",
                        mutationPolicy=item.mutation_policy,
                        priority="blocking" if item.mutation_policy == "immutable" else "important",
                    )
                )
        core.required_deliverables = [item.statement for item in snapshot.success_signals]
        return ConstraintSet(
            understandingRef=snapshot.artifact_id,
            understandingVersion=snapshot.version,
            core=core,
            semantic=semantic,
        )


class ContextBuilder:
    def build(
        self,
        snapshot: UnderstandingSnapshot,
        constraints: ConstraintSet,
        *,
        reality: RealityAssessment | None = None,
        evidence: EvidencePack | None = None,
        tool_run_refs: Sequence[str] = (),
        calendar_snapshot_ref: str | None = None,
    ) -> ContextPack:
        claims: list[ContextClaim] = []
        if reality:
            claims.append(
                ContextClaim(
                    id="reality-summary",
                    claim=reality.feasibility_summary,
                    sourceType="model_assumption",
                    sourceRef="reality-assessment",
                    verificationStatus="inference",
                    credibility=reality.confidence,
                )
            )
        if evidence:
            sources = [*evidence.domain_evidence, *evidence.resource_candidates]
            for index, source in enumerate(sources):
                source_ref = str(source.source_ref or "").strip()
                claim = getattr(source, "claim", None) or getattr(source, "title", "")
                claims.append(
                    ContextClaim(
                        id=f"evidence:{index}",
                        claim=claim,
                        sourceType="tool_verified" if source_ref else "model_assumption",
                        sourceRef=source_ref or f"evidence-inference:{index}",
                        verificationStatus="verified" if source_ref else "inference",
                        credibility=source.credibility,
                    )
                )
        return ContextPack(
            understandingRef=snapshot.artifact_id,
            constraintRef=constraints.artifact_id,
            claims=claims,
            toolRunRefs=list(tool_run_refs),
            calendarSnapshotRef=calendar_snapshot_ref,
        )


class PlanCompatibilityAdapter:
    def from_artifacts(
        self,
        *,
        snapshot: UnderstandingSnapshot,
        constraints: ConstraintSet,
        context: ContextPack,
        strategy: StrategyPortfolio,
        execution: ExecutionBlueprint,
    ) -> PlanBlueprint:
        strategy_option = next(
            item for item in strategy.strategies if item.id == strategy.recommended_strategy_id
        )
        verified_resource_refs = {
            claim.source_ref for claim in context.claims if claim.verification_status == "verified"
        }
        milestone_id = f"milestone-{strategy_option.id}"
        milestones = [
            PlanMilestone(
                id=milestone_id,
                title=strategy_option.name,
                purpose=strategy_option.rationale.why_it_fits_user,
                successSignalRefs=[item.id for item in snapshot.success_signals],
            )
        ]
        tasks: list[PlanTask] = []
        for raw in execution.tasks:
            expected = raw.estimated_minutes
            tasks.append(
                PlanTask(
                    id=raw.id,
                    milestoneId=milestone_id,
                    title=raw.title,
                    purpose=raw.purpose,
                    whyNow=raw.why_now,
                    actionSteps=raw.action_steps,
                    dependencies=raw.dependencies,
                    effortEstimate=EffortEstimate(
                        minMinutes=max(1, int(expected * 0.75)),
                        expectedMinutes=expected,
                        maxMinutes=max(expected, int(expected * 1.35)),
                        confidence=0.65,
                        estimationBasis="Execution Agent estimate converted to an explicit range.",
                    ),
                    priority="high" if raw.difficulty == "high" else "low" if raw.difficulty == "low" else "medium",
                    optionality=raw.optionality,
                    deliverable=raw.deliverable,
                    completionEvidence=raw.completion_evidence,
                    resourceRefs=[
                        value.source_ref
                        for value in raw.resources
                        if value.source_ref in verified_resource_refs
                    ],
                    risks=raw.risks,
                    fallback=raw.fallback_action,
                    sourceGoalRefs=[item.id for item in snapshot.success_signals],
                    sourceConstraintRefs=[item.stable_id for item in constraints.semantic],
                )
            )
        return PlanBlueprint(
            goalSummary=snapshot.goal_summary,
            understandingRef=snapshot.artifact_id,
            constraintRef=constraints.artifact_id,
            contextRef=context.artifact_id,
            milestones=milestones,
            tasks=tasks,
            assumptions=[item.statement for item in snapshot.assumptions],
        )


class PlanHardValidator:
    def validate(
        self,
        plan: PlanBlueprint,
        *,
        snapshot: UnderstandingSnapshot,
        constraints: ConstraintSet,
        context: ContextPack,
        repair_round: int = 0,
    ) -> QualityReport:
        issues: list[QualityIssue] = []

        def issue(rule: str, target_type: str, target_id: str, description: str, operations: list[str]) -> None:
            issues.append(
                QualityIssue(
                    issueId=f"{rule}:{target_id}",
                    category="content" if rule != "provenance" else "evidence",
                    severity="major",
                    ruleId=rule,
                    targetType=target_type,
                    targetId=target_id,
                    description=description,
                    evidenceRefs=[],
                    allowedOperations=operations,
                    repairBasis="deterministic_validator",
                )
            )

        if plan.goal_summary.strip() != snapshot.goal_summary.strip():
            issue("goal_fidelity", "plan", plan.artifact_id, "plan changed the approved goal", [])
        task_ids = [task.id for task in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            issue("unique_task_id", "plan", plan.artifact_id, "task ids must be unique", [])
        normalized_titles = [" ".join(task.title.casefold().split()) for task in plan.tasks]
        if len(normalized_titles) != len(set(normalized_titles)):
            issue("duplicate_task", "plan", plan.artifact_id, "plan contains duplicate task titles", ["update_task", "remove_optional_task"])
        known = set(task_ids)
        verified_refs = {
            claim.source_ref
            for claim in context.claims
            if claim.verification_status == "verified"
        }
        for task in plan.tasks:
            if not task.action_steps:
                issue("action_steps", "task", task.id, "task has no action steps", ["update_task"])
            if not task.deliverable.strip():
                issue("deliverable", "task", task.id, "task has no deliverable", ["update_task"])
            if not task.completion_evidence:
                issue("completion_evidence", "task", task.id, "task has no completion evidence", ["update_task"])
            missing = [item for item in task.dependencies if item not in known]
            if missing:
                issue("dependency_exists", "task", task.id, f"unknown dependencies: {missing}", ["remove_dependency", "add_task"])
            if not task.source_goal_refs:
                issue("goal_support", "task", task.id, "task is not traceable to the approved goal", ["add_success_coverage", "remove_optional_task"])
            unknown_resources = set(task.resource_refs) - verified_refs
            if unknown_resources:
                issue("provenance", "task", task.id, f"resource refs are not verified: {sorted(unknown_resources)}", ["replace_resource"])
            elif any(pattern.search(task.title + " " + task.purpose) for pattern in EXTERNAL_FACT_PATTERNS):
                if not set(task.resource_refs) & verified_refs:
                    issue("provenance", "task", task.id, "external claim has no verified source", ["replace_resource", "update_task"])
            if task.optionality == "required":
                if not task.risks:
                    issue("risk", "task", task.id, "required task has no explicit risk", ["update_task"])
                if not task.fallback.strip():
                    issue("fallback", "task", task.id, "required task has no fallback", ["update_task"])
        if self._has_cycle(plan.tasks):
            issue("dependency_dag", "plan", plan.artifact_id, "dependency graph contains a cycle", ["remove_dependency"])
        covered = {ref for task in plan.tasks for ref in task.source_goal_refs}
        for signal in snapshot.success_signals:
            if signal.id not in covered:
                issue("success_coverage", "success_signal", signal.id, "success signal is not covered", ["add_success_coverage", "add_task"])
        immutable = {
            item.stable_id
            for item in constraints.semantic
            if item.mutation_policy == "immutable"
        }
        constraint_coverage = {ref for task in plan.tasks for ref in task.source_constraint_refs}
        for constraint_id in immutable - constraint_coverage:
            issue("immutable_constraint", "constraint", constraint_id, "immutable constraint is not preserved by any task", ["update_task", "add_task"])
        capacity = constraints.core.weekday_capacity_minutes
        if capacity:
            expected = sum(task.effort_estimate.expected_minutes for task in plan.tasks)
            if expected > capacity * 12:
                issue("capacity_order", "plan", plan.artifact_id, "workload is outside the configured capacity order", ["update_effort", "remove_optional_task"])
        return QualityReport(
            targetArtifactId=plan.artifact_id,
            targetVersion=plan.version,
            hardRulesPassed=not issues,
            issues=issues,
            repairRound=repair_round,
        )

    @staticmethod
    def _has_cycle(tasks: Sequence[PlanTask]) -> bool:
        graph = {task.id: list(task.dependencies) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(dependency in graph and visit(dependency) for dependency in graph[node]):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)


class PatchGuard:
    PLAN_OPERATIONS = {
        "add_task",
        "update_task",
        "remove_optional_task",
        "split_task",
        "move_task",
        "add_dependency",
        "remove_dependency",
        "replace_resource",
        "update_effort",
        "add_success_coverage",
    }

    def apply_plan(
        self,
        plan: PlanBlueprint,
        proposal: RepairProposal,
        issue: QualityIssue,
        *,
        validator: PlanHardValidator,
        snapshot: UnderstandingSnapshot,
        constraints: ConstraintSet,
        context: ContextPack,
    ) -> tuple[PlanBlueprint, RepairResult]:
        if proposal.artifact_id != plan.artifact_id or proposal.artifact_version != plan.version:
            raise ValueError("repair proposal is stale")
        if proposal.issue_id != issue.issue_id:
            raise ValueError("repair proposal is not bound to the issue")
        if len(proposal.operations) > 4:
            raise ValueError("repair proposal exceeds the issue-scoped operation budget")
        allowed = set(issue.allowed_operations)
        candidate = copy.deepcopy(plan.model_dump())
        tasks = candidate["tasks"]
        for operation in proposal.operations:
            if operation.operation not in self.PLAN_OPERATIONS or operation.operation not in allowed:
                raise ValueError("repair operation is not allowed for this issue")
            index = next((i for i, task in enumerate(tasks) if task["id"] == operation.target_id), None)
            if operation.operation == "add_task":
                task = PlanTask.model_validate(operation.payload)
                if any(value["id"] == task.id for value in tasks):
                    raise ValueError("repair cannot add a duplicate task id")
                tasks.append(task.model_dump())
                continue
            if operation.operation == "add_success_coverage":
                if index is None:
                    raise ValueError("repair target task does not exist")
                refs = set(tasks[index]["source_goal_refs"])
                refs.update(str(value) for value in operation.payload.get("sourceGoalRefs", []))
                tasks[index]["source_goal_refs"] = sorted(refs)
                continue
            if index is None:
                raise ValueError("repair target task does not exist")
            task = tasks[index]
            if operation.operation == "remove_optional_task":
                if task["optionality"] != "optional":
                    raise ValueError("repair cannot remove a required or important task")
                tasks.pop(index)
            elif operation.operation == "update_effort":
                task["effort_estimate"] = EffortEstimate.model_validate(operation.payload).model_dump()
            elif operation.operation == "remove_dependency":
                dependency = str(operation.payload.get("dependencyId") or "")
                task["dependencies"] = [value for value in task["dependencies"] if value != dependency]
            elif operation.operation == "add_dependency":
                dependency = str(operation.payload.get("dependencyId") or "")
                if dependency not in {value["id"] for value in tasks}:
                    raise ValueError("repair dependency target does not exist")
                task["dependencies"] = list(dict.fromkeys([*task["dependencies"], dependency]))
            elif operation.operation == "replace_resource":
                task["resource_refs"] = [str(value) for value in operation.payload.get("resourceRefs", [])]
            elif operation.operation == "update_task":
                forbidden = {"id", "milestoneId", "milestone_id", "sourceConstraintRefs", "source_constraint_refs"}
                if forbidden & set(operation.payload):
                    raise ValueError("repair attempted to modify immutable task identity or constraint lineage")
                task.update(operation.payload)
            else:
                raise ValueError("complex repair operation requires a dedicated deterministic handler")
        candidate.update(
            {
                "artifact_id": new_artifact_id("plan"),
                "version": plan.version + 1,
                "tasks": tasks,
            }
        )
        revised = PlanBlueprint.model_validate(candidate)
        report = validator.validate(
            revised,
            snapshot=snapshot,
            constraints=constraints,
            context=context,
            repair_round=min(2, issue.severity != "minor"),
        )
        same_issue = any(value.rule_id == issue.rule_id and value.target_id == issue.target_id for value in report.issues)
        if same_issue:
            return plan, RepairResult(accepted=False, reason="regression validation rejected the repair")
        return revised, RepairResult(
            accepted=True,
            reason="repair passed deterministic regression validation",
            newArtifactId=revised.artifact_id,
            newVersion=revised.version,
            invalidatedArtifacts=ArtifactInvalidator.downstream_of("plan"),
        )


class ArtifactInvalidator:
    ORDER = {
        "understanding": ["constraint", "context", "plan", "plan_quality", "schedule", "schedule_quality", "calendar"],
        "constraint": ["context", "plan", "plan_quality", "schedule", "schedule_quality", "calendar"],
        "plan": ["plan_quality", "schedule", "schedule_quality", "calendar"],
        "effort": ["schedule", "schedule_quality", "calendar"],
        "schedule": ["schedule_quality", "calendar"],
        "presentation": ["calendar"],
    }

    @classmethod
    def downstream_of(cls, changed: str) -> list[str]:
        return list(cls.ORDER.get(changed, []))


class RepairBudget:
    maximum_rounds = 2

    def assert_available(self, report: QualityReport) -> None:
        if report.repair_round >= self.maximum_rounds:
            raise ValueError("automatic repair budget exhausted; continue to final review")

    def next_round(self, report: QualityReport) -> int:
        self.assert_available(report)
        return report.repair_round + 1


class ScheduleGenerator:
    def generate(
        self,
        plan: PlanBlueprint,
        constraints: ConstraintSet,
        *,
        start: datetime,
        timezone: str = "Asia/Shanghai",
        calendar_snapshot_ref: str | None = None,
    ) -> ScheduleBlueprint:
        maximum = constraints.core.maximum_session_minutes or 120
        cursor = start
        sessions: list[ScheduleSession] = []
        unscheduled: list[str] = []
        daily_used: dict[str, int] = defaultdict(int)
        sequence = 0
        for task in self._topological(plan.tasks):
            remaining = task.effort_estimate.expected_minutes
            skipped_days = 0
            while remaining > 0:
                day_key = cursor.date().isoformat()
                day_limit = (
                    constraints.core.weekend_capacity_minutes
                    if cursor.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None
                    else constraints.core.weekday_capacity_minutes
                )
                excluded = (
                    day_key in constraints.core.excluded_dates
                    or cursor.weekday() in constraints.core.excluded_weekdays
                    or day_limit == 0
                )
                available = remaining if day_limit is None else max(0, day_limit - daily_used[day_key])
                if excluded or available <= 0:
                    cursor = (cursor + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    skipped_days += 1
                    if skipped_days > 370:
                        unscheduled.append(task.id)
                        break
                    continue
                duration = min(maximum, remaining, available)
                end = cursor + timedelta(minutes=duration)
                sessions.append(
                    ScheduleSession(
                        id=f"session-{task.id}-{sequence}",
                        taskId=task.id,
                        start=cursor.isoformat(),
                        end=end.isoformat(),
                        durationMinutes=duration,
                        sequence=sequence,
                        reason="Topological task order with bounded session length.",
                    )
                )
                daily_used[day_key] += duration
                cursor = end + timedelta(minutes=30)
                remaining -= duration
                sequence += 1
        scheduled = sum(value.duration_minutes for value in sessions)
        buffer = int(scheduled * constraints.core.minimum_buffer_ratio)
        return ScheduleBlueprint(
            planRef=plan.artifact_id,
            planVersion=plan.version,
            calendarSnapshotRef=calendar_snapshot_ref,
            planningTimezone=timezone,
            periods=sorted({value.start[:10] for value in sessions}),
            sessions=sessions,
            capacitySummary=CapacitySummary(
                availableMinutes=max(scheduled + buffer, constraints.core.weekday_capacity_minutes or 0),
                scheduledMinutes=scheduled,
                bufferMinutes=buffer,
            ),
            bufferSummary=f"Reserved {buffer} minutes ({constraints.core.minimum_buffer_ratio:.0%}).",
            unscheduledTaskIds=unscheduled,
        )

    @staticmethod
    def _topological(tasks: Sequence[PlanTask]) -> list[PlanTask]:
        by_id = {task.id: task for task in tasks}
        indegree = {task.id: 0 for task in tasks}
        children: dict[str, list[str]] = defaultdict(list)
        for task in tasks:
            for dependency in task.dependencies:
                if dependency in by_id:
                    indegree[task.id] += 1
                    children[dependency].append(task.id)
        queue = deque(task.id for task in tasks if indegree[task.id] == 0)
        result: list[PlanTask] = []
        while queue:
            current = queue.popleft()
            result.append(by_id[current])
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(result) != len(tasks):
            raise ValueError("cannot schedule a cyclic plan")
        return result


class ScheduleValidator:
    def validate(
        self,
        schedule: ScheduleBlueprint,
        *,
        plan: PlanBlueprint,
        constraints: ConstraintSet,
        current_calendar_snapshot_ref: str | None = None,
        calendar_busy: Sequence[tuple[datetime, datetime]] = (),
    ) -> QualityReport:
        issues: list[QualityIssue] = []
        task_ids = {task.id for task in plan.tasks}
        covered = {session.task_id for session in schedule.sessions}
        for task in plan.tasks:
            if task.optionality == "required" and task.id not in covered:
                issues.append(self._issue("required_task_scheduled", task.id, "required task is unscheduled", ["update_schedule_session"]))
        if any(session.task_id not in task_ids for session in schedule.sessions):
            issues.append(self._issue("task_identity", schedule.artifact_id, "schedule contains an unknown task", []))
        if current_calendar_snapshot_ref != schedule.calendar_snapshot_ref:
            issues.append(self._issue("calendar_snapshot", schedule.artifact_id, "calendar snapshot is stale", []))
        maximum = constraints.core.maximum_session_minutes or 120
        parsed: dict[str, tuple[datetime, datetime]] = {}
        for session in schedule.sessions:
            try:
                start = datetime.fromisoformat(session.start)
                end = datetime.fromisoformat(session.end)
            except ValueError:
                issues.append(self._issue("timezone", session.id, "session time is not valid ISO-8601", ["move_schedule_session"]))
                continue
            parsed[session.id] = (start, end)
            if start.tzinfo is None or end.tzinfo is None:
                issues.append(self._issue("timezone", session.id, "session time has no timezone offset", ["move_schedule_session"]))
            if end <= start or int((end - start).total_seconds() // 60) != session.duration_minutes:
                issues.append(self._issue("duration", session.id, "session duration does not match start/end", ["update_schedule_session"]))
            if session.duration_minutes > maximum:
                issues.append(self._issue("maximum_session", session.id, "session exceeds maximum duration", ["split_schedule_session"]))
            if start.date().isoformat() in constraints.core.excluded_dates or start.weekday() in constraints.core.excluded_weekdays:
                issues.append(self._issue("excluded_time", session.id, "session uses excluded time", ["move_schedule_session"]))
            if constraints.core.deadline and end.date().isoformat() > constraints.core.deadline:
                issues.append(self._issue("deadline", session.id, "session exceeds deadline", ["move_schedule_session", "split_schedule_session"]))
            if any(start < busy_end and end > busy_start for busy_start, busy_end in calendar_busy):
                issues.append(self._issue("calendar_conflict", session.id, "session overlaps an existing calendar event", ["move_schedule_session"]))
        ordered = sorted(parsed.items(), key=lambda item: item[1][0])
        for index, (session_id, (_, end)) in enumerate(ordered[:-1]):
            next_id, (next_start, _) = ordered[index + 1]
            if end > next_start:
                issues.append(self._issue("session_overlap", next_id, f"session overlaps {session_id}", ["move_schedule_session"]))
        task_windows: dict[str, tuple[datetime, datetime]] = {}
        for task_id in task_ids:
            windows = [parsed[session.id] for session in schedule.sessions if session.task_id == task_id and session.id in parsed]
            if windows:
                task_windows[task_id] = (min(value[0] for value in windows), max(value[1] for value in windows))
        for task in plan.tasks:
            if task.id not in task_windows:
                continue
            for dependency in task.dependencies:
                if dependency in task_windows and task_windows[task.id][0] < task_windows[dependency][1]:
                    issues.append(self._issue("dependency_order", task.id, f"task starts before dependency {dependency} completes", ["move_schedule_session"]))
        daily: dict[str, int] = defaultdict(int)
        for session in schedule.sessions:
            if session.id in parsed:
                daily[parsed[session.id][0].date().isoformat()] += session.duration_minutes
        daily_limit = constraints.core.weekday_capacity_minutes
        for day, minutes in daily.items():
            parsed_day = datetime.fromisoformat(day)
            limit = (
                constraints.core.weekend_capacity_minutes
                if parsed_day.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None
                else daily_limit
            )
            if limit is not None and minutes > limit:
                    issues.append(self._issue("daily_capacity", day, "scheduled minutes exceed configured capacity", ["move_schedule_session", "split_schedule_session"]))
        weekly: dict[tuple[int, int], int] = defaultdict(int)
        for day, minutes in daily.items():
            iso = datetime.fromisoformat(day).isocalendar()
            weekly[(iso.year, iso.week)] += minutes
        if daily_limit is not None or constraints.core.weekend_capacity_minutes is not None:
            weekend_limit = constraints.core.weekend_capacity_minutes
            if weekend_limit is None:
                weekend_limit = daily_limit or 0
            weekly_limit = (daily_limit or 0) * 5 + weekend_limit * 2
            for week, minutes in weekly.items():
                if minutes > weekly_limit:
                    issues.append(self._issue("weekly_capacity", f"{week[0]}-W{week[1]:02d}", "scheduled minutes exceed weekly capacity", ["move_schedule_session", "split_schedule_session"]))
        expected = sum(task.effort_estimate.expected_minutes for task in plan.tasks)
        scheduled = sum(session.duration_minutes for session in schedule.sessions)
        if expected != scheduled:
            issues.append(self._issue("effort_preserved", schedule.artifact_id, "schedule changed total task effort", ["update_schedule_session"]))
        required_buffer = int(scheduled * constraints.core.minimum_buffer_ratio)
        if schedule.capacity_summary.buffer_minutes < required_buffer:
            issues.append(self._issue("minimum_buffer", schedule.artifact_id, "schedule does not reserve the minimum buffer", ["move_schedule_session"]))
        return QualityReport(
            targetArtifactId=schedule.artifact_id,
            targetVersion=schedule.version,
            hardRulesPassed=not issues,
            semanticReviewRequired=False,
            issues=issues,
        )

    @staticmethod
    def _issue(rule: str, target: str, description: str, operations: list[str]) -> QualityIssue:
        return QualityIssue(
            issueId=f"schedule:{rule}:{target}",
            category="schedule",
            severity="major",
            ruleId=rule,
            targetType="schedule",
            targetId=target,
            description=description,
            evidenceRefs=[],
            allowedOperations=operations,
            repairBasis="deterministic_schedule_validator",
        )


class CalendarMaterializer:
    def materialize(
        self,
        plan: PlanBlueprint,
        schedule: ScheduleBlueprint,
        *,
        timezone: str,
        current_calendar_snapshot_ref: str | None,
    ) -> CalendarProposal:
        if schedule.plan_ref != plan.artifact_id or schedule.plan_version != plan.version:
            raise ValueError("schedule is not bound to the current plan")
        if schedule.calendar_snapshot_ref != current_calendar_snapshot_ref:
            raise ValueError("calendar snapshot is stale")
        tasks = {task.id: task for task in plan.tasks}
        events: list[CalendarEventProposal] = []
        seen: set[str] = set()
        for session in schedule.sessions:
            task = tasks.get(session.task_id)
            if task is None:
                raise ValueError("calendar event cannot be traced to a task")
            source_key = f"planning-v2:{plan.artifact_id}:{schedule.artifact_id}:{session.id}"
            if source_key in seen:
                raise ValueError("calendar sourceKey must be unique")
            seen.add(source_key)
            events.append(
                CalendarEventProposal(
                    sourcePlanId=plan.artifact_id,
                    sourcePlanVersion=plan.version,
                    sourceScheduleId=schedule.artifact_id,
                    sourceScheduleVersion=schedule.version,
                    sourceTaskId=task.id,
                    sourceSessionId=session.id,
                    sourceKey=source_key,
                    title=task.title,
                    start=session.start,
                    end=session.end,
                    description=task.purpose,
                    completionEvidence=task.completion_evidence,
                )
            )
        required = {task.id for task in plan.tasks if task.optionality == "required"}
        if not required.issubset({event.source_task_id for event in events}):
            raise ValueError("calendar proposal does not cover all required tasks")
        source_sessions = {event.source_session_id for event in events}
        if source_sessions != {session.id for session in schedule.sessions}:
            raise ValueError("calendar proposal changed the validated schedule workload")
        event_minutes = sum(
            int((datetime.fromisoformat(event.end) - datetime.fromisoformat(event.start)).total_seconds() // 60)
            for event in events
        )
        if event_minutes != schedule.capacity_summary.scheduled_minutes:
            raise ValueError("calendar proposal changed total scheduled minutes")
        return CalendarProposal(
            planRef=plan.artifact_id,
            scheduleRef=schedule.artifact_id,
            calendarSnapshotRef=current_calendar_snapshot_ref,
            timezone=timezone,
            events=events,
        )


class FeedbackRouter:
    RULES = (
        ("approve", ("确认", "批准", "写入日历", "approve")),
        ("reject", ("拒绝", "取消", "reject")),
        ("understanding_change", ("目标", "成功标准", "goal", "constraint")),
        ("schedule_change", ("时间", "日期", "排期", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "schedule", "deadline")),
        ("resource_change", ("资源", "资料", "课程", "resource")),
        ("presentation_change", ("标题", "描述", "展示", "presentation")),
    )

    def route(self, text: str) -> FeedbackRoute:
        normalized = text.strip()
        lower = normalized.casefold()
        for category, tokens in self.RULES:
            if any(token.casefold() in lower for token in tokens):
                return FeedbackRoute(
                    category=category,
                    confidence=0.9,
                    targetIds=[],
                    normalizedInstruction=normalized,
                    reason=f"Matched explicit {category} language.",
                )
        return FeedbackRoute(
            category="plan_change",
            confidence=0.55,
            targetIds=[],
            normalizedInstruction=normalized,
            reason="No higher-priority target was explicit; route to scoped plan repair.",
        )


class FinalApprovalService:
    def create(
        self,
        *,
        session_id: str,
        understanding_version: int,
        constraint_version: int,
        context_version: int,
        plan_version: int,
        quality_report_version: int,
        schedule_version: int,
        calendar_proposal_version: int,
        calendar_snapshot_version: int,
        checkpoint_version: int,
    ) -> FinalApprovalBundle:
        return FinalApprovalBundle(
            sessionId=session_id,
            understandingVersion=understanding_version,
            constraintVersion=constraint_version,
            contextVersion=context_version,
            planVersion=plan_version,
            qualityReportVersion=quality_report_version,
            scheduleVersion=schedule_version,
            calendarProposalVersion=calendar_proposal_version,
            calendarSnapshotVersion=calendar_snapshot_version,
            checkpointVersion=checkpoint_version,
        )

    def assert_current(self, approval: FinalApprovalBundle, versions: Mapping[str, int]) -> None:
        expected = {
            "understanding": approval.understanding_version,
            "constraint": approval.constraint_version,
            "context": approval.context_version,
            "plan": approval.plan_version,
            "quality_report": approval.quality_report_version,
            "schedule": approval.schedule_version,
            "calendar_proposal": approval.calendar_proposal_version,
            "calendar_snapshot": approval.calendar_snapshot_version,
            "checkpoint": approval.checkpoint_version,
        }
        stale = [name for name, version in expected.items() if versions.get(name) != version]
        if stale:
            raise ValueError(f"final approval is stale: {', '.join(stale)}")
        if approval.consumed:
            raise ValueError("final approval has already been consumed")


def quality_from_review(report: PlanCritiqueReport, plan: PlanBlueprint) -> QualityReport:
    issues = [
        QualityIssue(
            issueId=f"critic:{index}",
            category="content",
            severity=item.severity,
            ruleId="semantic_reviewer",
            targetType="plan",
            targetId=plan.artifact_id,
            description=item.description,
            evidenceRefs=[item.evidence] if item.evidence else [],
            allowedOperations=["update_task", "add_task", "replace_resource"],
            repairBasis="isolated_semantic_reviewer",
        )
        for index, item in enumerate(report.issues, start=1)
    ]
    return QualityReport(
        targetArtifactId=plan.artifact_id,
        targetVersion=plan.version,
        hardRulesPassed=True,
        semanticReviewRequired=True,
        semanticReviewCompleted=True,
        issues=issues,
        score=report.score,
        remainingRisks=report.remaining_risks,
    )


class MemoryGateway:
    """Single read boundary; current approved understanding outranks history."""

    def __init__(self, repository):
        self.repository = repository

    def read(
        self,
        *,
        snapshot: UnderstandingSnapshot,
        domain: str = "",
        limit: int = 8,
    ) -> list[SemanticItem]:
        current_keys = {
            item.key
            for section in (
                snapshot.facts,
                snapshot.constraints,
                snapshot.preferences,
                snapshot.success_signals,
            )
            for item in section
        }
        result: list[SemanticItem] = []
        for memory in self.repository.relevant(domain, limit=limit * 2):
            key = f"memory:{memory.category}:{memory.id}"
            if key in current_keys:
                continue
            result.append(
                SemanticItem(
                    id=f"memory-{memory.id}",
                    key=key,
                    statement=memory.statement,
                    sourceType="memory_confirmed",
                    sourceRef=memory.id,
                    confidence=memory.confidence,
                    mutationPolicy="auto_replace",
                )
            )
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def propose_candidate(
        *,
        statement: str,
        category: str,
        source_refs: Sequence[str],
        confidence: float,
        proposed_scope: Sequence[str] = (),
    ) -> MemoryCandidateDraft:
        return MemoryCandidateDraft(
            statement=statement,
            category=category,
            sourceRefs=list(source_refs),
            confidence=confidence,
            proposedScope=list(proposed_scope),
            evidenceCount=len(set(source_refs)),
        )


class ExecutionFeedbackService:
    def record(
        self,
        *,
        task: PlanTask,
        status: str,
        actual_minutes: int | None = None,
        completion_evidence: Sequence[str] = (),
        blocker_reason: str | None = None,
        failure_reason: str | None = None,
    ) -> ExecutionOutcome:
        if status == "completed" and not completion_evidence:
            raise ValueError("completed task requires completion evidence")
        return ExecutionOutcome(
            taskId=task.id,
            status=status,
            estimatedMinutes=task.effort_estimate.expected_minutes,
            actualMinutes=actual_minutes,
            completionEvidence=list(completion_evidence),
            blockerReason=blocker_reason,
            failureReason=failure_reason,
        )

    def propose_replan(
        self,
        *,
        session_id: str,
        outcomes: Sequence[ExecutionOutcome],
    ) -> ReplanProposal | None:
        affected = [
            value.task_id
            for value in outcomes
            if value.status in {"blocked", "failed", "rescheduled"}
        ]
        if not affected:
            return None
        return ReplanProposal(
            sessionId=session_id,
            affectedTaskIds=affected,
            reason="Execution outcomes require a user-reviewed plan or schedule adjustment.",
            proposedOperations=[],
            requiresFinalReview=True,
        )


class UserAdaptationService:
    minimum_observations = 3

    def update_duration(
        self,
        current: UserAdaptation,
        observations: Sequence[LearningObservation],
        *,
        ratios: Sequence[float],
    ) -> UserAdaptation:
        if len(observations) != len(ratios):
            raise ValueError("adaptation ratios must be bound to observations")
        bounded = [value for value in ratios if 0.5 <= value <= 3]
        if len(bounded) < self.minimum_observations:
            return current
        directions = {value > 1.05 for value in bounded if abs(value - 1) > 0.05}
        if len(directions) != 1:
            return current
        ordered = sorted(bounded)
        median = ordered[len(ordered) // 2]
        adjusted = max(0.5, min(3, round((current.duration_multiplier + median) / 2, 2)))
        return current.model_copy(
            update={
                "version": current.version + 1,
                "duration_multiplier": adjusted,
                "observation_refs": [value.id for value in observations],
            }
        )


class PromotionPolicy:
    LOW_RISK = {"duration_estimate", "retrieval_ranking", "non_safety_routing", "non_safety_prompt_wording"}
    HUMAN_RELEASE = {"safety_policy", "write_permission", "memory_admission", "artifact_schema", "approval_rule", "high_risk_domain_policy"}

    def audit(
        self,
        *,
        runtime_version: str,
        change_type: str,
        previous_value: str,
        proposed_value: str,
        observation_refs: Sequence[str],
    ) -> PromotionAudit:
        automatic = change_type in self.LOW_RISK and len(set(observation_refs)) >= 3
        requires_human = change_type in self.HUMAN_RELEASE or not automatic
        return PromotionAudit(
            runtimeVersion=runtime_version,
            changeType=change_type,
            previousValue=previous_value,
            proposedValue=proposed_value,
            observationRefs=list(observation_refs),
            allowedForAutomaticPromotion=automatic,
            requiresHumanRelease=requires_human,
            reason=(
                "Low-risk configuration has repeated evidence."
                if automatic
                else "Change remains behind the human release boundary."
            ),
            rollbackValue=previous_value,
        )
