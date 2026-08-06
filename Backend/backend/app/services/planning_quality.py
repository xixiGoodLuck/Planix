from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
import re
from typing import Any

from ..schemas import (
    LocalRelevance,
    PlanDensityPolicy,
    PlanHorizon,
    PlanQualityIssue,
    PlanQualityMetrics,
    PlanQualityReport,
    PlanSourceType,
    StructuredGoalPlan,
)


GENERIC_WEAK_KEYWORDS = {
    "安全",
    "计划",
    "学习",
    "项目",
    "练习",
    "目标",
    "建议",
    "方法",
    "步骤",
    "安排",
    "时间",
    "基础",
    "入门",
    "预算",
    "risk",
    "plan",
    "learn",
    "learning",
    "project",
    "practice",
    "goal",
    "tips",
    "method",
    "steps",
    "schedule",
    "time",
    "basic",
    "beginner",
}

WEAK_TASK_TITLES = {
    "继续学习",
    "保持练习",
    "复习内容",
    "完成任务",
    "进行准备",
    "提升能力",
    "了解相关知识",
}

LONG_TERM_HINTS = (
    "考研",
    "实习准备",
    "实习",
    "学 python",
    "学习 python",
    "python",
    "减肥",
    "毕业设计",
    "项目开发",
    "作品集",
    "portfolio",
    "internship",
)


def detect_plan_horizon(goal: str, date: str, deadline: str = "") -> PlanHorizon:
    start = _parse_date_or_today(date)
    explicit_days = _explicit_duration_days(goal)
    deadline_days = _deadline_duration_days(start, deadline)
    if explicit_days:
        duration_days = explicit_days
    elif deadline_days:
        duration_days = deadline_days
    elif _looks_long_term(goal):
        duration_days = 30
    else:
        duration_days = 14

    duration_days = max(1, min(duration_days, 3650))
    policy_values = _policy_values(duration_days)
    end = start + timedelta(days=duration_days)
    return PlanHorizon(
        rawText=goal,
        durationDays=duration_days,
        horizonType=_horizon_type(duration_days),
        startDate=start.isoformat(),
        endDate=end.isoformat(),
        expectedMilestoneCount=policy_values["min_milestones"],
        expectedMinTaskCount=policy_values["min_total_tasks"],
        expectedWeekCount=policy_values["min_covered_weeks"],
    )


def build_density_policy(horizon: PlanHorizon) -> PlanDensityPolicy:
    values = _policy_values(horizon.duration_days)
    return PlanDensityPolicy(
        durationDays=horizon.duration_days,
        minMilestones=values["min_milestones"],
        maxMilestones=values["max_milestones"],
        minTotalTasks=values["min_total_tasks"],
        maxTotalTasks=values["max_total_tasks"],
        minTasksPerMilestone=values["min_tasks_per_milestone"],
        requireWeeklyCoverage=values["require_weekly_coverage"],
        minCoveredWeeks=values["min_covered_weeks"],
        firstDaysDetail=min(14, horizon.duration_days),
    )


