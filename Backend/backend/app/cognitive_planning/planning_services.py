from __future__ import annotations

import copy
import math
import re
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence

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


QUESTION_BUDGET = {"quick": 1, "standard": 2, "complex": 3}
EXTERNAL_FACT_PATTERNS = (
    re.compile(r"https?://", re.I),
    re.compile(r"(?:current|latest|as of|当前|最新).{0,24}(?:price|policy|version|deadline|价格|政策|版本|截止日期)", re.I),
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
    def classify_complexity(snapshot: UnderstandingSnapshot) -> str:
        count = len(snapshot.constraints) + len(snapshot.facts) + len(snapshot.unknowns)
        if any(item.blocking_category in {"safety", "feasibility"} for item in snapshot.unknowns):
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
        non_assumable = {"core_goal", "safety", "feasibility", "hard_constraint"}
        safety_blocked = any(item.blocking_category in non_assumable for item in blocking)
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
                if item.blocking_category in non_assumable:
                    continue
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
            unknowns = [item for item in unknowns if item.key not in blocking_keys or item.blocking_category in non_assumable]
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
    _date = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
    _relative_horizon = re.compile(
        r"(?P<value>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*"
        r"(?P<unit>days?|weeks?|months?)",
        re.I,
    )
    _weekday_names = {
        "周一": 0, "星期一": 0, "monday": 0,
        "周二": 1, "星期二": 1, "tuesday": 1,
        "周三": 2, "星期三": 2, "wednesday": 2,
        "周四": 3, "星期四": 3, "thursday": 3,
        "周五": 4, "星期五": 4, "friday": 4,
        "周六": 5, "星期六": 5, "saturday": 5,
        "周日": 6, "星期日": 6, "星期天": 6, "sunday": 6,
    }

    def compile(self, snapshot: UnderstandingSnapshot, *, today: date | None = None) -> ConstraintSet:
        core = CoreConstraints()
        semantic: list[SemanticConstraint] = []
        constraint_ids = {item.id for item in snapshot.constraints}
        items = list({item.id: item for item in [*snapshot.constraints, *snapshot.facts]}.values())
        anchor = today or date.today()
        relative_horizon_days: int | None = None
        for item in items:
            statement = item.statement
            budget = self._budget.search(statement)
            lower = statement.casefold()
            compiled = False
            for clause in re.split(r"[，,、;；]|\band\b|和|以及", statement, flags=re.I):
                clause_lower = clause.casefold()
                hours = self._hours.search(clause)
                minutes = self._minutes.search(clause)
                duration = (
                    int(float(hours.group("value")) * 60)
                    if hours else int(minutes.group("value")) if minutes else None
                )
                if duration is None:
                    continue
                if any(token in clause_lower for token in ("单次", "每次", "session")):
                    core.maximum_session_minutes = duration
                elif any(token in clause_lower for token in ("每周", "一周", "per week", "weekly")):
                    core.weekly_capacity_minutes = duration
                elif any(token in clause_lower for token in ("周末", "weekend")):
                    core.weekend_capacity_minutes = duration
                elif any(token in clause_lower for token in ("工作日", "weekday")):
                    core.weekday_capacity_minutes = duration
                elif any(token in clause_lower for token in ("每天", "每日", "daily", "per day", "a day")):
                    core.weekday_capacity_minutes = duration
                    core.weekend_capacity_minutes = duration
                else:
                    continue
                compiled = True
            dates = self._date.findall(statement)
            if dates and any(token in lower for token in ("截止", "deadline", "before", "之前")):
                core.deadline = dates[-1]
                compiled = True
            if dates and any(token in lower for token in ("开始", "start", "from")):
                core.required_start_date = dates[0]
                compiled = True
            if dates and any(token in lower for token in ("排除", "不要", "避开", "exclude", "not on")):
                core.excluded_dates = sorted(set([*core.excluded_dates, *dates]))
                compiled = True
            if any(token in lower for token in ("不要安排", "排除", "避开", "exclude", "not on")):
                excluded = [number for name, number in self._weekday_names.items() if name in lower]
                if excluded:
                    core.excluded_weekdays = sorted(set([*core.excluded_weekdays, *excluded]))
                    compiled = True
            if any(token in lower for token in ("个月", "周内", "weeks", "months", "planning horizon")):
                core.planning_horizon = statement
                compiled = True
            relative_days = self._relative_horizon_days(statement)
            if relative_days is not None:
                relative_horizon_days = max(relative_horizon_days or 0, relative_days)
                compiled = True
            if budget:
                core.budget_limit = float(budget.group("value"))
                compiled = True
            elif any(token in lower for token in ("零预算", "0预算", "zero budget", "no budget")):
                core.budget_limit = 0
                compiled = True
            if not compiled and item.id in constraint_ids:
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
        if relative_horizon_days is not None:
            core.planning_horizon = f"{relative_horizon_days} days"
            if core.deadline is None:
                core.deadline = (anchor + timedelta(days=relative_horizon_days)).isoformat()
        core.required_deliverables = [item.statement for item in snapshot.success_signals]
        return ConstraintSet(
            understandingRef=snapshot.artifact_id,
            understandingVersion=snapshot.version,
            core=core,
            semantic=semantic,
            sourceConstraintIds=[item.id for item in snapshot.constraints],
        )

    @classmethod
    def _relative_horizon_days(cls, statement: str) -> int | None:
        match = cls._relative_horizon.search(statement)
        if not match:
            return None
        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
        }
        raw = match.group("value").casefold()
        value = int(raw) if raw.isdigit() else words[raw]
        unit = match.group("unit").casefold()
        multiplier = 1 if unit.startswith("day") else 7 if unit.startswith("week") else 30
        return value * multiplier


