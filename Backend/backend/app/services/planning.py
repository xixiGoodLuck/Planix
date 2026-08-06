import json
import os
import re
from datetime import date as date_type
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import ValidationError

from ..db import get_conn, load_memory
from ..errors import bad_request
from ..schemas import (
    DailyReviewOut,
    DailyReviewRequest,
    GoalPlanOut,
    GoalPlanRequest,
    LearningResource,
    MemoryCreate,
    ModelUsage,
    PhaseItem,
    PlanFitCheck,
    PlanCreate,
    PlanDensityPolicy,
    PlanHorizon,
    PlanQualityReport,
    PlanOut,
    PlannerTask,
    RefinePlanContext,
    RefinedTask,
    RefineTaskRequest,
    ReplanApplyRequest,
    ReplanTask,
    StructuredGoalPlan,
    TimeBlock,
)
from .llm import LlmClient
from .memory_store import MemoryService
from .planner import _json_object
from .plans import create_plan, list_plans
from .planning_quality import (
    assess_local_source_grounding,
    build_density_policy,
    detect_plan_horizon,
    validate_structured_plan_quality,
    with_demo_quality_metrics,
)
from .rag import RagService
from .structured_goal_plan import (
    build_local_structured_plan,
    derive_phase_items,
    derive_planner_tasks,
    normalize_structured_plan,
    render_goal_plan_markdown,
)


GOAL_PLAN_MIN_LLM_TIMEOUT_SECONDS = 55
GOAL_PLAN_MAX_LLM_TIMEOUT_SECONDS = 65
GOAL_PLAN_DEFAULT_MAX_TOKENS = 4096
GOAL_PLAN_MAX_TOKEN_LIMIT = 8000
GOAL_PLAN_MAX_TOKENS_ENV = "PLANIX_GOAL_PLAN_MAX_TOKENS"
COMMAND_DRAFT_SOURCE_KEY_RE = re.compile(r"^command-draft:([^:]+):m(\d+):t(\d+)$")
SKIING_DOMAIN_TERMS = (
    "滑雪",
    "雪板",
    "雪鞋",
    "雪道",
    "犁式",
    "刹车",
    "平行转弯",
    "snowplow",
    "ski",
    "skiing",
    "skis",
    "slope",
)
YOGA_CONFLICT_TERMS = (
    "瑜伽",
    "山式",
    "树式",
    "tadasana",
    "vrksasana",
    "asana",
    "yoga",
)


def _load_preference_memory() -> str:
    item = MemoryService().get_by_source_key("preference", "preferences:local-user")
    if item and item.content.strip():
        return item.content
    return load_memory()


def _parse_date(value: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise bad_request("date must use YYYY-MM-DD format") from exc


def _next_day(value: str) -> str:
    return (_parse_date(value) + timedelta(days=1)).isoformat()


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_list(raw: str) -> list:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _normalize_phase_items(items: object) -> list[PhaseItem]:
    if not isinstance(items, list):
        return []
    phases = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if title:
            phases.append(PhaseItem(title=title, detail=detail))
    return phases


def _normalize_planner_tasks(items: object) -> list[PlannerTask]:
    if not isinstance(items, list):
        return []
    tasks = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("text") or "").strip()
        if not title:
            continue
        tasks.append(
            PlannerTask(
                time=str(item.get("time") or "09:00")[:5],
                title=title,
                reason=str(item.get("reason") or "This task supports the current goal."),
            )
        )
    return tasks


def _normalize_suggestions(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items[:6] if str(item).strip()]


def _normalize_replan_tasks(items: object, target_date: str) -> list[ReplanTask]:
    if not isinstance(items, list):
        return []
    tasks = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("text") or "").strip()
        if not title:
            continue
        tasks.append(
            ReplanTask(
                targetDate=str(item.get("targetDate") or item.get("target_date") or target_date),
                time=str(item.get("time") or "09:00")[:5],
                title=title,
                reason=str(item.get("reason") or "Replanned from unfinished work."),
                sourcePlanId=item.get("sourcePlanId") or item.get("source_plan_id"),
            )
        )
    return tasks


def _structured_from_parsed(
    parsed: dict[str, object] | None,
    fallback: StructuredGoalPlan,
) -> StructuredGoalPlan:
    if not parsed:
        return fallback
    raw = parsed.get("structuredPlan") or parsed.get("structured_plan")
    if raw is None and "goalTitle" in parsed and "milestones" in parsed:
        raw = parsed
    return normalize_structured_plan(raw, fallback)


def _base_url_host(base_url: str) -> str:
    try:
        return urlparse(base_url).netloc or ""
    except Exception:
        return ""


def _safe_goal_error_type(error_type: str | None) -> str | None:
    if not error_type:
        return None
    allowed = {
        "auth_error",
        "bad_model",
        "bad_base_url",
        "network_error",
        "timeout",
        "insufficient_balance",
        "invalid_key_format",
        "invalid_model_output",
        "model_output_truncated",
        "empty_content",
        "plan_quality_failed",
        "unknown",
    }
    return error_type if error_type in allowed else "unknown"


def _detect_output_language(*values: str) -> str:
    text = " ".join(value or "" for value in values)
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return "zh-CN" if cjk_count >= 2 else "en-US"


def _goal_plan_timeout(settings_timeout_seconds: int) -> int:
    return min(max(settings_timeout_seconds, GOAL_PLAN_MIN_LLM_TIMEOUT_SECONDS), GOAL_PLAN_MAX_LLM_TIMEOUT_SECONDS)


def _goal_plan_max_tokens() -> int:
    raw = os.getenv(GOAL_PLAN_MAX_TOKENS_ENV, "").strip()
    if not raw:
        return GOAL_PLAN_DEFAULT_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return GOAL_PLAN_DEFAULT_MAX_TOKENS
    if value <= 0:
        return GOAL_PLAN_DEFAULT_MAX_TOKENS
    return min(value, GOAL_PLAN_MAX_TOKEN_LIMIT)


def _plan_to_dict(plan: PlanOut) -> dict[str, object]:
    return plan.model_dump(by_alias=True)


def _positive_minutes(value: object, fallback: int = 60) -> int:
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return minutes if minutes > 0 else fallback


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


OFFICIAL_RESOURCE_DOMAINS = {
    "docs.python.org",
    "pypi.org",
    "flask.palletsprojects.com",
    "pandas.pydata.org",
    "numpy.org",
    "requests.readthedocs.io",
    "beautiful-soup-4.readthedocs.io",
    "fastapi.tiangolo.com",
    "sqlalchemy.org",
    "sqlite.org",
    "developer.mozilla.org",
}


def _model_usage_from_result(result, task_type: str) -> ModelUsage:
    usage = result.usage or {}
    return ModelUsage(
        provider=result.provider,
        model=result.model,
        promptTokens=usage.get("promptTokens"),
        completionTokens=usage.get("completionTokens"),
        totalTokens=usage.get("totalTokens"),
        latencyMs=result.latency_ms,
        mode="llm",
        taskType=task_type,
        fallbackUsed=getattr(result, "fallback_used", None),
        localFallbackAllowed=getattr(result, "local_fallback_allowed", None),
        attempts=getattr(result, "attempts", None) or [],
    )


def _local_model_usage(client: LlmClient, task_type: str, error: object | None = None) -> ModelUsage:
    attempts = getattr(error, "attempts", None) or []
    return ModelUsage(
        provider=client.settings.provider,
        model=client.settings.model,
        mode="local_fallback",
        taskType=task_type,
        fallbackUsed=True if attempts else None,
        localFallbackAllowed=getattr(error, "local_fallback_allowed", None),
        attempts=attempts,
    )


