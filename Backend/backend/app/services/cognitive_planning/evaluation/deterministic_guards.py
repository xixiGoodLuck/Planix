from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from ....harness.quality import meets_critic_score_gate

from ..contracts import (
    ExecutionBlueprint,
    PlanCritiqueReport,
    RealityAssessment,
    UserGoalModel,
)


FORBIDDEN_TEMPLATE_PHRASES = (
    "这个主题",
    "能做小项目",
    "学习并复现",
    "完成一个可检查产出",
    "确认基础与最小路径",
    "项目驱动的稳步学习计划",
)


class DeterministicGuardError(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = issues


def template_phrase_hits(values: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for value in values:
        for phrase in FORBIDDEN_TEMPLATE_PHRASES:
            if phrase in (value or "") and phrase not in hits:
                hits.append(phrase)
    return hits


def validate_execution_invariants(blueprint: ExecutionBlueprint) -> None:
    issues: list[str] = []
    narrative_fields = {
        "executionLogic": blueprint.narrative.execution_logic,
        "dependencyExplanation": blueprint.narrative.dependency_explanation,
        "weeklyOrStageRhythm": blueprint.narrative.weekly_or_stage_rhythm,
        "workloadReasoning": blueprint.narrative.workload_reasoning,
        "riskHandling": blueprint.narrative.risk_handling,
    }
    for field_name, value in narrative_fields.items():
        if not value.strip():
            issues.append(f"execution narrative has no {field_name}")
    ids = [task.id for task in blueprint.tasks]
    if len(ids) != len(set(ids)):
        issues.append("task ids must be unique")
    titles = [task.title for task in blueprint.tasks]
    for phrase in template_phrase_hits(titles):
        issues.append(f"forbidden template phrase in task title: {phrase}")
    known_ids = set(ids)
    for task in blueprint.tasks:
        if not task.title.strip():
            issues.append(f"{task.id} has no title")
        if not task.purpose.strip():
            issues.append(f"{task.id} has no purpose")
        if not task.why_now.strip():
            issues.append(f"{task.id} has no sequencing rationale")
        unknown_dependencies = [item for item in task.dependencies if item not in known_ids]
        if unknown_dependencies:
            issues.append(f"{task.id} has unknown dependencies: {', '.join(unknown_dependencies)}")
        if task.scheduled_date:
            try:
                date.fromisoformat(task.scheduled_date)
            except ValueError:
                issues.append(f"{task.id} has invalid scheduledDate")
        elif not str(task.schedule_window or "").strip():
            issues.append(f"{task.id} has no scheduledDate or scheduleWindow")
        if not task.action_steps or any(not item.strip() for item in task.action_steps):
            issues.append(f"{task.id} has no action steps")
        if not task.completion_evidence or any(not item.strip() for item in task.completion_evidence):
            issues.append(f"{task.id} has no completion evidence")
        if not task.deliverable.strip():
            issues.append(f"{task.id} has no deliverable")
        if not task.resources:
            issues.append(f"{task.id} has no resources")
        for index, resource in enumerate(task.resources, start=1):
            if not resource.title.strip():
                issues.append(f"{task.id} resource {index} has no title")
            if not resource.exact_usage.strip():
                issues.append(f"{task.id} resource {index} has no exact usage")
            if not resource.expected_contribution.strip():
                issues.append(f"{task.id} resource {index} has no expected contribution")
        if not task.fallback_action.strip():
            issues.append(f"{task.id} has no fallback action")
        if not task.risks or any(not item.strip() for item in task.risks):
            issues.append(f"{task.id} has no complete risk description")
    issues.extend(_dependency_cycle_issues(blueprint))
    if issues:
        raise DeterministicGuardError(issues)


_BUDGET_HINT = re.compile(
    r"(?:budget|spending\s+limit|cost\s+cap|预算|费用上限|支出上限)",
    re.IGNORECASE,
)
_CURRENCY_PATTERNS = (
    re.compile(
        r"(?:CNY|RMB|[￥¥])\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万|k)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万|k)?\s*(?:元|人民币|CNY|RMB)",
        re.IGNORECASE,
    ),
)
_WEEKLY_CAPACITY_PATTERNS = (
    re.compile(
        r"每周[^0-9]{0,16}([0-9]+(?:\.[0-9]+)?)\s*(小时|分钟)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([0-9]+(?:\.[0-9]+)?)\s*(hours?|hrs?|minutes?|mins?)\s*(?:per|each)\s+week",
        re.IGNORECASE,
    ),
    re.compile(
        r"weekly(?:\s+(?:capacity|availability|commitment))?\s*(?:of|is|:)?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(hours?|hrs?|minutes?|mins?)",
        re.IGNORECASE,
    ),
)
_CRITIC_POLICY_SENTENCE_SPLIT = re.compile(r"[\n。！？!?；;]+")
_HALF_TIME_REFERENCE = re.compile(
    r"(?:"
    r"\bhalf(?:[-\s]+(?:the\s+)?(?:planned\s+)?)?(?:time|capacity|availability|workload)\b"
    r"|\b(?:time|capacity|availability|workload)\s+(?:is\s+)?halved\b"
    r"|\b50\s*%\s*(?:of\s+)?(?:the\s+)?(?:time|capacity|availability|workload)\b"
    r"|半(?:时间|时长|工时|容量|负荷)"
    r"|一半(?:的)?(?:时间|时长|工时|容量|负荷)"
    r"|(?:时间|时长|工时|容量|负荷|投入|可用时间)(?:只剩|减半|降低到|降低至|降至)"
    r"\s*(?:一半|50\s*%)"
    r"|50\s*%\s*(?:时间|时长|工时|容量|负荷|投入)"
    r")",
    re.IGNORECASE,
)
_WEEKLY_WORKLOAD_REFERENCE = re.compile(
    r"(?:"
    r"\bweekly\s+(?:time|capacity|availability|commitment|workload|load|hours?|minutes?|budget)\b"
    r"|\b(?:time|capacity|availability|commitment|workload|load|hours?|minutes?|budget)"
    r"\s+(?:per|each)\s+week\b"
    r"|\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)\s*(?:per|each)\s+week\b"
    r"|每周.{0,12}(?:投入|时间|时长|工时|容量|负荷|训练量|工作量|任务量|分钟|小时|上限|配额)"
    r"|(?:周工时|周时长|周容量|周负荷|周投入|每周上限|每周配额)"
    r")",
    re.IGNORECASE,
)
_HARD_QUOTA_LANGUAGE = re.compile(
    r"(?:"
    r"\b(?:must|shall|should|required?|needs?\s+to|cannot|can't|must\s+not)\b"
    r"|\b(?:no\s+more\s+than|at\s+most|exactly|hard\s+cap|quota)\b"
    r"|\b(?:cap(?:ped)?\s+at|limit(?:ed)?\s+to|set\s+to|fix(?:ed)?\s+at)\b"
    r"|\b(?:reduce|cut|lower|shrink)\b.{0,24}(?:\bto\b|\bby\s+half\b|50\s*%)"
    r"|\b(?:exceeds?|over)\b.{0,16}\b(?:capacity|cap|quota|limit)\b"
    r"|必须|要求|应当|需要(?:将|把)?|不得|不能超过|最多|只能|仅能|上限|配额"
    r"|超出|超过.{0,8}(?:容量|上限|配额)|限制在|控制在|固定为|设为|降至|减至|减少至"
    r"|降低至|降低到|减半|压缩到|压缩至|应(?:将|把|减|降|控制|限制|为)"
    r")",
    re.IGNORECASE,
)
_NON_BINDING_QUOTA_LANGUAGE = re.compile(
    r"(?:\b(?:may|might|can|could|optionally|suggest(?:ed)?)\b|(?:可以|可将|可能|视情况|建议))",
    re.IGNORECASE,
)
_OVERRIDING_BINDING_LANGUAGE = re.compile(
    r"(?:\b(?:must|shall|required?|cannot|can't|must\s+not|no\s+more\s+than|at\s+most|exactly)\b"
    r"|必须|要求|应当|不得|不能超过|最多|只能|仅能|上限|配额)",
    re.IGNORECASE,
)
_DIRECT_SOURCE_REFERENCE = re.compile(
    r"(?:\bsource\s*ref\b|\burl\b|\b(?:source\s+)?link\b|来源引用|来源链接|网址|链接)",
    re.IGNORECASE,
)
_RESOURCE_PROVIDER_NAME = re.compile(
    r"(?:\b(?:service\s+)?provider(?:\s+name)?\b|\bvendor(?:\s+name)?\b|\bvenue(?:\s+name)?\b"
    r"|服务商|供应商|提供商|机构名称|场馆名称|平台名称)",
    re.IGNORECASE,
)
_SOURCE_REQUIREMENT_LANGUAGE = re.compile(
    r"(?:\b(?:must|shall|should|required?|needs?\s+to|add|provide|include|supply|specify|name|identify)\b"
    r"|必须|要求|应当|需要|补充|提供|添加|写明|列出|指定|标明)",
    re.IGNORECASE,
)
_SOURCE_ABSENCE_LANGUAGE = re.compile(
    r"(?:\b(?:missing|lacks?|without|absent|not\s+(?:provided|supplied|named)|unverified)\b"
    r"|缺少|未提供|未给出|没有|无(?:可验证)?|不可验证|无法验证)",
    re.IGNORECASE,
)
_SOURCE_DEFECT_LANGUAGE = re.compile(
    r"(?:\b(?:unactionable|not\s+actionable|invalid|insufficient|cannot\s+be\s+verified)\b"
    r"|不可操作|不可执行|不合格|不足|不可验证|无法验证)",
    re.IGNORECASE,
)
_SOURCE_OPTIONAL_LANGUAGE = re.compile(
    r"(?:\b(?:optional|not\s+required|need\s+not|do\s+not\s+(?:require|demand)|must\s+not\s+(?:require|demand))\b"
    r"|无需|不需要|不必|可选|不要求|不得要求|不应要求)",
    re.IGNORECASE,
)
_WEEK_RANGE_PATTERNS = (
    re.compile(r"\bweeks?\s*(\d+)\s*(?:-|–|—|to|through)\s*(?:week\s*)?(\d+)\b", re.IGNORECASE),
    re.compile(r"第?\s*(\d+)\s*(?:-|–|—|至|到)\s*第?\s*(\d+)\s*周"),
)
_SINGLE_WEEK_PATTERNS = (
    re.compile(r"\bweek[\s:_-]*(\d+)\b", re.IGNORECASE),
    re.compile(r"第?\s*(\d+)\s*周"),
)
_COUNTDOWN_WEEK_PATTERNS = (
    (
        "zh",
        re.compile(
            r"^\s*(?P<anchor>[\u4e00-\u9fff]{1,24})前\s*"
            r"(?P<week>\d{1,3})\s*周\s*$"
        ),
    ),
    (
        "en",
        re.compile(
            r"^\s*(?P<week>\d{1,3})\s+weeks?\s+before\s+"
            r"(?P<anchor>[a-z][a-z0-9]*(?:[\s_-]+[a-z0-9]+){0,5})\s*$",
            re.IGNORECASE,
        ),
    ),
)
_MAX_TOP_LEVEL_EXECUTION_TASKS = 10


def _dependency_cycle_issues(blueprint: ExecutionBlueprint) -> list[str]:
    graph = {task.id: list(task.dependencies) for task in blueprint.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(task_id: str) -> list[str] | None:
        if task_id in visiting:
            start = stack.index(task_id)
            return [*stack[start:], task_id]
        if task_id in visited:
            return None
        visiting.add(task_id)
        stack.append(task_id)
        for dependency in graph.get(task_id, []):
            if dependency not in graph:
                continue
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(task_id)
        visited.add(task_id)
        return None

    for task_id in graph:
        cycle = visit(task_id)
        if cycle:
            return ["task dependency graph must be acyclic: " + " -> ".join(cycle)]
    return []


def _task_schedule_value(task: Any) -> str:
    return str(task.scheduled_date or task.schedule_window or "").strip()


def _countdown_week_axis(value: str | None) -> tuple[str, list[int]] | None:
    """Parse a week offset that counts backwards from one named anchor.

    The anchor is part of the axis identity. For example, ``8 weeks before
    departure`` and ``6 weeks before arrival`` are deliberately not compared.
    """

    text = str(value or "").strip()
    if not text:
        return None
    for language, pattern in _COUNTDOWN_WEEK_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        week = int(match.group("week"))
        if week < 1:
            return None
        anchor = re.sub(r"[\s_-]+", " ", match.group("anchor").strip()).casefold()
        return f"{language}:{anchor}", [week]
    return None


def _week_schedule_axis(
    value: str | None,
) -> tuple[str, str, list[int]] | None:
    countdown = _countdown_week_axis(value)
    if countdown is not None:
        axis, weeks = countdown
        return "countdown_week", axis, weeks
    weeks = _relative_weeks(value)
    if weeks:
        return "relative_week", "elapsed", weeks
    return None


def _dependency_schedule_violations(
    blueprint: ExecutionBlueprint,
) -> list[dict[str, str]]:
    """Return only dependency reversals proven by comparable schedules.

    Relative schedule windows are intentionally treated as coarse ranges. A
    violation is reported only when the dependent task's entire range ends
    before its prerequisite starts. Overlapping ranges are not rejected
    because the available schedule data cannot prove their internal order.
    Exact dates are comparable directly.
    """

    tasks = {task.id: task for task in blueprint.tasks}
    violations: list[dict[str, str]] = []
    for task in blueprint.tasks:
        for dependency_id in task.dependencies:
            dependency = tasks.get(dependency_id)
            if dependency is None:
                continue

            schedule_type = ""
            reversed_order = False
            if task.scheduled_date and dependency.scheduled_date:
                schedule_type = "exact_date"
                reversed_order = date.fromisoformat(task.scheduled_date) < date.fromisoformat(
                    dependency.scheduled_date
                )
            elif not task.scheduled_date and not dependency.scheduled_date:
                task_axis = _week_schedule_axis(task.schedule_window)
                dependency_axis = _week_schedule_axis(dependency.schedule_window)
                if (
                    task_axis
                    and dependency_axis
                    and task_axis[:2] == dependency_axis[:2]
                ):
                    schedule_type, _, task_weeks = task_axis
                    _, _, dependency_weeks = dependency_axis
                    if schedule_type == "countdown_week":
                        # Larger offsets are earlier on a countdown axis.
                        reversed_order = min(task_weeks) > max(dependency_weeks)
                    else:
                        reversed_order = max(task_weeks) < min(dependency_weeks)

            if not reversed_order:
                continue
            task_schedule = _task_schedule_value(task)
            dependency_schedule = _task_schedule_value(dependency)
            violations.append(
                {
                    "taskId": task.id,
                    "dependencyId": dependency.id,
                    "taskSchedule": task_schedule,
                    "dependencySchedule": dependency_schedule,
                    "scheduleType": schedule_type,
                    "reason": (
                        f"{task.id} schedule {task_schedule} occurs before dependency "
                        f"{dependency.id} schedule {dependency_schedule}"
                    ),
                }
            )
    return violations


def _number_with_multiplier(raw: str, multiplier: str | None) -> int | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    if (multiplier or "").casefold() == "万":
        value *= 10_000
    elif (multiplier or "").casefold() == "k":
        value *= 1_000
    integral = value.to_integral_value()
    return int(integral) if value == integral and value > 0 else None


def _explicit_spending_limit_cny(goal: UserGoalModel) -> int | None:
    """Return only one unambiguous, explicitly unit-labelled hard cap."""

    candidates: set[int] = set()
    for constraint in goal.hard_constraints:
        texts = [constraint.source_text.strip(), constraint.statement.strip()]
        combined = " ".join(item for item in texts if item)
        category = constraint.category.strip()
        if not _BUDGET_HINT.search(f"{category} {combined}"):
            continue
        for text in texts:
            for pattern in _CURRENCY_PATTERNS:
                for match in pattern.finditer(text):
                    value = _number_with_multiplier(match.group(1), match.group(2))
                    if value is not None:
                        candidates.add(value)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _weekly_capacity_minutes(goal: UserGoalModel) -> int | None:
    """Read a weekly capacity only from explicit, unit-labelled Goal evidence."""

    texts: list[str] = []
    for constraint in goal.hard_constraints:
        texts.append(constraint.source_text.strip() or constraint.statement.strip())
    for fact in goal.known_facts:
        if fact.source_text.strip():
            texts.append(fact.source_text.strip())

    candidates: set[int] = set()
    for text in texts:
        for pattern in _WEEKLY_CAPACITY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                value = Decimal(match.group(1))
            except InvalidOperation:
                continue
            unit = match.group(2).casefold()
            minutes = value * (60 if unit in {"小时", "hour", "hours", "hr", "hrs"} else 1)
            integral = minutes.to_integral_value()
            if minutes == integral and minutes > 0:
                candidates.add(int(integral))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _critic_policy_text_windows(*values: str) -> list[str]:
    sentences: list[str] = []
    for value in values:
        sentences.extend(
            item.strip()
            for item in _CRITIC_POLICY_SENTENCE_SPLIT.split(str(value or ""))
            if item.strip()
        )
    windows = list(sentences)
    windows.extend(
        f"{left} {right}"
        for left, right in zip(sentences, sentences[1:], strict=False)
    )
    return list(dict.fromkeys(windows))


def _contains_half_time_reference(text: str) -> bool:
    return any(
        _HALF_TIME_REFERENCE.search(window)
        for window in _critic_policy_text_windows(text)
    )


def _contains_hard_weekly_quota(text: str) -> bool:
    for window in _critic_policy_text_windows(text):
        if not _WEEKLY_WORKLOAD_REFERENCE.search(window):
            continue
        if not _HARD_QUOTA_LANGUAGE.search(window):
            continue
        if (
            _NON_BINDING_QUOTA_LANGUAGE.search(window)
            and not _OVERRIDING_BINDING_LANGUAGE.search(window)
        ):
            continue
        return True
    return False


def _contains_unfounded_resource_reference_requirement(text: str) -> bool:
    for window in _critic_policy_text_windows(text):
        if _SOURCE_OPTIONAL_LANGUAGE.search(window):
            continue
        direct_reference = _DIRECT_SOURCE_REFERENCE.search(window)
        provider_name = _RESOURCE_PROVIDER_NAME.search(window)
        if not direct_reference and not provider_name:
            continue
        demand = _SOURCE_REQUIREMENT_LANGUAGE.search(window)
        absence = _SOURCE_ABSENCE_LANGUAGE.search(window)
        defect = _SOURCE_DEFECT_LANGUAGE.search(window)
        # A request to add sourceRef/URL is explicit by itself. Provider or
        # venue names are more ambiguous, so require the Critic to tie that
        # demand to absence/unverifiability before treating it as a policy
        # violation.
        if direct_reference and (demand or (absence and defect)):
            return True
        if provider_name and absence and (demand or defect):
            return True
    return False


def critic_policy_context(
    *,
    goal: UserGoalModel,
    execution: ExecutionBlueprint,
) -> dict[str, Any]:
    """Return model-readable facts that bound Critic contingency judgments.

    The context contains only deterministic facts. It does not assign or
    alter a Critic score and does not claim that an absent source reference
    has been verified by another mechanism.
    """

    weekly_capacity = _weekly_capacity_minutes(goal)
    resources = [
        (task.id, index, resource)
        for task in execution.tasks
        for index, resource in enumerate(task.resources, start=1)
    ]
    missing_source_refs = [
        {
            "taskId": task_id,
            "resourceIndex": index,
            "title": resource.title,
        }
        for task_id, index, resource in resources
        if not str(resource.source_ref or "").strip()
    ]
    return {
        "weeklyCapacity": {
            "explicitInGoal": weekly_capacity is not None,
            "minutes": weekly_capacity,
        },
        "halfTimeSimulation": {
            "isContingency": True,
            "isPrimaryWeeklyQuota": False,
            "mayExtendTimelineUnlessGoalForbids": True,
        },
        "resources": {
            "total": len(resources),
            "withSourceRef": len(resources) - len(missing_source_refs),
            "withoutSourceRef": len(missing_source_refs),
            "missingSourceRefs": missing_source_refs,
            "sourceRefRequiredForActionability": False,
            "selectionVerificationAndFallbackAreAcceptable": True,
        },
    }


def critic_policy_violations(
    report: PlanCritiqueReport,
    *,
    goal: UserGoalModel,
    execution: ExecutionBlueprint,
) -> list[dict[str, Any]]:
    """Find high-impact Critic claims that contradict deterministic policy.

    Only major/blocker issues and repair requests are inspected. The guard is
    deliberately narrow: it neither recalculates nor fabricates a score, and
    it ignores ordinary mentions of half-time simulations or optional source
    references.
    """

    context = critic_policy_context(goal=goal, execution=execution)
    violations: list[dict[str, Any]] = []
    high_impact_issues = [
        (index, issue)
        for index, issue in enumerate(report.issues)
        if issue.severity in {"major", "blocker"}
    ]
    high_impact_half_time_context = any(
        _contains_half_time_reference(f"{issue.description} {issue.evidence}")
        for _, issue in high_impact_issues
    )

    review_items: list[tuple[str, int, str, str]] = [
        (
            "issue",
            index,
            f"{issue.description} {issue.evidence}",
            issue.severity,
        )
        for index, issue in high_impact_issues
    ]
    review_items.extend(
        (
            "repair_request",
            index,
            f"{request.instruction} {request.expected_change}",
            "repair_request",
        )
        for index, request in enumerate(report.repair_requests)
    )

    for source_kind, source_index, text, severity in review_items:
        direct_half_time = _contains_half_time_reference(text)
        inherited_half_time = (
            source_kind == "repair_request" and high_impact_half_time_context
        )
        if (
            not context["weeklyCapacity"]["explicitInGoal"]
            and (direct_half_time or inherited_half_time)
            and _contains_hard_weekly_quota(text)
        ):
            violations.append(
                {
                    "code": "half_time_contingency_promoted_to_primary_quota",
                    "sourceKind": source_kind,
                    "sourceIndex": source_index,
                    "severity": severity,
                    "message": (
                        "The Critic promoted a half-time contingency into a hard weekly "
                        "quota without an explicit weekly capacity in the Goal."
                    ),
                }
            )

        if (
            context["resources"]["withoutSourceRef"] > 0
            and _contains_unfounded_resource_reference_requirement(text)
        ):
            violations.append(
                {
                    "code": "unverified_resource_reference_promoted_to_hard_requirement",
                    "sourceKind": source_kind,
                    "sourceIndex": source_index,
                    "severity": severity,
                    "message": (
                        "The Critic demanded a sourceRef, URL, venue, or provider name "
                        "solely because a resource has no verified source reference."
                    ),
                }
            )

    return violations


def _relative_weeks(value: str | None) -> list[int] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in _WEEK_RANGE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end < start or end - start > 104:
            return None
        return list(range(start, end + 1))
    for pattern in _SINGLE_WEEK_PATTERNS:
        match = pattern.search(text)
        if match and int(match.group(1)) >= 1:
            return [int(match.group(1))]
    return None


def _numeric(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _canonical_weekly_period(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.isdigit() and int(text) >= 1:
        return f"relative-week:{int(text)}"
    weeks = _relative_weeks(text)
    if weeks and len(weeks) == 1:
        return f"relative-week:{weeks[0]}"
    iso_match = re.fullmatch(r"(?:iso-week:)?(\d{4})[-_/ ]?[wW](\d{1,2})", text)
    if iso_match:
        return f"iso-week:{int(iso_match.group(1))}-{int(iso_match.group(2))}"
    return None


def _task_period_allocations(task: Any) -> tuple[dict[str, Decimal], list[str]]:
    extensions = task.domain_extensions if isinstance(task.domain_extensions, dict) else {}
    explicit = extensions.get("weeklyMinutes")
    weeks = _relative_weeks(task.schedule_window)
    issues: list[str] = []

    if isinstance(explicit, dict) and explicit:
        allocations: dict[str, Decimal] = {}
        for period, raw_minutes in explicit.items():
            minutes = _numeric(raw_minutes)
            if minutes is None:
                return {}, [f"{task.id} weeklyMinutes contains a non-numeric allocation"]
            canonical_period = _canonical_weekly_period(period)
            if canonical_period is None:
                return {}, [
                    f"{task.id} weeklyMinutes contains an unrecognized weekly period"
                ]
            if canonical_period in allocations:
                issues.append(f"{task.id} weeklyMinutes contains duplicate period aliases")
            allocations[canonical_period] = allocations.get(canonical_period, Decimal(0)) + minutes
        if sum(allocations.values(), Decimal(0)) != Decimal(task.estimated_minutes):
            issues.append(f"{task.id} weeklyMinutes must sum to estimatedMinutes")
        return allocations, issues

    if isinstance(explicit, list) and explicit:
        if not weeks or len(explicit) != len(weeks):
            return {}, [
                f"{task.id} weeklyMinutes list must match its scheduleWindow weeks"
            ]
        values = [_numeric(item) for item in explicit]
        if any(item is None for item in values):
            return {}, [f"{task.id} weeklyMinutes contains a non-numeric allocation"]
        allocations = {
            f"relative-week:{week}": value
            for week, value in zip(weeks, values, strict=True)
            if value is not None
        }
        if sum(allocations.values(), Decimal(0)) != Decimal(task.estimated_minutes):
            issues.append(f"{task.id} weeklyMinutes must sum to estimatedMinutes")
        return allocations, issues

    scalar = _numeric(explicit) if explicit is not None else None
    if explicit is not None and scalar is None:
        return {}, [f"{task.id} weeklyMinutes contains a non-numeric allocation"]
    if scalar is not None and weeks:
        allocations = {f"relative-week:{week}": scalar for week in weeks}
        if sum(allocations.values(), Decimal(0)) != Decimal(task.estimated_minutes):
            issues.append(f"{task.id} weeklyMinutes must sum to estimatedMinutes")
        return allocations, issues

    if task.scheduled_date:
        scheduled = date.fromisoformat(task.scheduled_date)
        iso_year, iso_week, _ = scheduled.isocalendar()
        return {f"iso-week:{iso_year}-{iso_week}": Decimal(task.estimated_minutes)}, issues
    if weeks:
        per_week = Decimal(task.estimated_minutes) / Decimal(len(weeks))
        return {f"relative-week:{week}": per_week for week in weeks}, issues
    return {}, issues


def _render_decimal(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def execution_preflight_context(
    blueprint: ExecutionBlueprint,
    *,
    goal: UserGoalModel,
) -> dict[str, Any]:
    """Build a JSON-safe arithmetic and dependency brief for model repair."""

    weekly_capacity = _weekly_capacity_minutes(goal)
    period_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    task_allocations: dict[str, dict[str, int | float]] = {}
    allocation_issues: list[str] = []
    for task in blueprint.tasks:
        allocations, issues = _task_period_allocations(task)
        allocation_issues.extend(issues)
        task_allocations[task.id] = {
            period: _render_decimal(minutes)
            for period, minutes in sorted(allocations.items())
        }
        for period, minutes in allocations.items():
            period_totals[period] += minutes

    rendered_totals = {
        period: _render_decimal(minutes)
        for period, minutes in sorted(period_totals.items())
    }
    period_slack = (
        {
            period: _render_decimal(Decimal(weekly_capacity) - minutes)
            for period, minutes in sorted(period_totals.items())
        }
        if weekly_capacity is not None
        else {}
    )
    return {
        "maxTopLevelTasks": _MAX_TOP_LEVEL_EXECUTION_TASKS,
        "weeklyCapacityMinutes": weekly_capacity,
        "periodTotals": rendered_totals,
        "periodSlack": period_slack,
        "taskAllocations": task_allocations,
        "allocationIssues": list(dict.fromkeys(allocation_issues)),
        "dependencyScheduleViolations": _dependency_schedule_violations(blueprint),
    }


def validate_execution_preflight(
    blueprint: ExecutionBlueprint,
    *,
    goal: UserGoalModel,
    reality: RealityAssessment | None = None,
) -> None:
    """Validate only typed or reliably calculable facts before persistence."""

    issues: list[str] = []
    try:
        validate_execution_invariants(blueprint)
    except DeterministicGuardError as exc:
        issues.extend(exc.issues)

    issues.extend(
        item["reason"] for item in _dependency_schedule_violations(blueprint)
    )

    if reality is not None and not reality.can_proceed_to_evidence:
        issues.append("Reality assessment does not authorize downstream execution design")

    spending_limit = _explicit_spending_limit_cny(goal)
    if spending_limit is not None:
        if blueprint.budget_summary is None:
            issues.append(f"explicit CNY spending cap {spending_limit} requires budgetSummary")
        elif blueprint.budget_summary.spending_limit_cny != spending_limit:
            issues.append(
                "budgetSummary.spendingLimitCny must equal the explicit Goal spending cap "
                f"{spending_limit}"
            )

    weekly_capacity = _weekly_capacity_minutes(goal)
    if weekly_capacity is not None:
        period_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
        for task in blueprint.tasks:
            allocations, allocation_issues = _task_period_allocations(task)
            issues.extend(allocation_issues)
            for period, minutes in allocations.items():
                period_totals[period] += minutes
        for period, minutes in sorted(period_totals.items()):
            if minutes > weekly_capacity:
                rendered = int(minutes) if minutes == minutes.to_integral_value() else float(minutes)
                issues.append(
                    f"{period} workload {rendered} minutes exceeds explicit weekly capacity "
                    f"{weekly_capacity} minutes"
                )

    if issues:
        raise DeterministicGuardError(list(dict.fromkeys(issues)))


def calendar_write_allowed(
    *,
    planning_mode: str,
    critique: PlanCritiqueReport | None,
    strategy_approved: bool,
    execution_approved: bool,
) -> bool:
    return bool(
        planning_mode == "model_backed"
        and critique
        and critique.status == "passed"
        and meets_critic_score_gate(critique)
        and critique.calendar_writable
        and not any(item.severity == "blocker" for item in critique.issues)
        and not critique.repair_requests
        and strategy_approved
        and execution_approved
    )