class ContextBuilder:
    def build(
        self,
        snapshot: UnderstandingSnapshot,
        constraints: ConstraintSet,
        *,
        claims: Sequence[ContextClaim] = (),
        memory_refs: Sequence[str] = (),
        tool_run_refs: Sequence[str] = (),
        calendar_snapshot_ref: str | None = None,
    ) -> ContextPack:
        return ContextPack(
            understandingRef=snapshot.artifact_id,
            constraintRef=constraints.artifact_id,
            claims=list(claims),
            memoryRefs=list(memory_refs),
            toolRunRefs=list(tool_run_refs),
            calendarSnapshotRef=calendar_snapshot_ref,
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
        milestone_ids = [milestone.id for milestone in plan.milestones]
        if len(milestone_ids) != len(set(milestone_ids)):
            issue("unique_milestone_id", "plan", plan.artifact_id, "milestone ids must be unique", [])
        if len(task_ids) != len(set(task_ids)):
            issue("unique_task_id", "plan", plan.artifact_id, "task ids must be unique", [])
        normalized_titles = [" ".join(task.title.casefold().split()) for task in plan.tasks]
        if len(normalized_titles) != len(set(normalized_titles)):
            issue("duplicate_task", "plan", plan.artifact_id, "plan contains duplicate task titles", ["update_task", "remove_optional_task"])
        known = set(task_ids)
        known_milestones = set(milestone_ids)
        goal_refs = {item.id for item in snapshot.success_signals}
        constraint_refs = set(constraints.source_constraint_ids) | {item.stable_id for item in constraints.semantic}
        verified_refs = {
            ref
            for claim in context.claims
            if claim.verification_status == "verified"
            for ref in (claim.id, claim.source_ref)
        }
        for milestone in plan.milestones:
            if set(milestone.success_signal_refs) - goal_refs:
                issue("milestone_success_ref_exists", "milestone", milestone.id, "milestone references an unknown success signal", ["add_success_coverage"])
        for task in plan.tasks:
            if task.milestone_id not in known_milestones:
                issue("milestone_exists", "task", task.id, "task milestone does not exist", ["move_task"])
            if not task.action_steps:
                issue("action_steps", "task", task.id, "task has no action steps", ["update_task"])
            if not task.deliverable.strip():
                issue("deliverable", "task", task.id, "task has no deliverable", ["update_task"])
            if not task.completion_evidence:
                issue("completion_evidence", "task", task.id, "task has no completion evidence", ["update_task"])
            missing = [item for item in task.dependencies if item not in known]
            if missing:
                issue("dependency_exists", "task", task.id, f"unknown dependencies: {missing}", ["remove_dependency", "add_task"])
            if task.id in task.dependencies:
                issue("dependency_self", "task", task.id, "task cannot depend on itself", ["remove_dependency"])
            if len(task.dependencies) != len(set(task.dependencies)):
                issue("dependency_duplicate", "task", task.id, "task contains duplicate dependencies", ["remove_dependency"])
            if not task.source_goal_refs:
                issue("goal_support", "task", task.id, "task is not traceable to the approved goal", ["add_success_coverage", "remove_optional_task"])
            elif set(task.source_goal_refs) - goal_refs:
                issue("goal_ref_exists", "task", task.id, "task references an unknown success signal", ["add_success_coverage"])
            if set(task.source_constraint_refs) - constraint_refs:
                issue("constraint_ref_exists", "task", task.id, "task references an unknown constraint", ["update_task"])
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
            if item.mutation_policy == "immutable" and self._requires_task_coverage(item.statement)
        }
        constraint_coverage = {ref for task in plan.tasks for ref in task.source_constraint_refs}
        for constraint_id in immutable - constraint_coverage:
            issue("immutable_constraint", "constraint", constraint_id, "immutable constraint is not preserved by any task", ["add_constraint_coverage", "add_task"])
        capacity = self._total_usable_capacity(constraints.core)
        if capacity is not None:
            expected = sum(task.effort_estimate.expected_minutes for task in plan.tasks)
            if expected > capacity:
                issue(
                    "capacity_order",
                    "plan",
                    plan.artifact_id,
                    f"expected workload {expected} minutes exceeds usable horizon capacity {capacity} minutes",
                    ["update_effort", "remove_optional_task"],
                )
        return QualityReport(
            targetArtifactId=plan.artifact_id,
            targetVersion=plan.version,
            hardRulesPassed=not issues,
            issues=issues,
            repairRound=repair_round,
        )

    @staticmethod
    def _requires_task_coverage(statement: str) -> bool:
        normalized = " ".join(statement.casefold().split())
        return not any(
            marker in normalized
            for marker in ("not required", "not necessary", "is optional", "无需", "不要求", "非必需", "可选")
        )

    @staticmethod
    def _total_usable_capacity(core: CoreConstraints) -> int | None:
        match = re.fullmatch(r"(?P<days>\d+) days", core.planning_horizon or "")
        if match:
            days = int(match.group("days"))
        elif core.deadline:
            start = date.fromisoformat(core.required_start_date) if core.required_start_date else date.today() + timedelta(days=1)
            days = max(1, (date.fromisoformat(core.deadline) - start).days + 1)
        else:
            return None
        limits: list[int] = []
        if core.weekly_capacity_minutes is not None:
            limits.append(core.weekly_capacity_minutes * math.ceil(days / 7))
        if core.weekday_capacity_minutes is not None or core.weekend_capacity_minutes is not None:
            start = date.fromisoformat(core.required_start_date) if core.required_start_date else date.today() + timedelta(days=1)
            daily_total = 0
            for offset in range(days):
                current = start + timedelta(days=offset)
                limit = core.weekend_capacity_minutes if current.weekday() >= 5 else core.weekday_capacity_minutes
                daily_total += int(limit or 0)
            limits.append(daily_total)
        if not limits:
            return None
        return math.floor(min(limits) * (1 - core.minimum_buffer_ratio))

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
        "add_constraint_coverage",
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
            if operation.operation == "add_constraint_coverage":
                if index is None:
                    raise ValueError("repair target task does not exist")
                refs = set(tasks[index]["source_constraint_refs"])
                refs.update(str(value) for value in operation.payload.get("sourceConstraintRefs", []))
                tasks[index]["source_constraint_refs"] = sorted(refs)
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
            elif operation.operation == "move_task":
                milestone_id = str(operation.payload.get("milestoneId") or operation.payload.get("milestone_id") or "")
                if milestone_id not in {value["id"] for value in candidate["milestones"]}:
                    raise ValueError("repair milestone target does not exist")
                task["milestone_id"] = milestone_id
            elif operation.operation == "split_task":
                raw_parts = operation.payload.get("tasks") or operation.payload.get("newTasks") or []
                parts = [PlanTask.model_validate(value) for value in raw_parts]
                if len(parts) < 2 or parts[0].id != task["id"]:
                    raise ValueError("split_task must preserve the original id on the first part")
                if sum(value.effort_estimate.expected_minutes for value in parts) != task["effort_estimate"]["expected_minutes"]:
                    raise ValueError("split_task must preserve total expected effort")
                if len({value.id for value in parts}) != len(parts):
                    raise ValueError("split_task produced duplicate task ids")
                tasks[index:index + 1] = [value.model_dump() for value in parts]
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
        same_issue = any(
            value.rule_id == issue.rule_id
            and (issue.target_type == "plan" or value.target_id == issue.target_id)
            for value in report.issues
        )
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