def _clean_text(value: object, max_length: int = 500) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _record(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _task_brief(task: dict[str, object], milestone_title: str = "") -> dict[str, object]:
    return {
        "title": _clean_text(task.get("title"), 120),
        "description": _clean_text(task.get("description"), 220),
        "dueDate": task.get("dueDate") or task.get("due_date"),
        "priority": task.get("priority"),
        "estimatedMinutes": task.get("estimatedMinutes") or task.get("estimated_minutes"),
        "milestoneTitle": milestone_title,
    }


def build_refine_plan_context(
    structured_plan: object,
    *,
    milestone_index: int = 0,
    task_index: int = 0,
    sources: list[dict[str, object]] | None = None,
    plan_horizon: dict[str, object] | None = None,
    quality_status: str | None = None,
    daily_learning_minutes: int | None = None,
) -> dict[str, object]:
    plan = _record(structured_plan)
    milestones = [item for item in plan.get("milestones") or [] if isinstance(item, dict)]
    selected_milestone = milestones[milestone_index] if 0 <= milestone_index < len(milestones) else {}
    milestone_title = _clean_text(selected_milestone.get("title"), 120) if selected_milestone else ""
    milestone_description = _clean_text(selected_milestone.get("description"), 220) if selected_milestone else ""
    milestone_tasks = [item for item in selected_milestone.get("tasks") or [] if isinstance(item, dict)] if selected_milestone else []
    selected_task = milestone_tasks[task_index] if 0 <= task_index < len(milestone_tasks) else {}

    flattened: list[tuple[int, int, str, dict[str, object]]] = []
    for m_index, milestone in enumerate(milestones):
        m_title = _clean_text(milestone.get("title"), 120)
        for t_index, task in enumerate(milestone.get("tasks") or []):
            if isinstance(task, dict):
                flattened.append((m_index, t_index, m_title, task))
    selected_flat_index = next(
        (index for index, (m_index, t_index, _title, _task) in enumerate(flattened) if m_index == milestone_index and t_index == task_index),
        -1,
    )
    previous_task = None
    next_task = None
    if selected_flat_index > 0:
        _m_index, _t_index, previous_milestone, previous = flattened[selected_flat_index - 1]
        previous_task = _task_brief(previous, previous_milestone)
    if 0 <= selected_flat_index < len(flattened) - 1:
        _m_index, _t_index, next_milestone, next_item = flattened[selected_flat_index + 1]
        next_task = _task_brief(next_item, next_milestone)

    source_summaries = []
    for source in (sources or [])[:4]:
        if not isinstance(source, dict):
            continue
        source_summaries.append(
            {
                "title": _clean_text(source.get("title"), 120),
                "chunk": _clean_text(source.get("chunk") or source.get("summary"), 240),
                "sourceType": source.get("sourceType") or source.get("source_type"),
                "url": source.get("url"),
            }
        )

    current_task = _task_brief(selected_task, milestone_title)
    if daily_learning_minutes is None:
        daily_learning_minutes = _positive_minutes(current_task.get("estimatedMinutes"), 0) or None
    return {
        "planTitle": _clean_text(plan.get("goalTitle") or plan.get("goal_title"), 160),
        "planSummary": _clean_text(plan.get("goalDescription") or plan.get("goal_description"), 360),
        "durationDays": plan.get("durationDays") or plan.get("duration_days") or (plan_horizon or {}).get("durationDays"),
        "qualityStatus": quality_status,
        "dailyLearningMinutes": daily_learning_minutes,
        "currentMilestone": {
            "title": milestone_title,
            "description": milestone_description,
            "index": milestone_index,
        },
        "currentTask": {
            **current_task,
            "index": task_index,
        },
        "previousTask": previous_task,
        "nextTask": next_task,
        "sameMilestoneTasks": [_clean_text(task.get("title"), 120) for task in milestone_tasks[:12] if _clean_text(task.get("title"), 120)],
        "sources": source_summaries,
    }


def resolve_refine_plan_context_from_source_key(
    source_key: str,
    *,
    daily_learning_minutes: int | None = None,
) -> dict[str, object] | None:
    match = COMMAND_DRAFT_SOURCE_KEY_RE.match((source_key or "").strip())
    if not match:
        return None
    draft_id, milestone_index_raw, task_index_raw = match.groups()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload_json
            FROM command_drafts
            WHERE id = ? AND kind = 'calendar_plan'
            LIMIT 1
            """,
            (draft_id,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    structured_plan = payload.get("structuredPlan")
    if not isinstance(structured_plan, dict):
        return None
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    plan_horizon = payload.get("planHorizon") if isinstance(payload.get("planHorizon"), dict) else None
    return build_refine_plan_context(
        structured_plan,
        milestone_index=int(milestone_index_raw),
        task_index=int(task_index_raw),
        sources=sources,
        plan_horizon=plan_horizon,
        quality_status=str(payload.get("qualityStatus") or "") or None,
        daily_learning_minutes=daily_learning_minutes,
    )


def _refine_payload_with_source_context(payload: RefineTaskRequest) -> RefineTaskRequest:
    source_context = resolve_refine_plan_context_from_source_key(
        payload.source_key,
        daily_learning_minutes=payload.available_minutes,
    )
    if not source_context:
        return payload
    current_task = _record(source_context.get("currentTask"))
    task_description = _clean_text(current_task.get("description"), 500) or payload.task_description
    goal = _clean_text(source_context.get("planTitle"), 180) or payload.goal
    return payload.model_copy(
        update={
            "goal": goal,
            "task_description": task_description,
            "plan_context": RefinePlanContext.model_validate(source_context),
        }
    )


def _context_dict(payload: RefineTaskRequest) -> dict[str, object]:
    if not payload.plan_context:
        return {}
    return payload.plan_context.model_dump(by_alias=True)


def _refine_context_text(payload: RefineTaskRequest) -> str:
    context = _context_dict(payload)
    return " ".join(
        [
            payload.goal or "",
            payload.task_title or "",
            payload.task_description or "",
            _dump(context) if context else "",
        ]
    ).lower()


def _refined_task_text(refined: RefinedTask) -> str:
    return _dump(refined.model_dump(by_alias=True)).lower()


def has_refinement_domain_drift(refined: RefinedTask, payload: RefineTaskRequest) -> bool:
    context_text = _refine_context_text(payload)
    output_text = _refined_task_text(refined)
    if any(term in context_text for term in SKIING_DOMAIN_TERMS):
        return any(term in output_text for term in YOGA_CONFLICT_TERMS)
    return False


def _minutes_from_text(*values: str) -> int | None:
    text = " ".join(value or "" for value in values)
    minute_match = re.search(r"(\d{1,4})\s*(?:分钟|分|min|mins|minute|minutes|m\b)", text, re.I)
    if minute_match:
        return max(1, int(minute_match.group(1)))
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|hour|hours|h\b)", text, re.I)
    if hour_match:
        return max(1, round(float(hour_match.group(1)) * 60))
    return None


def _refine_budget(payload: RefineTaskRequest) -> tuple[int, str]:
    context = _context_dict(payload)
    current_task = _record(context.get("currentTask"))
    context_minutes = _positive_minutes(current_task.get("estimatedMinutes") or current_task.get("estimated_minutes"), 0)
    if context_minutes:
        return context_minutes, "current_task_estimate"
    available_minutes = _positive_minutes(payload.available_minutes, 0)
    if available_minutes:
        return available_minutes, "available_minutes"
    daily_minutes = _positive_minutes(context.get("dailyLearningMinutes") or context.get("daily_learning_minutes"), 0)
    if daily_minutes:
        return daily_minutes, "daily_learning_minutes"
    text_minutes = _minutes_from_text(payload.refinement_instruction, payload.goal, payload.task_description)
    if text_minutes:
        return text_minutes, "user_instruction"
    return 60, "default"


def _budget_explanation(minutes: int, source: str, output_language: str) -> str:
    if output_language == "en":
        labels = {
            "current_task_estimate": "the current task estimate",
            "available_minutes": "the available minutes passed by the caller",
            "daily_learning_minutes": "the daily learning budget in the plan context",
            "user_instruction": "the time budget mentioned in your instruction",
            "default": "the default refinement budget",
        }
        return f"Time budget: {minutes} minutes, based on {labels.get(source, 'the task context')}."
    labels = {
        "current_task_estimate": "当前任务估时",
        "available_minutes": "调用方传入的可用时间",
        "daily_learning_minutes": "计划上下文中的每日学习预算",
        "user_instruction": "你的指令中提到的时间预算",
        "default": "默认细化预算",
    }
    return f"时间预算：{minutes} 分钟，来自{labels.get(source, '任务上下文')}。"


def _duration_chunks(minutes: int) -> list[int]:
    minutes = max(1, minutes)
    if minutes <= 30:
        return [minutes]
    if minutes == 40:
        return [20, 20]
    chunks: list[int] = []
    remaining = minutes
    while remaining > 30:
        chunks.append(30)
        remaining -= 30
    if remaining:
        chunks.append(remaining)
    return chunks


def _default_time_blocks(minutes: int, task_title: str, output_language: str) -> list[TimeBlock]:
    chunks = _duration_chunks(minutes)
    zh_titles = ["定位资料与目标", "跟做最小示例", "独立练习", "项目整合", "记录验收"]
    zh_actions = [
        f"围绕“{task_title}”确认今天要学的概念、官方资料入口和产出物。",
        "跟着官方文档或权威资料完成一个最小可运行示例。",
        "不看答案独立完成一组小练习，并记录报错或卡点。",
        "把练习合并成一个可保存的小脚本、笔记或项目片段。",
        "复盘完成内容，写下验收结果和下一步问题。",
    ]
    en_titles = ["Orient", "Follow an official example", "Practice independently", "Integrate output", "Review"]
    en_actions = [
        f"Clarify the concept, resource, and visible output for {task_title}.",
        "Follow one official or authoritative example until it runs locally.",
        "Complete a small exercise without copying the answer.",
        "Turn the work into a saved script, note, or project fragment.",
        "Record the result, questions, and the next action.",
    ]
    titles = en_titles if output_language == "en" else zh_titles
    actions = en_actions if output_language == "en" else zh_actions
    blocks = []
    for index, duration in enumerate(chunks):
        template_index = min(index, len(titles) - 1)
        blocks.append(
            TimeBlock(
                title=titles[template_index],
                durationMinutes=duration,
                action=actions[template_index],
                expectedOutput=("A saved, checkable output." if output_language == "en" else "留下一个可检查的输出物。"),
            )
        )
    return blocks


def normalize_time_blocks(raw_blocks: object, *, budget_minutes: int, task_title: str, output_language: str) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    if isinstance(raw_blocks, list):
        for index, item in enumerate(raw_blocks):
            if not isinstance(item, dict):
                continue
            duration = _positive_minutes(item.get("durationMinutes") or item.get("duration_minutes"), 0)
            if not duration:
                continue
            title = _clean_text(item.get("title"), 80) or (f"Block {index + 1}" if output_language == "en" else f"执行块 {index + 1}")
            action = _clean_text(item.get("action"), 260) or _clean_text(item.get("description"), 260) or title
            expected = _clean_text(item.get("expectedOutput") or item.get("expected_output"), 180) or None
            chunks = _duration_chunks(duration)
            for chunk_index, chunk in enumerate(chunks):
                suffix = f" {chunk_index + 1}/{len(chunks)}" if len(chunks) > 1 else ""
                blocks.append(
                    TimeBlock(
                        title=f"{title}{suffix}",
                        durationMinutes=chunk,
                        action=action,
                        expectedOutput=expected,
                    )
                )
    if not blocks:
        return _default_time_blocks(budget_minutes, task_title, output_language)

    normalized: list[TimeBlock] = []
    total = 0
    for block in blocks:
        if total >= budget_minutes:
            break
        remaining = budget_minutes - total
        duration = min(block.duration_minutes, remaining)
        if duration <= 0:
            continue
        normalized.append(
            TimeBlock(
                title=block.title,
                durationMinutes=duration,
                action=block.action,
                expectedOutput=block.expected_output,
            )
        )
        total += duration
    if total < budget_minutes:
        for block in _default_time_blocks(budget_minutes - total, task_title, output_language):
            normalized.append(block)
            total += block.duration_minutes
            if total >= budget_minutes:
                break
    return normalized


def _allowed_resource_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_RESOURCE_DOMAINS)


def _resource_type_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in {"docs.python.org", "developer.mozilla.org", "pypi.org"}:
        return "official_doc"
    return "library_doc"


def _default_learning_resources(task_title: str, output_language: str) -> list[LearningResource]:
    text = task_title.lower()
    resources: list[LearningResource] = []
    if "flask" in text:
        resources.append(LearningResource(title="Flask Documentation", type="library_doc", url="https://flask.palletsprojects.com/", reason="Official Flask documentation."))
    if "pandas" in text or "dataframe" in text:
        resources.append(LearningResource(title="pandas Documentation", type="library_doc", url="https://pandas.pydata.org/docs/", reason="Official pandas documentation."))
    if "requests" in text or "爬取" in text or "网页" in text:
        resources.append(LearningResource(title="Requests Documentation", type="library_doc", url="https://requests.readthedocs.io/", reason="Official Requests documentation."))
    if "beautifulsoup" in text or "beautiful soup" in text or "html" in text:
        resources.append(LearningResource(title="Beautiful Soup Documentation", type="library_doc", url="https://beautiful-soup-4.readthedocs.io/", reason="Authoritative Beautiful Soup documentation."))
    if "sqlite" in text or "数据库" in text:
        resources.append(LearningResource(title="sqlite3 Standard Library", type="official_doc", url="https://docs.python.org/3/library/sqlite3.html", reason="Python standard library documentation."))
    if "python" in text or not resources:
        title = "Python Official Tutorial" if output_language == "en" else "Python 官方教程"
        resources.insert(0, LearningResource(title=title, type="official_doc", url="https://docs.python.org/3/tutorial/", reason="Python 官方文档，适合作为第一学习入口。"))
    return resources[:3]


def validate_learning_resources(raw_resources: object, *, task_title: str, output_language: str) -> list[LearningResource]:
    resources: list[LearningResource] = []
    if isinstance(raw_resources, list):
        for item in raw_resources[:6]:
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"), 120) or ("Search keyword" if output_language == "en" else "搜索关键词")
            url = _clean_text(item.get("url"), 300) or None
            search_keyword = _clean_text(item.get("searchKeyword") or item.get("search_keyword"), 180) or None
            reason = _clean_text(item.get("reason"), 200) or None
            if url and _allowed_resource_url(url):
                resources.append(
                    LearningResource(
                        title=title,
                        type=_resource_type_for_url(url),  # type: ignore[arg-type]
                        url=url,
                        searchKeyword=search_keyword,
                        reason=reason,
                    )
                )
            else:
                keyword = search_keyword or title
                if url:
                    host = urlparse(url).hostname or ""
                    keyword = f"{title} {host}".strip()
                resources.append(
                    LearningResource(
                        title=title,
                        type="search_keyword",
                        url=None,
                        searchKeyword=keyword,
                        reason=reason or ("URL outside allowlist; use as a search keyword." if output_language == "en" else "链接不在 allowlist 中，已改为搜索关键词。"),
                    )
                )
    if not resources:
        resources = _default_learning_resources(task_title, output_language)
    return resources[:4]


def _plan_fit_check(parsed: dict[str, object], payload: RefineTaskRequest) -> PlanFitCheck:
    raw = parsed.get("planFitCheck") or parsed.get("plan_fit_check")
    context = _context_dict(payload)
    milestone = _record(context.get("currentMilestone"))
    if isinstance(raw, dict):
        note = _clean_text(raw.get("note"), 260)
        return PlanFitCheck(
            fitsCurrentMilestone=bool(raw.get("fitsCurrentMilestone") if "fitsCurrentMilestone" in raw else raw.get("fits_current_milestone", True)),
            advancesOverallGoal=bool(raw.get("advancesOverallGoal") if "advancesOverallGoal" in raw else raw.get("advances_overall_goal", True)),
            hasCheckableOutput=bool(raw.get("hasCheckableOutput") if "hasCheckableOutput" in raw else raw.get("has_checkable_output", True)),
            note=note or _default_plan_fit_note(payload, milestone),
        )
    return PlanFitCheck(
        fitsCurrentMilestone=True,
        advancesOverallGoal=True,
        hasCheckableOutput=True,
        note=_default_plan_fit_note(payload, milestone),
    )


def _default_plan_fit_note(payload: RefineTaskRequest, milestone: dict[str, object]) -> str:
    milestone_title = _clean_text(milestone.get("title"), 120)
    if payload.output_language == "en":
        if milestone_title:
            return f"This refinement supports the current milestone ({milestone_title}), advances the overall plan, and leaves a checkable output."
        return "This refinement advances the overall plan and leaves a checkable output."
    if milestone_title:
        return f"该细化服务于当前阶段“{milestone_title}”，能推进整体计划，并产出可检查结果。"
    return "该细化能推进整体计划，并产出可检查结果。"


def _is_skiing_payload(payload: RefineTaskRequest) -> bool:
    return any(term in _refine_context_text(payload) for term in SKIING_DOMAIN_TERMS)


def _skiing_time_blocks(minutes: int, output_language: str) -> list[TimeBlock]:
    if output_language == "en":
        raw_blocks = [
            {"title": "Warm up and stance cues", "durationMinutes": min(30, minutes), "action": "Warm up ankles, knees, and hips, then rehearse a neutral skiing stance on flat ground.", "expectedOutput": "A ready body and clear stance cues."},
            {"title": "Flat-ground balance drills", "durationMinutes": max(1, minutes - 30), "action": "Practice knee flexion, centered weight, side-to-side pressure shifts, and small marching steps while keeping the skis/feet parallel.", "expectedOutput": "Stable balance without changing into another sport drill."},
        ]
    else:
        raw_blocks = [
            {"title": "热身与滑雪站姿", "durationMinutes": min(30, minutes), "action": "活动脚踝、膝盖和髋部，在平地按滑雪站姿练习双腿微曲、重心居中、上身稳定。", "expectedOutput": "身体热身完成，并能说出滑雪站姿的关键要点。"},
            {"title": "平地平衡练习", "durationMinutes": max(1, minutes - 30), "action": "围绕滑雪平衡做原地踏步、左右重心转移、前后压力感知练习，保持雪板/双脚方向稳定。", "expectedOutput": "完成一组滑雪平衡记录，知道自己哪一侧更不稳。"},
        ]
    return normalize_time_blocks(raw_blocks, budget_minutes=minutes, task_title="skiing balance", output_language=output_language)


def _skiing_learning_resources(output_language: str) -> list[LearningResource]:
    if output_language == "en":
        raw_resources = [
            {"title": "Skiing beginner stance and balance", "type": "search_keyword", "searchKeyword": "beginner skiing stance balance drills"},
            {"title": "Snowplow skiing safety basics", "type": "search_keyword", "searchKeyword": "beginner skiing safety snowplow basics"},
        ]
    else:
        raw_resources = [
            {"title": "滑雪基本站姿与平衡", "type": "search_keyword", "searchKeyword": "滑雪 初学者 基本站姿 平衡 练习"},
            {"title": "滑雪入门安全与犁式刹车", "type": "search_keyword", "searchKeyword": "滑雪 入门 安全 犁式刹车 基础"},
        ]
    return validate_learning_resources(raw_resources, task_title="滑雪站姿平衡", output_language=output_language)


def _refined_task_from_parsed(parsed: object, payload: RefineTaskRequest) -> RefinedTask:
    if not isinstance(parsed, dict):
        raise ValueError("refined task output must be a JSON object")
    budget_minutes, budget_source = _refine_budget(payload)
    data = {
        "title": str(parsed.get("title") or "").strip(),
        "objective": str(parsed.get("objective") or "").strip(),
        "estimatedMinutes": budget_minutes,
        "steps": _string_list(parsed.get("steps")),
        "checklist": _string_list(parsed.get("checklist")),
        "acceptanceCriteria": _string_list(parsed.get("acceptanceCriteria") or parsed.get("acceptance_criteria")),
        "deliverable": str(parsed.get("deliverable") or "").strip(),
        "risks": _string_list(parsed.get("risks")),
        "fallbackTips": _string_list(parsed.get("fallbackTips") or parsed.get("fallback_tips")),
        "mode": "llm",
        "timeBlocks": normalize_time_blocks(
            parsed.get("timeBlocks") or parsed.get("time_blocks"),
            budget_minutes=budget_minutes,
            task_title=payload.task_title,
            output_language=payload.output_language,
        ),
        "learningResources": validate_learning_resources(
            parsed.get("learningResources") or parsed.get("learning_resources"),
            task_title=payload.task_title,
            output_language=payload.output_language,
        ),
        "budgetExplanation": _clean_text(parsed.get("budgetExplanation") or parsed.get("budget_explanation"), 240)
        or _budget_explanation(budget_minutes, budget_source, payload.output_language),
        "planFitCheck": _plan_fit_check(parsed, payload),
    }
    return RefinedTask(**data)


def build_refine_local_fallback(payload: RefineTaskRequest, *, fallback_reason: str, error_type: str | None = None) -> RefinedTask:
    minutes, budget_source = _refine_budget(payload)
    title = payload.task_title.strip()
    description = payload.task_description.strip()
    instruction = payload.refinement_instruction.strip()
    is_english = payload.output_language == "en"
    time_blocks = normalize_time_blocks([], budget_minutes=minutes, task_title=title, output_language=payload.output_language)
    learning_resources = validate_learning_resources([], task_title=title, output_language=payload.output_language)
    plan_fit_check = _plan_fit_check({}, payload)
    if _is_skiing_payload(payload):
        time_blocks = _skiing_time_blocks(minutes, payload.output_language)
        learning_resources = _skiing_learning_resources(payload.output_language)
        if is_english:
            return RefinedTask(
                title=title,
                objective=description or f"Practice the skiing stance and flat-ground balance needed for {title}.",
                estimatedMinutes=minutes,
                steps=[
                    "Warm up ankles, knees, hips, and core before putting on skis or simulating ski stance.",
                    "Practice a neutral skiing stance: knees flexed, shins lightly forward, hips centered, hands in front.",
                    "Do flat-ground balance drills with small marching steps and slow left-right pressure shifts.",
                    "Record which side feels unstable and one cue to use on the next snow session.",
                ],
                checklist=[
                    "Knees stay flexed without locking.",
                    "Weight stays centered instead of leaning back.",
                    "Balance drill is recorded with one improvement cue.",
                ],
                acceptanceCriteria=[
                    "The practice stays within skiing stance and balance, with no unrelated activity substituted.",
                ],
                deliverable="A short skiing balance practice record with the most unstable side and next cue.",
                risks=["Leaning back or locking the knees can make the stance unstable."],
                fallbackTips=["If balance is difficult, shorten the drill and use a wall or poles for light support."],
                timeBlocks=time_blocks,
                learningResources=learning_resources,
                budgetExplanation=_budget_explanation(minutes, budget_source, payload.output_language),
                planFitCheck=plan_fit_check,
                mode="local_fallback",
                fallbackReason=fallback_reason,
                errorType=error_type,
            )
        return RefinedTask(
            title=title,
            objective=description or f"围绕“{title}”练习滑雪基本站姿、重心居中和平地平衡。",
            estimatedMinutes=minutes,
            steps=[
                "先活动脚踝、膝盖、髋部和核心，确认身体适合做滑雪站姿练习。",
                "练习滑雪基本站姿：双腿微曲、小腿轻压鞋舌、髋部居中、双手在身体前方。",
                "做平地平衡练习：原地踏步、左右重心转移、前后压力感知，保持双脚或雪板方向稳定。",
                "记录哪一侧更不稳，以及下一次上雪或模拟练习要提醒自己的一个动作 cue。",
            ],
            checklist=[
                "膝盖保持微曲，没有锁死。",
                "重心没有后坐，能维持在脚掌中部附近。",
                "完成平地平衡记录，并写下一条改进提示。",
            ],
            acceptanceCriteria=[
                "细化内容保持在滑雪站姿和平衡练习内，没有替换成其他运动或泛泛体能练习。",
            ],
            deliverable="一份滑雪平衡练习记录：最不稳的一侧、保持时间或次数、下一次练习 cue。",
            risks=["后坐或膝盖锁死会让滑雪站姿不稳定。"],
            fallbackTips=["如果平衡困难，先扶墙或扶雪杖做短组练习，再逐步减少辅助。"],
            timeBlocks=time_blocks,
            learningResources=learning_resources,
            budgetExplanation=_budget_explanation(minutes, budget_source, payload.output_language),
            planFitCheck=plan_fit_check,
            mode="local_fallback",
            fallbackReason=fallback_reason,
            errorType=error_type,
        )
    if is_english:
        objective = description or f"Turn {title} into one concrete, finishable work session."
        if instruction:
            objective = f"{objective} Extra refinement request: {instruction}"
        return RefinedTask(
            title=title,
            objective=objective,
            estimatedMinutes=minutes,
            steps=[
                f"Spend the first 10 minutes clarifying what {title} must produce.",
                "Work through the smallest practical example or exercise before expanding the scope.",
                "Record the result, open questions, and the next smallest follow-up action.",
            ],
            checklist=[
                "The task has a visible output.",
                "The next action is clear if more work is needed.",
            ],
            acceptanceCriteria=[
                "You can explain what was completed and point to the saved output.",
            ],
            deliverable=f"A short note or file that captures the result of {title}.",
            risks=[
                "The task may stay too broad if you do not define a concrete output first.",
            ],
            fallbackTips=[
                "If stuck, reduce the task to a 15-minute practice step.",
                "If information is missing, write the missing question before continuing.",
            ],
            timeBlocks=time_blocks,
            learningResources=learning_resources,
            budgetExplanation=_budget_explanation(minutes, budget_source, payload.output_language),
            planFitCheck=plan_fit_check,
            mode="local_fallback",
            fallbackReason=fallback_reason,
            errorType=error_type,
        )
    objective = description or f"把“{title}”拆成一次可以完成的具体执行任务。"
    if instruction:
        objective = f"{objective} 额外细化要求：{instruction}"
    return RefinedTask(
        title=title,
        objective=objective,
        estimatedMinutes=minutes,
        steps=[
            f"先用 10 分钟明确“{title}”今天要产出的结果。",
            "完成一个最小可执行练习或样例，先做出来再扩展细节。",
            "记录完成内容、卡点和下一步最小行动。",
        ],
        checklist=[
            "已经留下一个可检查的输出物。",
            "如果还要继续，下一步行动已经写清楚。",
        ],
        acceptanceCriteria=[
            "能说明本次完成了什么，并能指向保存下来的输出。",
        ],
        deliverable=f"一份关于“{title}”的结果记录、练习文件或检查清单。",
        risks=[
            "如果一开始不定义产出物，任务容易停留在泛泛学习。",
        ],
        fallbackTips=[
            "卡住时，把任务缩小成 15 分钟内能完成的一步。",
            "缺资料时，先写下具体问题，再决定是否查资料或请教他人。",
        ],
        timeBlocks=time_blocks,
        learningResources=learning_resources,
        budgetExplanation=_budget_explanation(minutes, budget_source, payload.output_language),
        planFitCheck=plan_fit_check,
        mode="local_fallback",
        fallbackReason=fallback_reason,
        errorType=error_type,
    )


def _local_refined_task(payload: RefineTaskRequest, *, fallback_reason: str, error_type: str | None = None) -> RefinedTask:
    return build_refine_local_fallback(payload, fallback_reason=fallback_reason, error_type=error_type)


def _goal_plan_system_prompt() -> str:
    return (
        "You are a planning agent. Return compact valid JSON only. "
        "No markdown, no commentary, no extra keys. "
        'Shape: {"summary":"one short sentence","structuredPlan":{"goalTitle":"...",'
        '"goalDescription":"natural concise description","durationDays":14,'
        '"milestones":[{"title":"...","description":"...","tasks":[{"title":"...",'
        '"description":"...","estimatedMinutes":45,"dueDate":"YYYY-MM-DD",'
        '"priority":"low|medium|high"}]}],"reviewPlan":{"frequency":"daily|weekly",'
        '"questions":["..."]}}}. '
        "Do not include phases, tasks, markdown, code fences, or explanatory text outside JSON. "
        "Use the provided planHorizon and densityPolicy. Do not shorten a 30/60/90/180 day request into one week. "
        "Long plans must use milestones plus weekly progress tasks, not one generic daily list. "
        "For 90 day plans, create at least 3 monthly milestones, at least 24 total tasks, and dueDate values across at least 10 weeks. "
        "For 30 day plans, create at least 12 tasks across 4 weeks. For 14 day plans, create at least 8 tasks across 2 weeks. "
        "Every task must have a concrete title, description, estimatedMinutes, dueDate, and priority. "
        "Avoid vague task titles such as 继续学习, 保持练习, 完成任务, or 提升能力. "
        "Keep field values natural and concise; avoid oversized prose. Use retrievedSources when available. "
        "The structuredPlan is the source of truth. Do not write user data. "
        "The user payload may include preferenceMemory, historyMemory, todayPlans, and memoryContextSummary. "
        "Respect explicit constraints first, then preferenceMemory, then todayPlans, then historyMemory, then RAG materials. "
        "If preferenceMemory.dailyAvailableMinutes exists, keep task estimatedMinutes and daily workload within that time budget. "
        "Do not invent personal details that are not present in the provided context. "
        "All user-facing string values must use outputLanguage from the user payload. "
        "If outputLanguage is zh-CN, write Simplified Chinese; keep technical terms like Python, FastAPI, RAG, API as-is."
    )


def _repair_goal_plan(
    llm_client: LlmClient,
    *,
    payload: GoalPlanRequest,
    output_language: str,
    original_plan: StructuredGoalPlan,
    original_summary: str,
    quality_report: PlanQualityReport,
    horizon: PlanHorizon,
    policy: PlanDensityPolicy,
    fallback: StructuredGoalPlan,
) -> tuple[StructuredGoalPlan, str, PlanQualityReport] | None:
    result, _error = llm_client.complete(
        "planning_goal_plan_repair",
        (
            "You repair a structured plan that failed a deterministic quality gate. "
            "Return strict JSON only with keys summary and structuredPlan. "
            "Preserve the original goal and useful tasks, but expand or date them so the plan matches planHorizon and densityPolicy. "
            "Do not explain the repair. Do not write user data."
        ),
        _dump(
            {
                "goal": payload.goal,
                "outputLanguage": output_language,
                "originalSummary": original_summary,
                "originalStructuredPlan": original_plan.model_dump(by_alias=True),
                "qualityReport": quality_report.model_dump(by_alias=True),
                "planHorizon": horizon.model_dump(by_alias=True),
                "densityPolicy": policy.model_dump(by_alias=True),
                "date": payload.date,
                "deadline": payload.deadline,
                "dailyHours": payload.daily_hours,
            }
        ),
        max_tokens=_goal_plan_max_tokens(),
        max_token_cap=GOAL_PLAN_MAX_TOKEN_LIMIT,
        temperature=0.15,
        timeout_seconds=_goal_plan_timeout(llm_client.settings.timeout_seconds),
        response_format_json=True,
        task_type="plan_generation",
    )
    parsed = _json_object(result.content) if result else None
    if not parsed:
        return None
    repaired_plan = _structured_from_parsed(parsed, fallback)
    repaired_report = validate_structured_plan_quality(
        repaired_plan.model_dump(by_alias=True),
        horizon,
        policy,
    )
    if not repaired_report.ok:
        return None
    repaired_summary = str(parsed.get("summary") or repaired_plan.goal_description)
    return repaired_plan, repaired_summary, repaired_report


def _goal_plan_source_type(mode: str, quality_status: str, grounded_source_type: str) -> str:
    if quality_status == "local_fallback" or mode != "llm":
        return "local_fallback"
    if grounded_source_type == "local_context":
        return "local_context"
    return "model_knowledge"


class PlanningService:
    def __init__(self):
        self.rag = RagService()

    def create_goal_plan(self, payload: GoalPlanRequest) -> GoalPlanOut:
        _parse_date(payload.date)
        preferences = payload.preferences or _load_preference_memory()
        sources = self.rag.retrieve(" ".join([payload.goal, payload.materials]), limit=4)
        retrieved_sources = [source.model_dump(by_alias=True) for source in sources]
        source_grounding = assess_local_source_grounding(payload.goal, retrieved_sources)
        horizon = detect_plan_horizon(payload.goal, payload.date, payload.deadline)
        policy = build_density_policy(horizon)
        output_language = payload.output_language or _detect_output_language(payload.goal, payload.materials, preferences)
        fallback_structured = build_local_structured_plan(
            payload.goal,
            date=payload.date,
            deadline=payload.deadline,
            daily_hours=payload.daily_hours,
            source_count=len(sources),
            horizon=horizon,
            policy=policy,
        )
        llm_client = LlmClient()
        llm_result, llm_error = llm_client.complete(
            "planning_goal_plan",
            _goal_plan_system_prompt(),
            _dump(
                {
                    "goal": payload.goal,
                    "outputLanguage": output_language,
                    "deadline": payload.deadline,
                    "dailyHours": payload.daily_hours,
                    "materials": payload.materials[:3000],
                    "retrievedSources": retrieved_sources,
                    "preferences": preferences,
                    "date": payload.date,
                    "planHorizon": horizon.model_dump(by_alias=True),
                    "densityPolicy": policy.model_dump(by_alias=True),
                }
            ),
            max_tokens=_goal_plan_max_tokens(),
            max_token_cap=GOAL_PLAN_MAX_TOKEN_LIMIT,
            temperature=0.2,
            timeout_seconds=_goal_plan_timeout(llm_client.settings.timeout_seconds),
            response_format_json=True,
            task_type="plan_generation",
        )
        model_usage = (
            _model_usage_from_result(llm_result, "plan_generation")
            if llm_result
            else _local_model_usage(llm_client, "plan_generation", llm_error)
        )

        mode = "mock"
        provider = None
        model = None
        fallback_reason = None
        error_type = None
        error_message = None
        base_url_host = None
        parsed = _json_object(llm_result.content) if llm_result else None
        if not parsed and llm_error and llm_error.local_fallback_allowed is False:
            raise RuntimeError(llm_error.message)
        quality_status = "local_fallback"
        repair_attempted = False
        if parsed:
            mode = "llm"
            provider = llm_result.provider if llm_result else None
            model = llm_result.model if llm_result else None
            structured_plan = _structured_from_parsed(parsed, fallback_structured)
            summary = str(parsed.get("summary") or structured_plan.goal_description)
            quality_report = validate_structured_plan_quality(
                structured_plan.model_dump(by_alias=True),
                horizon,
                policy,
            )
            if quality_report.ok:
                quality_status = "passed"
            else:
                repair_attempted = True
                repaired = _repair_goal_plan(
                    llm_client,
                    payload=payload,
                    output_language=output_language,
                    original_plan=structured_plan,
                    original_summary=summary,
                    quality_report=quality_report,
                    horizon=horizon,
                    policy=policy,
                    fallback=fallback_structured,
                )
                if repaired:
                    structured_plan, summary, quality_report = repaired
                    quality_status = "repaired"
                else:
                    structured_plan = fallback_structured
                    summary = structured_plan.goal_description
                    quality_report = validate_structured_plan_quality(
                        structured_plan.model_dump(by_alias=True),
                        horizon,
                        policy,
                    )
                    mode = "mock"
                    fallback_reason = "quality_gate_failed"
                    error_type = "plan_quality_failed"
                    error_message = "The model returned a structured plan, but it did not pass the plan quality gate."
                    quality_status = "local_fallback"
        else:
            structured_plan = fallback_structured
            summary = structured_plan.goal_description
            quality_report = validate_structured_plan_quality(
                structured_plan.model_dump(by_alias=True),
                horizon,
                policy,
            )
            provider = llm_client.settings.provider
            model = llm_client.settings.model
            base_url_host = _base_url_host(llm_client.settings.base_url)
            if llm_result and not parsed:
                fallback_reason = "llm_error"
                error_type = "invalid_model_output"
                error_message = "The model returned content, but it was not valid structured JSON."
            elif llm_client.settings.provider == "mock":
                fallback_reason = "mock_provider"
                error_message = "Mock provider is active."
            elif not llm_client.settings.has_api_key:
                fallback_reason = "missing_api_key"
                error_message = "API key is not saved."
            else:
                fallback_reason = "llm_error"
                error_type = _safe_goal_error_type(llm_error.error_type if llm_error else "unknown")
                error_message = llm_error.message if llm_error else "The model response could not be used."

        phases = derive_phase_items(structured_plan)
        tasks = derive_planner_tasks(structured_plan)
        if not summary:
            summary = structured_plan.goal_description
        source_type = _goal_plan_source_type(mode, quality_status, str(source_grounding["sourceType"]))
        local_relevance = str(source_grounding["localRelevance"])
        fallback_used = quality_status == "local_fallback" or mode != "llm"
        quality_report = with_demo_quality_metrics(
            quality_report,
            horizon=horizon,
            quality_status=quality_status,
            source_type=source_type,
            local_relevance=local_relevance,
            repair_attempted=repair_attempted,
            fallback_used=fallback_used,
        )

        plan_id = str(uuid4())
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO planning_goals(
                  id, goal, deadline, daily_hours, materials, preferences,
                  summary, phases_json, tasks_json, structured_plan_json, sources_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    payload.goal,
                    payload.deadline,
                    payload.daily_hours,
                    payload.materials,
                    preferences,
                    summary,
                    _dump([phase.model_dump() for phase in phases]),
                    _dump([task.model_dump() for task in tasks]),
                    _dump(structured_plan.model_dump(by_alias=True)),
                    _dump(retrieved_sources),
                ),
            )

        MemoryService().create_memory(
            MemoryCreate(
                kind="planning_history",
                title=structured_plan.goal_title,
                content=render_goal_plan_markdown(structured_plan),
                summary=summary or structured_plan.goal_description,
                source="ai",
                sourceId=plan_id,
                sourceKey=f"planning_goal:{plan_id}",
                metadata={
                    "structuredPlan": structured_plan.model_dump(by_alias=True),
                    "mode": mode,
                    "fallbackReason": fallback_reason,
                    "errorType": error_type,
                    "source": "goals",
                },
            )
        )

        return GoalPlanOut(
            id=plan_id,
            mode=mode,
            summary=summary,
            phases=phases,
            tasks=tasks,
            sources=sources,
            structuredPlan=structured_plan,
            provider=provider,
            model=model,
            fallbackReason=fallback_reason,
            errorType=error_type,
            errorMessage=error_message,
            baseUrlHost=base_url_host,
            planHorizon=horizon,
            qualityReport=quality_report,
            qualityStatus=quality_status,
            sourceType=source_type,
            localRelevance=local_relevance,
            modelUsage=model_usage,
        )

    def refine_task(self, payload: RefineTaskRequest) -> RefinedTask:
        payload = _refine_payload_with_source_context(payload)
        if payload.date:
            _parse_date(payload.date)
        llm_client = LlmClient()
        source_payload = [source.model_dump(by_alias=True) for source in payload.retrieved_sources[:4]]
        llm_result, llm_error = llm_client.complete(
            "planning_refine_task",
            (
                "You are a task refinement assistant. Return strict JSON only. "
                "No markdown, no code fences, no commentary, no extra keys. "
                'Required shape: {"title":"...","objective":"...","estimatedMinutes":60,'
                '"steps":["...","...","..."],"checklist":["...","..."],'
                '"acceptanceCriteria":["..."],"deliverable":"...","risks":[],"fallbackTips":[],'
                '"timeBlocks":[{"title":"...","durationMinutes":30,"action":"...","expectedOutput":"..."}],'
                '"learningResources":[{"title":"...","type":"official_doc|library_doc|search_keyword|local_source","url":null,"searchKeyword":"...","reason":"..."}],'
                '"budgetExplanation":"...","planFitCheck":{"fitsCurrentMilestone":true,'
                '"advancesOverallGoal":true,"hasCheckableOutput":true,"note":"..."}}. '
                "Refine only the selected task, not the whole goal plan. "
                "Use planContext to understand where the task sits in the overall plan. "
                "Do not change the task domain: if planContext says skiing, the output must stay about skiing rather than yoga, fitness, meditation, or another activity. "
                "Make the result concrete enough for one work session and include where to learn, how to learn, and how to verify completion. "
                "The output must use the requested outputLanguage: zh means Simplified Chinese; en means English. "
                "Respect the provided timeBudgetMinutes exactly in estimatedMinutes and split it into timeBlocks. "
                "Every timeBlocks.durationMinutes must be <= 30. Never output a 40/45/60/90/120 minute block. "
                "Prefer official or authoritative learning resources from allowedResourceDomains. "
                "Do not invent random tutorial links. If no official URL fits, return a search_keyword resource with url null. "
                "If refinementInstruction is present, combine it with taskTitle, taskDescription, planContext, sources, and constraints; do not ignore the original task and do not "
                "use the instruction alone. If refinementInstruction is empty, refine from the task content only. "
                "Do not write data or claim the task was saved."
            ),
            _dump(
                {
                    "goal": payload.goal,
                    "taskTitle": payload.task_title,
                    "taskDescription": payload.task_description,
                    "date": payload.date,
                    "availableMinutes": payload.available_minutes,
                    "timeBudgetMinutes": _refine_budget(payload)[0],
                    "planContext": payload.plan_context.model_dump(by_alias=True) if payload.plan_context else None,
                    "sourceKey": payload.source_key,
                    "planId": payload.plan_id,
                    "userConstraints": payload.user_constraints,
                    "retrievedSources": source_payload,
                    "allowedResourceDomains": sorted(OFFICIAL_RESOURCE_DOMAINS),
                    "outputLanguage": payload.output_language,
                    "refinementInstruction": payload.refinement_instruction,
                }
            ),
            max_tokens=1800,
            temperature=0.2,
            timeout_seconds=min(max(llm_client.settings.timeout_seconds, 30), 60),
            response_format_json=True,
            task_type="task_refinement",
        )

        parsed = _json_object(llm_result.content) if llm_result else None
        if not parsed and llm_error and llm_error.local_fallback_allowed is False:
            raise RuntimeError(llm_error.message)
        if parsed:
            try:
                refined = _refined_task_from_parsed(parsed, payload)
                if has_refinement_domain_drift(refined, payload):
                    return _local_refined_task(
                        payload,
                        fallback_reason="llm_error",
                        error_type="domain_mismatch",
                    )
                return refined
            except (ValidationError, ValueError):
                return _local_refined_task(
                    payload,
                    fallback_reason="llm_error",
                    error_type="invalid_model_output",
                )

        if llm_result and not parsed:
            return _local_refined_task(
                payload,
                fallback_reason="llm_error",
                error_type="invalid_model_output",
            )
        if llm_client.settings.provider == "mock":
            return _local_refined_task(payload, fallback_reason="mock_provider")
        if not llm_client.settings.has_api_key:
            return _local_refined_task(payload, fallback_reason="missing_api_key")
        return _local_refined_task(
            payload,
            fallback_reason="llm_error",
            error_type=_safe_goal_error_type(llm_error.error_type if llm_error else "unknown"),
        )

    def create_daily_review(self, payload: DailyReviewRequest) -> DailyReviewOut:
        review_date = _parse_date(payload.date).isoformat()
        target_date = _next_day(review_date)
        plans = self._plans_for_review(payload)
        done_count = len([plan for plan in plans if plan.get("done")])
        total_count = len(plans)
        unfinished = [plan for plan in plans if not plan.get("done")]
        llm_result, _ = LlmClient().complete(
            "planning_daily_review",
            (
                "You are an AI daily review and replanning assistant. Return only valid JSON, no markdown. "
                'Required shape: {"summary":"...","suggestions":["..."],'
                '"replanTasks":[{"targetDate":"YYYY-MM-DD","time":"HH:MM","title":"...","reason":"...",'
                '"sourcePlanId":"..."}]}. '
                "Use at most 4 suggestions and at most 4 replanTasks. Do not modify data directly."
            ),
            _dump(
                {
                    "date": review_date,
                    "targetDate": target_date,
                    "goal": payload.goal,
                    "plans": plans,
                    "doneCount": done_count,
                    "totalCount": total_count,
                    "unfinished": unfinished,
                    "preferences": payload.preferences or _load_preference_memory(),
                }
            ),
            max_tokens=1800,
            temperature=0.2,
            task_type="chat",
        )

        mode = "mock"
        provider = None
        model = None
        parsed = _json_object(llm_result.content) if llm_result else None
        if parsed:
            mode = "llm"
            provider = llm_result.provider if llm_result else None
            model = llm_result.model if llm_result else None
            summary = str(parsed.get("summary") or f"Today completed {done_count}/{total_count} tasks.")
            suggestions = _normalize_suggestions(parsed.get("suggestions"))
            replan_tasks = _normalize_replan_tasks(parsed.get("replanTasks") or parsed.get("replan_tasks"), target_date)
        else:
            summary, suggestions, replan_tasks = self._mock_daily_review(done_count, total_count, unfinished, target_date)

        if not suggestions:
            suggestions = ["Keep today's useful output and split unfinished work into smaller next steps."]

        review_id = str(uuid4())
        with get_conn() as conn:
            row = conn.execute(
                """
                INSERT INTO daily_reviews(
                  id, date, summary, suggestions, done_count, total_count,
                  suggestions_json, replan_tasks_json, target_date, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(date)
                DO UPDATE SET
                  summary = excluded.summary,
                  suggestions = excluded.suggestions,
                  done_count = excluded.done_count,
                  total_count = excluded.total_count,
                  suggestions_json = excluded.suggestions_json,
                  replan_tasks_json = excluded.replan_tasks_json,
                  target_date = excluded.target_date,
                  updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (
                    review_id,
                    review_date,
                    summary,
                    _dump(suggestions),
                    done_count,
                    total_count,
                    _dump(suggestions),
                    _dump([task.model_dump(by_alias=True) for task in replan_tasks]),
                    target_date,
                ),
            ).fetchone()

        return self._row_to_daily_review(row, mode=mode, provider=provider, model=model)

    def get_daily_review(self, review_date: str) -> DailyReviewOut:
        normalized_date = _parse_date(review_date).isoformat()
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM daily_reviews WHERE date = ?", (normalized_date,)).fetchone()
        if not row:
            return DailyReviewOut(
                id="",
                mode="saved",
                date=normalized_date,
                summary="",
                suggestions=[],
                doneCount=0,
                totalCount=0,
                targetDate=_next_day(normalized_date),
                replanTasks=[],
                updatedAt="",
            )
        return self._row_to_daily_review(row, mode="saved")

    def apply_replan(self, payload: ReplanApplyRequest) -> list[PlanOut]:
        created = []
        for task in payload.tasks:
            created.append(
                create_plan(
                    PlanCreate(
                        date=task.target_date,
                        time=task.time,
                        content=task.title,
                        result=task.reason,
                        priority="medium",
                        estimatedMinutes=45,
                        source="ai",
                    )
                )
            )
        return created

    def _plans_for_review(self, payload: DailyReviewRequest) -> list[dict[str, object]]:
        day = payload.data.get(payload.date, {}) if payload.data else {}
        frontend_plans = day.get("plans", []) if isinstance(day, dict) else []
        if frontend_plans:
            return [dict(plan) for plan in frontend_plans if isinstance(plan, dict)]
        return [_plan_to_dict(plan) for plan in list_plans(payload.date)]

    def _row_to_daily_review(self, row, mode: str, provider: str | None = None, model: str | None = None) -> DailyReviewOut:
        suggestions = _load_list(row["suggestions_json"] or row["suggestions"])
        replan_tasks = _normalize_replan_tasks(_load_list(row["replan_tasks_json"]), row["target_date"])
        return DailyReviewOut(
            id=row["id"],
            mode=mode,
            date=row["date"],
            summary=row["summary"],
            suggestions=[str(item) for item in suggestions],
            doneCount=row["done_count"],
            totalCount=row["total_count"],
            targetDate=row["target_date"],
            replanTasks=replan_tasks,
            provider=provider,
            model=model,
            updatedAt=row["updated_at"],
        )

    def _mock_goal_plan(self, payload: GoalPlanRequest, source_count: int = 0) -> tuple[str, list[PhaseItem], list[PlannerTask]]:
        goal = payload.goal or "AI application internship"
        source_note = f"Referenced {source_count} retrieved material chunks. " if source_count else ""
        return (
            f"{source_note}Generated a three-phase plan for {goal} with {payload.daily_hours:g} focused hours per day.",
            [
                PhaseItem(
                    title="Phase 1: Role alignment",
                    detail="Extract the target role requirements, map required skills, and build a focused learning checklist.",
                ),
                PhaseItem(
                    title="Phase 2: Portfolio sprint",
                    detail="Ship demonstrable AI application features, including planning loop, RAG, evaluation, and deployment evidence.",
                ),
                PhaseItem(
                    title="Phase 3: Application review",
                    detail="Refine resume bullets, project narrative, interview notes, and weekly feedback loops.",
                ),
            ],
            [
                PlannerTask(
                    time="09:00",
                    title="Extract five requirements from the target JD",
                    reason="Align today's work with real internship expectations.",
                ),
                PlannerTask(
                    time="14:30",
                    title="Implement or improve one AI application feature",
                    reason="Keep a visible engineering output every day.",
                ),
                PlannerTask(
                    time="20:30",
                    title="Review progress and update tomorrow's task list",
                    reason="Close the loop between planning, execution, review, and replanning.",
                ),
            ],
        )

    def _mock_daily_review(
        self,
        done_count: int,
        total_count: int,
        unfinished: list[dict[str, object]],
        target_date: str,
    ) -> tuple[str, list[str], list[ReplanTask]]:
        suggestions = [
            "Keep today's completed outputs as weekly review and interview evidence.",
            "Split unfinished tasks into 30-45 minute blocks to reduce tomorrow's startup cost.",
            "Prioritize the task most directly connected to the long-term goal.",
        ]
        replan_tasks = []
        for index, plan in enumerate(unfinished[:3]):
            title = plan.get("title") or plan.get("content") or "unfinished task"
            replan_tasks.append(
                ReplanTask(
                    targetDate=target_date,
                    time=str(plan.get("time") or f"{9 + index:02d}:00")[:5],
                    title=f"Continue: {title}",
                    reason="Moved from today's unfinished work into tomorrow's replan preview.",
                    sourcePlanId=str(plan.get("id")) if plan.get("id") else None,
                )
            )
        if not replan_tasks:
            replan_tasks.append(
                ReplanTask(
                    targetDate=target_date,
                    time="09:00",
                    title="Review today's evidence and choose the next step",
                    reason="No unfinished task was found, so tomorrow starts from a lightweight review.",
                )
            )
        return f"Today completed {done_count}/{total_count} tasks.", suggestions, replan_tasks