def validate_structured_plan_quality(
    structured_plan: dict[str, Any] | StructuredGoalPlan,
    horizon: PlanHorizon,
    policy: PlanDensityPolicy,
) -> PlanQualityReport:
    plan = _plan_dict(structured_plan)
    milestones = [item for item in plan.get("milestones") or [] if isinstance(item, dict)]
    tasks = _task_items(milestones)
    total_tasks = len([task for task in tasks if _clean_title(task.get("title"))])
    milestone_count = len(milestones)
    start = date_type.fromisoformat(horizon.start_date)
    end = date_type.fromisoformat(horizon.end_date)
    dates: list[date_type] = []
    missing_due_dates = 0
    out_of_range_dates = 0
    empty_titles = 0
    weak_titles = 0

    for task in tasks:
        title = _clean_title(task.get("title"))
        if not title:
            empty_titles += 1
        elif _is_weak_title(title):
            weak_titles += 1
        due = _parse_due_date(task.get("dueDate") or task.get("due_date"))
        if not due:
            missing_due_dates += 1
            continue
        if due < start or due > end:
            out_of_range_dates += 1
            continue
        dates.append(due)

    covered_week_count = len({((due - start).days // 7) + 1 for due in dates})
    date_span_days = (max(dates) - min(dates)).days + 1 if dates else 0
    issues: list[PlanQualityIssue] = []

    def add(code: str, message: str, severity: str) -> None:
        issues.append(PlanQualityIssue(code=code, message=message, severity=severity))  # type: ignore[arg-type]

    if milestone_count == 0:
        add("missing_milestones", "计划没有可用阶段。", "error")
    elif milestone_count < policy.min_milestones:
        add("too_few_milestones", "计划阶段数量低于当前周期的最低要求。", "error")
    elif milestone_count > policy.max_milestones:
        add("too_many_milestones", "计划阶段过多，可能不利于长期阅读。", "warning")

    if total_tasks == 0:
        add("missing_tasks", "计划没有有效任务标题。", "error")
    elif total_tasks < policy.min_total_tasks:
        add("too_few_tasks", "计划任务数量低于当前周期的最低要求。", "error")
    elif total_tasks > policy.max_total_tasks:
        add("too_many_tasks", "计划任务数量偏多，建议避免机械铺满每天。", "warning")

    if empty_titles:
        add("empty_task_title", "计划中存在空标题任务。", "error")

    if policy.require_weekly_coverage and covered_week_count < policy.min_covered_weeks:
        add("insufficient_weekly_coverage", "任务日期没有覆盖足够多的周推进单元。", "error")

    if horizon.duration_days >= 60 and dates and date_span_days <= 7:
        add("date_span_too_short", "长期计划的任务日期只覆盖了第一周。", "error")

    if missing_due_dates:
        severity = "error" if horizon.duration_days >= 30 and missing_due_dates >= max(2, total_tasks // 2) else "warning"
        add("missing_due_dates", "部分任务缺少 dueDate，长期计划会难以写入日历。", severity)

    if out_of_range_dates:
        severity = "error" if out_of_range_dates > max(1, total_tasks // 4) else "warning"
        add("out_of_range_due_dates", "部分任务 dueDate 超出识别出的规划周期。", severity)

    if weak_titles:
        severity = "error" if weak_titles >= max(1, total_tasks // 2) else "warning"
        add("weak_task_titles", "部分任务标题过于空泛，需要更具体的可执行动作。", severity)

    score = 100
    for issue in issues:
        score -= 18 if issue.severity == "error" else 6
    score = max(0, min(100, score))
    ok = not any(issue.severity == "error" for issue in issues)
    return PlanQualityReport(
        ok=ok,
        score=score,
        totalTasks=total_tasks,
        milestoneCount=milestone_count,
        coveredWeekCount=covered_week_count,
        dateSpanDays=date_span_days,
        issues=issues,
        metrics=PlanQualityMetrics(
            durationDays=horizon.duration_days,
            totalTasks=total_tasks,
            milestoneCount=milestone_count,
            coveredWeekCount=covered_week_count,
            dateSpanDays=date_span_days,
            weakTaskCount=weak_titles,
            missingDueDateCount=missing_due_dates,
            outOfRangeDueDateCount=out_of_range_dates,
        ),
    )


def with_demo_quality_metrics(
    report: PlanQualityReport,
    *,
    horizon: PlanHorizon,
    quality_status: str | None,
    source_type: str | None,
    local_relevance: str | None,
    repair_attempted: bool,
    fallback_used: bool,
) -> PlanQualityReport:
    base = report.metrics or PlanQualityMetrics()
    metrics = base.model_copy(
        update={
            "duration_days": base.duration_days or horizon.duration_days,
            "total_tasks": base.total_tasks if base.total_tasks is not None else report.total_tasks,
            "milestone_count": base.milestone_count if base.milestone_count is not None else report.milestone_count,
            "covered_week_count": base.covered_week_count if base.covered_week_count is not None else report.covered_week_count,
            "date_span_days": base.date_span_days if base.date_span_days is not None else report.date_span_days,
            "repair_attempted": repair_attempted,
            "fallback_used": fallback_used,
            "quality_status": quality_status,
            "source_type": source_type,
            "local_relevance": local_relevance,
        }
    )
    return report.model_copy(update={"metrics": metrics})


def assess_local_source_grounding(goal: str, local_sources: list[dict[str, Any]]) -> dict[str, Any]:
    keywords = _goal_keyword_groups(goal)
    core_keywords = keywords["coreKeywords"]
    expanded_keywords = keywords["expandedKeywords"]
    matched_keywords: list[str] = []
    relevant_source_count = 0
    core_keyword_hits = 0
    strong_expanded_hits = 0

    for source in local_sources:
        matches = _source_keyword_matches(source, core_keywords, expanded_keywords)
        matched_keywords.extend(matches["matched"])
        core_keyword_hits += len(matches["core"])
        strong_expanded_hits += len(matches["strongExpanded"])
        if matches["isRelevant"]:
            relevant_source_count += 1

    matched_keywords = _dedupe(matched_keywords)
    missing_keywords = [
        keyword
        for keyword in [*core_keywords, *expanded_keywords]
        if keyword not in matched_keywords and keyword not in GENERIC_WEAK_KEYWORDS
    ][:8]
    local_source_count = len(local_sources)
    if local_source_count == 0 or (core_keyword_hits == 0 and strong_expanded_hits == 0):
        local_relevance: LocalRelevance = "low"
    elif relevant_source_count >= 2 and core_keyword_hits >= 1:
        local_relevance = "high"
    else:
        local_relevance = "medium"

    source_type: PlanSourceType = "local_context" if local_relevance in {"high", "medium"} else "insufficient_context"
    return {
        "localRelevance": local_relevance,
        "sourceType": source_type,
        "localSourceCount": local_source_count,
        "relevantSourceCount": relevant_source_count,
        "coreKeywordHits": core_keyword_hits,
        "strongExpandedHits": strong_expanded_hits,
        "matchedKeywords": matched_keywords[:12],
        "missingKeywords": missing_keywords,
    }


def _policy_values(duration_days: int) -> dict[str, int | bool]:
    if duration_days <= 7:
        return {
            "min_milestones": 1,
            "max_milestones": 2,
            "min_total_tasks": 3,
            "max_total_tasks": 10,
            "min_tasks_per_milestone": 2,
            "require_weekly_coverage": False,
            "min_covered_weeks": 1,
        }
    if duration_days <= 14:
        return {
            "min_milestones": 2,
            "max_milestones": 3,
            "min_total_tasks": 8,
            "max_total_tasks": 16,
            "min_tasks_per_milestone": 3,
            "require_weekly_coverage": True,
            "min_covered_weeks": 2,
        }
    if duration_days <= 30:
        return {
            "min_milestones": 4,
            "max_milestones": 5,
            "min_total_tasks": 12,
            "max_total_tasks": 28,
            "min_tasks_per_milestone": 3,
            "require_weekly_coverage": True,
            "min_covered_weeks": 4,
        }
    if duration_days <= 90:
        return {
            "min_milestones": 3,
            "max_milestones": 12,
            "min_total_tasks": 24,
            "max_total_tasks": 48,
            "min_tasks_per_milestone": 4,
            "require_weekly_coverage": True,
            "min_covered_weeks": 10,
        }
    if duration_days <= 120:
        return {
            "min_milestones": 3,
            "max_milestones": 12,
            "min_total_tasks": 24,
            "max_total_tasks": 54,
            "min_tasks_per_milestone": 4,
            "require_weekly_coverage": True,
            "min_covered_weeks": 10,
        }
    return {
        "min_milestones": 6,
        "max_milestones": 12,
        "min_total_tasks": 36,
        "max_total_tasks": 72,
        "min_tasks_per_milestone": 4,
        "require_weekly_coverage": True,
        "min_covered_weeks": 18,
    }


def _explicit_duration_days(text: str) -> int | None:
    value = str(text or "").lower()
    if re.search(r"(半年|六个月|6\s*个月|six\s+months|half\s+a?\s*year)", value):
        return 180
    month_match = re.search(r"(\d+)\s*(个月|月|months?|mos?)", value)
    if month_match:
        return int(month_match.group(1)) * 30
    chinese_months = {
        "一个月": 30,
        "一月": 30,
        "两个月": 60,
        "二个月": 60,
        "三个月": 90,
        "四个月": 120,
        "五个月": 150,
        "六个月": 180,
    }
    for token, days in sorted(chinese_months.items(), key=lambda item: -len(item[0])):
        if token in value:
            return days
    english_months = {"one": 30, "two": 60, "three": 90, "four": 120, "five": 150, "six": 180}
    for token, days in english_months.items():
        if re.search(rf"\b{token}\s+months?\b", value):
            return days
    day_match = re.search(r"(\d+)\s*(天|日|days?|d)", value)
    if day_match:
        return int(day_match.group(1))
    week_match = re.search(r"(\d+)\s*(周|星期|weeks?|w)", value)
    if week_match:
        return int(week_match.group(1)) * 7
    if re.search(r"(两周|二周|两个星期|two\s+weeks)", value):
        return 14
    if re.search(r"(本周|一周|一个星期|7天|one\s+week)", value):
        return 7
    if re.search(r"(今天|一天|1天|one\s+day)", value):
        return 1
    return None


def _deadline_duration_days(start: date_type, deadline: str) -> int | None:
    try:
        deadline_date = date_type.fromisoformat(str(deadline or ""))
    except ValueError:
        return None
    if deadline_date <= start:
        return None
    return min((deadline_date - start).days, 3650)


def _parse_date_or_today(value: str) -> date_type:
    try:
        return date_type.fromisoformat(str(value or ""))
    except ValueError:
        return date_type.today()


def _horizon_type(duration_days: int) -> str:
    if duration_days <= 7:
        return "daily"
    if duration_days <= 28:
        return "weekly"
    if duration_days <= 60:
        return "monthly"
    if duration_days <= 120:
        return "quarterly"
    return "long_term"


def _looks_long_term(goal: str) -> bool:
    value = str(goal or "").lower()
    return any(token in value for token in LONG_TERM_HINTS)


def _plan_dict(plan: dict[str, Any] | StructuredGoalPlan) -> dict[str, Any]:
    if isinstance(plan, StructuredGoalPlan):
        return plan.model_dump(by_alias=True)
    return plan if isinstance(plan, dict) else {}


def _task_items(milestones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for milestone in milestones:
        raw_tasks = milestone.get("tasks")
        if isinstance(raw_tasks, list):
            tasks.extend([task for task in raw_tasks if isinstance(task, dict)])
    return tasks


def _clean_title(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _is_weak_title(title: str) -> bool:
    normalized = _clean_title(title).strip("：:，,。.;；-—_ ")
    return normalized in WEAK_TASK_TITLES


def _parse_due_date(value: object) -> date_type | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date_type.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _goal_keyword_groups(goal: str) -> dict[str, list[str]]:
    text = str(goal or "")
    lower = text.lower()
    core: list[str] = []
    expanded: list[str] = []
    if any(token in text for token in ("新疆", "喀纳斯", "伊犁", "乌鲁木齐", "吐鲁番")):
        core.append("新疆")
    if any(token in text for token in ("旅游", "旅行", "出游", "行程", "景点")) or "travel" in lower:
        core.append("旅游")
        expanded.extend(["行程", "景点", "交通", "住宿", "预算", "安全"])
    if any(token in text for token in ("游泳", "蛙泳", "漂浮", "换气", "水性")):
        core.append("游泳")
        expanded.extend(["漂浮", "换气", "水性", "水性练习", "蛙泳", "安全"])
    if "python" in lower:
        core.append("python")
        expanded.extend(["编程", "代码", "项目", "练习", "基础语法", "数据处理"])
    if "化学" in text:
        core.append("化学")
        expanded.extend(["元素", "反应", "实验", "方程式", "有机", "无机", "酸碱", "分子", "原子"])
    if "考研" in text or "英语" in text:
        core.append("考研" if "考研" in text else "英语")
        expanded.extend(["词汇", "阅读", "写作", "真题", "复习"])
    if "ai" in lower or "人工智能" in text or "实习" in text or "agent" in lower:
        if "ai" in lower:
            core.append("ai")
        if "人工智能" in text:
            core.append("人工智能")
        if "实习" in text:
            core.append("实习")
        if "agent" in lower:
            core.append("agent")
        expanded.extend(["AI 应用", "实习准备", "项目作品集", "RAG", "Agent"])
    english_terms = [item for item in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,}", lower) if len(item) >= 2]
    core.extend(term for term in english_terms if term not in GENERIC_WEAK_KEYWORDS)
    if not core:
        core.extend(_fallback_goal_core_keywords(text))
    return {"coreKeywords": _dedupe(core), "expandedKeywords": _dedupe(expanded)}


def _fallback_goal_core_keywords(text: str) -> list[str]:
    cleaned = _clean_text(text)
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}", cleaned)
    stop_parts = ("我要", "我想", "帮我", "请", "制定", "规划", "学习", "学会", "一个", "计划")
    result: list[str] = []
    for chunk in chunks:
        value = chunk
        for stop in stop_parts:
            value = value.replace(stop, "")
        if len(value) >= 2 and value not in GENERIC_WEAK_KEYWORDS:
            result.append(value[:12])
    return result[:3] or ([cleaned[:16]] if cleaned else [])


def _source_keyword_matches(source: dict[str, Any], core_keywords: list[str], expanded_keywords: list[str]) -> dict[str, Any]:
    text = _source_search_text(source)
    core_matches = [keyword for keyword in core_keywords if _keyword_in_text(keyword, text)]
    expanded_matches = [keyword for keyword in expanded_keywords if _keyword_in_text(keyword, text)]
    weak_matches = [keyword for keyword in GENERIC_WEAK_KEYWORDS if _keyword_in_text(keyword, text)]
    strong_expanded = [keyword for keyword in expanded_matches if keyword not in GENERIC_WEAK_KEYWORDS]
    return {
        "core": _dedupe(core_matches),
        "strongExpanded": _dedupe(strong_expanded),
        "matched": _dedupe([*core_matches, *strong_expanded, *weak_matches]),
        "isRelevant": bool(core_matches) or len(strong_expanded) >= 2,
    }


def _source_search_text(source: dict[str, Any]) -> str:
    values = [
        source.get("title"),
        source.get("summary"),
        source.get("chunk"),
        source.get("snippet"),
        source.get("content"),
        source.get("description"),
    ]
    return _clean_text(" ".join(str(value or "") for value in values)).lower()


def _keyword_in_text(keyword: str, text: str) -> bool:
    value = str(keyword or "").strip().lower()
    return bool(value and value in text)


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    cleaned = re.sub(r"[#>*_`\[\]{}()]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