class SchedulePatchGuard:
    """Deterministic, issue-scoped Schedule repair with stable task/session lineage."""

    def apply(
        self,
        schedule: ScheduleBlueprint,
        issue: QualityIssue,
        *,
        constraints: ConstraintSet,
        calendar_busy: Sequence[tuple[datetime, datetime]] = (),
    ) -> ScheduleBlueprint:
        sessions = [item.model_copy() for item in schedule.sessions]
        target_index = next((index for index, item in enumerate(sessions) if item.id == issue.target_id), None)
        allowed = set(issue.allowed_operations)
        if issue.rule_id == "maximum_session" and "split_schedule_session" in allowed and target_index is not None:
            original = sessions[target_index]
            maximum = constraints.core.maximum_session_minutes or 120
            start = datetime.fromisoformat(original.start)
            remaining = original.duration_minutes
            parts: list[ScheduleSession] = []
            part = 0
            while remaining:
                duration = min(maximum, remaining)
                end = start + timedelta(minutes=duration)
                parts.append(original.model_copy(update={
                    "id": original.id if part == 0 else f"{original.id}-split-{part}",
                    "start": start.isoformat(), "end": end.isoformat(), "duration_minutes": duration,
                    "status": "split", "reason": f"Split for {issue.rule_id}.",
                }))
                start = end + timedelta(minutes=30)
                remaining -= duration
                part += 1
            sessions[target_index:target_index + 1] = parts
        elif "move_schedule_session" in allowed:
            indexes = [target_index] if target_index is not None else list(range(len(sessions) - 1, -1, -1))
            if not indexes:
                raise ValueError("schedule repair has no movable session")
            moved = False
            for index in indexes:
                if index is None:
                    continue
                current = sessions[index]
                start = datetime.fromisoformat(current.start) + timedelta(days=1)
                start = start.replace(hour=9, minute=0, second=0, microsecond=0)
                for _ in range(370):
                    end = start + timedelta(minutes=current.duration_minutes)
                    excluded = start.date().isoformat() in constraints.core.excluded_dates or start.weekday() in constraints.core.excluded_weekdays
                    conflict = any(start < busy_end and end > busy_start for busy_start, busy_end in calendar_busy)
                    overlap = any(other.id != current.id and start < datetime.fromisoformat(other.end) and end > datetime.fromisoformat(other.start) for other in sessions)
                    capacity_ok = self._fits_capacity(sessions, current.id, start, current.duration_minutes, constraints)
                    if not excluded and not conflict and not overlap and capacity_ok and (not constraints.core.deadline or end.date().isoformat() <= constraints.core.deadline):
                        sessions[index] = current.model_copy(update={"start": start.isoformat(), "end": end.isoformat(), "status": "moved", "reason": f"Moved for {issue.rule_id}."})
                        moved = True
                        break
                    start += timedelta(days=1)
                if moved:
                    break
            if not moved:
                raise ValueError("schedule repair could not find a valid target window")
        else:
            raise ValueError("schedule issue has no supported deterministic operation")
        if sum(item.duration_minutes for item in sessions) != schedule.capacity_summary.scheduled_minutes:
            raise ValueError("schedule repair changed total effort")
        sessions = [item.model_copy(update={"sequence": index}) for index, item in enumerate(sorted(sessions, key=lambda value: value.start))]
        capacity_summary = self._capacity_summary(sessions, constraints, schedule.capacity_summary)
        return schedule.model_copy(update={
            "artifact_id": new_artifact_id("schedule"),
            "version": schedule.version + 1,
            "sessions": sessions,
            "periods": sorted({item.start[:10] for item in sessions}),
            "capacity_summary": capacity_summary,
            "buffer_summary": f"Reserved {capacity_summary.buffer_minutes} minutes ({constraints.core.minimum_buffer_ratio:.0%}).",
        })

    @staticmethod
    def _fits_capacity(
        sessions: Sequence[ScheduleSession],
        moving_id: str,
        candidate_start: datetime,
        duration: int,
        constraints: ConstraintSet,
    ) -> bool:
        same_day = sum(
            item.duration_minutes
            for item in sessions
            if item.id != moving_id and datetime.fromisoformat(item.start).date() == candidate_start.date()
        )
        daily_limit = (
            constraints.core.weekend_capacity_minutes
            if candidate_start.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None
            else constraints.core.weekday_capacity_minutes
        )
        daily_usable = ScheduleGenerator._usable(daily_limit, constraints.core.minimum_buffer_ratio)
        if daily_usable is not None and same_day + duration > daily_usable:
            return False
        candidate_week = candidate_start.isocalendar()[:2]
        same_week = sum(
            item.duration_minutes
            for item in sessions
            if item.id != moving_id and datetime.fromisoformat(item.start).isocalendar()[:2] == candidate_week
        )
        weekly_usable = ScheduleGenerator._usable(constraints.core.weekly_capacity_minutes, constraints.core.minimum_buffer_ratio)
        return weekly_usable is None or same_week + duration <= weekly_usable

    @staticmethod
    def _capacity_summary(
        sessions: Sequence[ScheduleSession],
        constraints: ConstraintSet,
        previous: CapacitySummary,
    ) -> CapacitySummary:
        days = {datetime.fromisoformat(item.start).date() for item in sessions}
        weeks = {day.isocalendar()[:2] for day in days}
        scheduled = sum(item.duration_minutes for item in sessions)
        limits = [
            constraints.core.weekend_capacity_minutes
            if day.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None
            else constraints.core.weekday_capacity_minutes
            for day in days
        ]
        concrete_limits = [limit for limit in limits if limit is not None]
        if concrete_limits:
            available = sum(concrete_limits)
            buffer = sum(limit - (ScheduleGenerator._usable(limit, constraints.core.minimum_buffer_ratio) or 0) for limit in concrete_limits)
        elif constraints.core.weekly_capacity_minutes is not None:
            available = constraints.core.weekly_capacity_minutes * len(weeks)
            buffer = sum(
                constraints.core.weekly_capacity_minutes
                - (ScheduleGenerator._usable(constraints.core.weekly_capacity_minutes, constraints.core.minimum_buffer_ratio) or 0)
                for _ in weeks
            )
        else:
            available = max(previous.available_minutes, scheduled)
            buffer = math.ceil(scheduled * constraints.core.minimum_buffer_ratio / max(0.01, 1 - constraints.core.minimum_buffer_ratio))
        return previous.model_copy(update={
            "available_minutes": available,
            "scheduled_minutes": scheduled,
            "buffer_minutes": buffer,
        })


class ScheduleGenerator:
    def generate(
        self,
        plan: PlanBlueprint,
        constraints: ConstraintSet,
        *,
        start: datetime,
        timezone: str = "Asia/Shanghai",
        calendar_snapshot_ref: str | None = None,
        calendar_busy: Sequence[tuple[datetime, datetime]] = (),
    ) -> ScheduleBlueprint:
        maximum = constraints.core.maximum_session_minutes or 120
        cursor = start
        sessions: list[ScheduleSession] = []
        unscheduled: list[str] = []
        daily_used: dict[str, int] = defaultdict(int)
        weekly_used: dict[tuple[int, int], int] = defaultdict(int)
        used_capacity: dict[str, int] = {}
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
                week = cursor.isocalendar()
                week_key = (week.year, week.week)
                weekly_limit = constraints.core.weekly_capacity_minutes
                day_usable = self._usable(day_limit, constraints.core.minimum_buffer_ratio)
                week_usable = self._usable(weekly_limit, constraints.core.minimum_buffer_ratio)
                excluded = (
                    day_key in constraints.core.excluded_dates
                    or cursor.weekday() in constraints.core.excluded_weekdays
                    or day_limit == 0
                )
                available = remaining if day_usable is None else max(0, day_usable - daily_used[day_key])
                if week_usable is not None:
                    available = min(available, max(0, week_usable - weekly_used[week_key]))
                if excluded or available <= 0:
                    cursor = (cursor + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    skipped_days += 1
                    if skipped_days > 370:
                        unscheduled.append(task.id)
                        break
                    continue
                duration = min(maximum, remaining, available)
                end = cursor + timedelta(minutes=duration)
                overlap = next((busy_end for busy_start, busy_end in calendar_busy if cursor < busy_end and end > busy_start), None)
                if overlap is not None:
                    cursor = overlap
                    continue
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
                weekly_used[week_key] += duration
                if day_limit is not None:
                    used_capacity[day_key] = day_limit
                cursor = end + timedelta(minutes=30)
                remaining -= duration
                sequence += 1
        scheduled = sum(value.duration_minutes for value in sessions)
        if used_capacity:
            available_total = sum(used_capacity.values())
            buffer = sum(limit - self._usable(limit, constraints.core.minimum_buffer_ratio) for limit in used_capacity.values())
        elif constraints.core.weekly_capacity_minutes is not None and weekly_used:
            available_total = constraints.core.weekly_capacity_minutes * len(weekly_used)
            buffer = sum(
                constraints.core.weekly_capacity_minutes - self._usable(constraints.core.weekly_capacity_minutes, constraints.core.minimum_buffer_ratio)
                for _ in weekly_used
            )
        else:
            buffer = math.ceil(scheduled * constraints.core.minimum_buffer_ratio / max(0.01, 1 - constraints.core.minimum_buffer_ratio))
            available_total = scheduled + buffer
        return ScheduleBlueprint(
            planRef=plan.artifact_id,
            planVersion=plan.version,
            calendarSnapshotRef=calendar_snapshot_ref,
            planningTimezone=timezone,
            periods=sorted({value.start[:10] for value in sessions}),
            sessions=sessions,
            capacitySummary=CapacitySummary(
                availableMinutes=available_total,
                scheduledMinutes=scheduled,
                bufferMinutes=buffer,
            ),
            bufferSummary=f"Reserved {buffer} minutes ({constraints.core.minimum_buffer_ratio:.0%}).",
            unscheduledTaskIds=unscheduled,
        )

    @staticmethod
    def _usable(limit: int | None, ratio: float) -> int | None:
        return None if limit is None else max(0, math.floor(limit * (1 - ratio)))

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
        for day, minutes in daily.items():
            parsed_day = datetime.fromisoformat(day)
            limit = (
                constraints.core.weekend_capacity_minutes
                if parsed_day.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None
                else constraints.core.weekday_capacity_minutes
            )
            usable = ScheduleGenerator._usable(limit, constraints.core.minimum_buffer_ratio)
            if usable is not None and minutes > usable:
                    issues.append(self._issue("daily_capacity", day, "scheduled minutes exceed configured capacity", ["move_schedule_session", "split_schedule_session"]))
        weekly: dict[tuple[int, int], int] = defaultdict(int)
        for day, minutes in daily.items():
            iso = datetime.fromisoformat(day).isocalendar()
            weekly[(iso.year, iso.week)] += minutes
        if constraints.core.weekly_capacity_minutes is not None:
            weekly_usable = ScheduleGenerator._usable(constraints.core.weekly_capacity_minutes, constraints.core.minimum_buffer_ratio) or 0
            for week, minutes in weekly.items():
                if minutes > weekly_usable:
                    issues.append(self._issue("weekly_capacity", f"{week[0]}-W{week[1]:02d}", "scheduled minutes exceed weekly capacity", ["move_schedule_session", "split_schedule_session"]))
        elif constraints.core.weekday_capacity_minutes is not None or constraints.core.weekend_capacity_minutes is not None:
            daily_limit = constraints.core.weekday_capacity_minutes
            weekend_limit = constraints.core.weekend_capacity_minutes
            if weekend_limit is None:
                weekend_limit = daily_limit or 0
            weekly_limit = (ScheduleGenerator._usable(daily_limit, constraints.core.minimum_buffer_ratio) or 0) * 5 + (ScheduleGenerator._usable(weekend_limit, constraints.core.minimum_buffer_ratio) or 0) * 2
            for week, minutes in weekly.items():
                if minutes > weekly_limit:
                    issues.append(self._issue("weekly_capacity", f"{week[0]}-W{week[1]:02d}", "scheduled minutes exceed weekly capacity", ["move_schedule_session", "split_schedule_session"]))
        expected = sum(task.effort_estimate.expected_minutes for task in plan.tasks)
        scheduled = sum(session.duration_minutes for session in schedule.sessions)
        if expected != scheduled:
            issues.append(self._issue("effort_preserved", schedule.artifact_id, "schedule changed total task effort", ["update_schedule_session"]))
        day_limits = []
        for day in daily:
            parsed_day = datetime.fromisoformat(day)
            limit = constraints.core.weekend_capacity_minutes if parsed_day.weekday() >= 5 and constraints.core.weekend_capacity_minutes is not None else constraints.core.weekday_capacity_minutes
            if limit is not None:
                day_limits.append(limit)
        if day_limits:
            available_capacity = sum(day_limits)
            required_buffer = sum(limit - (ScheduleGenerator._usable(limit, constraints.core.minimum_buffer_ratio) or 0) for limit in day_limits)
        elif constraints.core.weekly_capacity_minutes is not None and weekly:
            available_capacity = constraints.core.weekly_capacity_minutes * len(weekly)
            required_buffer = sum(constraints.core.weekly_capacity_minutes - (ScheduleGenerator._usable(constraints.core.weekly_capacity_minutes, constraints.core.minimum_buffer_ratio) or 0) for _ in weekly)
        else:
            available_capacity = schedule.capacity_summary.available_minutes
            required_buffer = math.ceil(scheduled * constraints.core.minimum_buffer_ratio / max(0.01, 1 - constraints.core.minimum_buffer_ratio))
        if schedule.capacity_summary.buffer_minutes < required_buffer or scheduled + schedule.capacity_summary.buffer_minutes > available_capacity:
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
        ("presentation_change", ("日历标题", "标题", "描述", "展示", "presentation")),
        ("schedule_change", ("时间", "日期", "排期", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "schedule", "deadline")),
        ("resource_change", ("资源", "资料", "课程", "resource")),
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
        schedule_quality_report_version: int,
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
            scheduleQualityReportVersion=schedule_quality_report_version,
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
            "schedule_quality": approval.schedule_quality_report_version,
            "calendar_proposal": approval.calendar_proposal_version,
            "calendar_snapshot": approval.calendar_snapshot_version,
        }
        stale = [name for name, version in expected.items() if versions.get(name) != version]
        if stale:
            raise ValueError(f"final approval is stale: {', '.join(stale)}")
        if approval.consumed:
            raise ValueError("final approval has already been consumed")


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
