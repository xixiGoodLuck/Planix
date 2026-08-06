#!/usr/bin/env python
"""Read-only auditor for browser-created Planix Cognitive Planning threads.

The formal acceptance batch is advanced only through the existing ``/command``
page.  This CLI performs replay GETs and never posts chat messages, accepts an
API key, or prints model prompts/provider responses.  The in-process runner is
kept solely for deterministic state-machine unit tests.

Example:

    python scripts/live_planning_e2e.py --audit-manifest manifest.json --required-provider deepseek

The generated JSON report contains identifiers and aggregate acceptance data,
not model prompts, provider responses, credentials, or full plan contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence
from urllib.parse import urlsplit

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8003"
RETRY_MESSAGE = "请重试当前深度规划"
STRATEGY_APPROVAL_MESSAGE = "确认方向"

# These threads are retained as failed audit evidence.  They stopped at design
# approval and must never be counted as part of a successful zero-start batch.
EXCLUDED_OLD_THREAD_IDS = (
    "551e5c26",
    "6e24190e",
    "824c7734",
    "dfd38516",
    "e1601014",
    "45c75d79",
    "49622092",
    "b3230bf5",
    "ac3fa5c1",
    "65e8beab",
)

REQUIRED_REPLAY_KINDS = frozenset(
    {
        "goal_model_updated",
        "goal_completion_updated",
        "reality_assessment_ready",
        "evidence_pack_ready",
        "strategy_portfolio_ready",
        "execution_blueprint_ready",
        "critique_report_ready",
        "planning_session_status",
    }
)

REQUIRED_ARTIFACT_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("Goal", "goal_model_updated"),
    ("Completion", "goal_completion_updated"),
    ("Reality", "reality_assessment_ready"),
    ("Evidence", "evidence_pack_ready"),
    ("Strategy", "strategy_portfolio_ready"),
    ("Execution", "execution_blueprint_ready"),
    ("Critique", "critique_report_ready"),
)

FORBIDDEN_REPLAY_KINDS = frozenset(
    {
        "execution_plan_draft",
        "draft_created",
        "runtime_started",
        "runtime_event",
        "calendar_plan_preview",
        "calendar_write_preview",
        "calendar_write_result",
        "approval_required",
        "approval_request",
        "plan_patch_preview",
    }
)


KeywordGroup = tuple[str, ...]

REQUIRED_MODEL_STAGES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("planning_goal_model", "Goal Intelligence Agent", frozenset({"produce_artifact"})),
    ("planning_reality", "Reality Agent", frozenset({"approve", "request_user_input"})),
    ("planning_evidence", "Evidence Agent", frozenset({"approve", "request_user_input"})),
    ("planning_strategy", "Strategy Agent", frozenset({"request_user_input"})),
    ("planning_execution", "Execution Agent", frozenset({"produce_artifact"})),
    (
        "planning_critique",
        "Critic Agent",
        frozenset({"approve", "block", "request_agent_revision"}),
    ),
)

NON_REAL_MODEL_MARKERS = frozenset({"mock", "fake", "local", "fixture", "stub", "test"})
KNOWN_REQUIRED_PROVIDERS = ("deepseek", "zhipu_glm", "glm", "kimi", "openai", "custom")
PERFORMANCE_TARGET_MINUTES = 45
PLANNING_CONCURRENCY_LIMIT = 2
MIN_CRITIC_SCORE = 90
SOURCE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")

_SENSITIVE_LABEL_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|(?:api[_ -]?key|authorization|bearer)\s*[:= ]\s*\S+)",
    re.IGNORECASE,
)

SOURCE_FINGERPRINT_ROOTS = (
    Path("Backend/backend/app"),
    Path("Frontend/src"),
    Path("scripts/live_planning_e2e.py"),
)


@dataclass(frozen=True)
class Scenario:
    key: str
    direction: str
    initial_message: str
    persona_response: str
    requires_clarification: bool
    keyword_groups: tuple[KeywordGroup, ...]
    supporting_keyword_groups: tuple[KeywordGroup, ...] = ()
    forbidden_keyword_groups: tuple[KeywordGroup, ...] = ()
    time_capacity_minutes: int | None = None
    spending_limit_cny: int | None = None


INDEPENDENT_GOAL = "这是独立新目标，不覆盖其他线程目标。"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="travel",
        direction="旅游",
        initial_message="我想今年秋天去日本旅游",
        persona_response=(
            f"{INDEPENDENT_GOAL} 我们两人从上海出发，计划今年秋天去日本关西自由行7天，"
            "总预算2万元。这是第一次自由行，偏好文化与美食。其他信息未知，请只根据这些事实继续规划。"
        ),
        requires_clarification=True,
        keyword_groups=(
            ("2万", "20000", "20,000", "预算", "budget"),
            ("交通", "航班", "铁路", "列车", "transport", "flight", "train"),
            ("住宿", "酒店", "旅馆", "accommodation", "hotel"),
            ("每日", "每天", "第1天", "day 1", "节奏", "itinerary"),
        ),
        supporting_keyword_groups=(
            (
                "秋天",
                "秋季",
                "9月",
                "九月",
                "10月",
                "十月",
                "11月",
                "十一月",
                "september",
                "october",
                "november",
                "autumn",
                "fall",
            ),
            ("风险", "延误", "雨天", "应急", "fallback", "备选"),
        ),
        spending_limit_cny=20000,
    ),
    Scenario(
        key="go",
        direction="Go",
        initial_message=(
            "我想学习 Golang Web 后端开发。我有 JavaScript 和基础 SQL 经验，每周可投入10小时，"
            "计划用12周完成。目标是独立做出一个带用户认证、PostgreSQL、REST API、自动化测试和"
            "Docker部署的Go服务，并能解释架构取舍。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 我有JavaScript和基础SQL经验，每周10小时，共12周；"
            "交付带认证、PostgreSQL、REST API、自动化测试、Docker部署的Go后端，"
            "并能够解释架构取舍。没有其他背景可补充。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("认证", "登录", "auth", "jwt", "session"),
            ("postgresql", "postgres", "数据库"),
            ("rest api", "restful", "接口", "api"),
            ("自动化测试", "单元测试", "integration test", "test"),
            ("docker", "容器"),
            ("架构", "取舍", "trade-off", "tradeoff"),
        ),
        time_capacity_minutes=7200,
    ),
    Scenario(
        key="python",
        direction="Python",
        initial_message=(
            "我想学习 Python Web 后端开发。我有 JavaScript 和基础 SQL 经验，每周可投入 10 小时，"
            "计划用 12 周完成。目标是独立做出一个带用户登录、PostgreSQL、REST API、自动化测试和 "
            "Docker 部署的 FastAPI 项目，并能解释架构取舍。主要使用 Windows，预算以免费资源为主。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 我有JavaScript和基础SQL经验，每周10小时，共12周；使用Windows和"
            "免费资源，交付带用户登录、PostgreSQL、REST API、自动化测试、Docker部署的FastAPI项目，"
            "并能够解释架构取舍。没有其他背景可补充。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("认证", "登录", "auth", "jwt", "session"),
            ("postgresql", "postgres", "数据库"),
            ("rest api", "restful", "接口", "api"),
            ("自动化测试", "单元测试", "integration test", "test"),
            ("docker", "容器"),
            ("架构", "取舍", "trade-off", "tradeoff"),
        ),
        time_capacity_minutes=7200,
    ),
    Scenario(
        key="swimming",
        direction="游泳",
        initial_message="我想学游泳",
        persona_response=(
            f"{INDEPENDENT_GOAL} 我是成人初学者。计划12周，"
            "每周只在有救生员的正规泳池训练2次，目标连续自由泳500米。我接受认证教练指导；其他信息未知。"
        ),
        requires_clarification=True,
        keyword_groups=(
            ("循序渐进", "渐进", "progressive", "分阶段"),
            ("救生员", "正规泳池", "lifeguard", "supervised pool"),
            ("教练", "专业指导", "coach", "instructor"),
            ("500米", "500m", "五百米"),
        ),
        supporting_keyword_groups=(("停止", "疼痛", "眩晕", "呼吸异常", "stop condition"),),
        forbidden_keyword_groups=(("github", "readme", "fastapi"),),
    ),
    Scenario(
        key="skiing",
        direction="滑雪",
        initial_message=(
            "我是成人滑雪零基础，10周后要进行两天初级雪场行程。训练和行程都使用认证教练，"
            "租赁头盔与护具，目标是安全完成初级雪道。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 成人零基础，10周后参加两天初级雪场行程；使用认证教练，租赁头盔"
            "和护具，目标安全完成初级雪道。其他信息未知。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("循序渐进", "渐进", "progressive", "分阶段"),
            ("初级雪道", "绿道", "beginner slope", "green run"),
            ("认证教练", "教练", "instructor", "coach"),
            ("头盔", "护具", "helmet", "protective"),
        ),
        supporting_keyword_groups=(("停止", "失控", "疼痛", "能见度", "雪场关闭", "stop"),),
        forbidden_keyword_groups=(("github", "fastapi", "rest api"),),
    ),
    Scenario(
        key="spoken_english",
        direction="英语口语",
        initial_message=(
            "我有基础英语阅读能力，想提升英语口语。每周可投入6小时，计划16周，目标是完成10分钟"
            "英文技术面试和项目介绍。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 我有基础阅读能力，每周6小时，共16周；目标是完成10分钟英文技术面试"
            "和项目介绍。其他信息未知。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("16周", "十六周", "week 16", "阶段", "里程碑"),
            ("技术面试", "technical interview", "模拟面试", "mock interview"),
            ("项目介绍", "project introduction", "project pitch"),
            ("10分钟", "十分钟", "10-minute"),
            ("录音", "逐字稿", "recording", "transcript", "作品"),
            ("每周6小时", "6小时", "six hours", "weekly"),
        ),
        time_capacity_minutes=5760,
    ),
    Scenario(
        key="job_search",
        direction="求职",
        initial_message=(
            "我有两年前端开发经验，想在12周内转向初级后端岗位，每周投入8小时。目标是完成后端作品集、"
            "针对性简历、技术面试准备并形成稳定投递节奏。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 我有两年前端经验，用12周转向初级后端岗位，每周8小时；"
            "交付后端作品集、针对性简历、面试材料并形成稳定投递节奏。其他信息未知。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("12周", "十二周", "阶段", "里程碑"),
            ("作品集", "portfolio", "项目"),
            ("简历", "resume", "cv"),
            ("面试", "interview"),
            ("投递", "application", "申请记录"),
            ("每周8小时", "8小时", "weekly"),
        ),
        time_capacity_minutes=5760,
    ),
    Scenario(
        key="fitness",
        direction="健身",
        initial_message=(
            "我是久坐的运动初学者，想用12周做到连续完成5公里，每周训练4次。"
            "训练中如出现疼痛、胸闷、眩晕或其他异常会立即停止并寻求专业评估。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 久坐初学者，12周、每周4次，目标连续完成5公里。"
            "疼痛、胸闷、眩晕或异常疲劳时停止训练并寻求医生或合格专业人员评估。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("循序渐进", "渐进", "跑走", "progressive", "run-walk"),
            ("5公里", "5km", "五公里"),
            ("每周4次", "四次", "4 sessions", "weekly"),
        ),
        supporting_keyword_groups=(
            ("停止", "疼痛", "胸闷", "眩晕", "stop"),
            ("专业评估", "医生", "专业人员", "medical", "professional"),
        ),
        forbidden_keyword_groups=(("github", "readme", "fastapi"),),
    ),
    Scenario(
        key="household_budget",
        direction="家庭预算",
        initial_message="我想把家庭财务安排好",
        persona_response=(
            f"{INDEPENDENT_GOAL} 家庭月税后收入1.2万元，固定支出6000元，已有储蓄5万元。"
            "目标是在12个月内建立覆盖6个月必要支出的应急金，每月复盘一次。只做现金流与应急金规划，"
            "不涉及证券、投资产品或真实交易建议。"
        ),
        requires_clarification=True,
        keyword_groups=(
            ("现金流", "收支", "cash flow", "预算"),
            ("应急金", "emergency fund", "紧急备用金"),
            ("6个月", "六个月", "six months"),
            ("12个月", "十二个月", "一年", "monthly"),
            ("复盘", "review", "检查"),
        ),
        forbidden_keyword_groups=(
            ("买入股票", "卖出股票", "股票推荐", "证券交易", "stock pick", "trade securities"),
        ),
    ),
    Scenario(
        key="photography",
        direction="摄影",
        initial_message=(
            "我是摄影初学者，每周可投入5小时，计划8周，预算1000元。目标是完成20张城市故事"
            "作品集，优先使用免费的Windows工具，并能说明选片和叙事思路。"
        ),
        persona_response=(
            f"{INDEPENDENT_GOAL} 摄影初学者，每周5小时，共8周，预算1000元；使用免费Windows"
            "工具，交付20张城市故事作品集，并说明选片、排序与叙事思路。"
        ),
        requires_clarification=False,
        keyword_groups=(
            ("8周", "八周", "阶段", "里程碑"),
            ("20张", "二十张", "20-photo", "作品集"),
            ("城市故事", "city story", "叙事"),
            ("选片", "排序", "编辑", "curat", "sequence"),
            ("windows", "免费工具", "free tool"),
            ("每周5小时", "5小时", "weekly"),
        ),
        time_capacity_minutes=2400,
        spending_limit_cny=1000,
    ),
)


def _safe_public_label(value: Any, *, max_length: int = 120) -> str:
    """Return a short public label without credential-shaped content."""

    rendered = str(value or "").strip()
    if not rendered:
        return ""
    if _SENSITIVE_LABEL_PATTERN.search(rendered):
        return "[redacted]"
    return rendered[:max_length]


def _sanitize_base_url(value: Any) -> str:
    """Keep only a safe HTTP origin; discard userinfo, paths and query data."""

    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").strip().casefold()
    if scheme not in {"http", "https"} or not hostname:
        return ""
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{rendered_host}{f':{port}' if port is not None else ''}"


class ReplayTransport(Protocol):
    """Read-only transport boundary used by the formal acceptance auditor."""

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        ...


class CommandTransport(ReplayTransport, Protocol):
    """Write-capable test double boundary for the in-process state machine."""

    def chat(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        ...


class HttpAuditTransport:
    """Production CLI transport. Deliberately exposes GET operations only."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 900.0) -> None:
        self.base_url = _sanitize_base_url(base_url)
        if not self.base_url:
            raise RunnerError("Audit base URL must be a valid HTTP origin")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def check_health(self) -> None:
        response = self.client.get("/api/health")
        response.raise_for_status()

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/command/thread/{thread_id}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RunnerError("Thread replay returned a non-object response")
        return payload


class RunnerError(RuntimeError):
    pass


@dataclass
class ScenarioResult:
    key: str
    direction: str
    thread_id: str = ""
    session_id: str = ""
    clarification_rounds: int = 0
    recommended_strategy_id: str = ""
    task_count: int = 0
    total_estimated_minutes: int = 0
    budget_allocated_cny: int | None = None
    resource_coverage: str = ""
    critic_score: int | None = None
    repair_count: int = 0
    strategy_approval_count: int = 0
    final_status: str = ""
    business_status: str = ""
    runtime_status: str = ""
    replay_verified: bool = False
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    model_failures: list[dict[str, Any]] = field(default_factory=list)
    model_stage_summary: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    wall_time_ms: int = 0
    model_request_count: int = 0
    model_latency_ms: int = 0
    rate_limit_count: int = 0
    truncation_count: int = 0
    contract_repair_count: int = 0
    automatic_retry_count: int = 0
    generation_modes: list[str] = field(default_factory=list)
    stage_performance: list[dict[str, Any]] = field(default_factory=list)
    request_intervals: list[dict[str, str]] = field(default_factory=list, repr=False)


Observer = Callable[[dict[str, Any]], None]


def _normalize(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return unicodedata.normalize("NFKC", rendered).casefold()


def _contains_non_negated_term(normalized_text: str, normalized_term: str) -> bool:
    negations = (
        "不",
        "不要",
        "不得",
        "不建议",
        "不提供",
        "不涉及",
        "不进行",
        "不执行",
        "不允许",
        "不推荐",
        "禁止",
        "避免",
        "避免进行",
        "不会",
        "无需",
        "无须",
        "not",
        "do not",
        "don't",
        "should not",
        "must not",
        "will not",
        "never",
        "avoid",
        "prohibit",
    )
    start = 0
    while True:
        index = normalized_text.find(normalized_term, start)
        if index < 0:
            return False
        prefix = normalized_text[max(0, index - 24):index]
        boundary = max(prefix.rfind(mark) for mark in ("。", "！", "？", ";", "；", "\n"))
        clause_tail = prefix[boundary + 1:].rstrip(" \t,，:：\"'")
        if not any(clause_tail.endswith(marker) for marker in negations):
            return True
        start = index + len(normalized_term)


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or "")


def _last_event(events: Sequence[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    return next((event for event in reversed(events) if _event_type(event) == event_type), None)


def _event_data(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _payload_data(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _safe_model_failure(event: dict[str, Any]) -> dict[str, Any]:
    failure = event.get("modelFailure")
    if not isinstance(failure, dict):
        return {}
    attempts = []
    for attempt in failure.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        attempts.append(
            {
                "provider": _safe_public_label(attempt.get("provider")),
                "status": _safe_public_label(attempt.get("status"), max_length=40),
                "errorType": _safe_public_label(attempt.get("errorType"), max_length=80),
            }
        )
    return {
        "stage": _safe_public_label(failure.get("stage"), max_length=80),
        "resumeNode": _safe_public_label(failure.get("resumeNode"), max_length=80),
        "retryable": bool(failure.get("retryable")),
        "automaticRetryAttempted": bool(failure.get("automaticRetryAttempted")),
        "attempts": attempts,
    }


def _looks_non_real(label: str) -> bool:
    normalized = label.strip().casefold()
    tokens = normalized
    for separator in ("-", "_", "/", ".", " "):
        tokens = tokens.replace(separator, " ")
    return bool(set(tokens.split()) & NON_REAL_MODEL_MARKERS)


def _verify_model_usage(
    usage: Any,
    *,
    stage: str,
    required_provider: str,
) -> tuple[str, str]:
    if not isinstance(usage, dict):
        raise RunnerError(f"{stage} has no public modelUsage proof")
    raw_provider = str(usage.get("provider") or "").strip()
    raw_model = str(usage.get("model") or "").strip()
    provider = _safe_public_label(raw_provider)
    model = _safe_public_label(raw_model)
    if str(usage.get("taskType") or "") != stage:
        raise RunnerError(f"{stage} modelUsage has the wrong task type")
    if str(usage.get("mode") or "").casefold() != "llm":
        raise RunnerError(f"{stage} was not produced in llm mode")
    if usage.get("localFallbackAllowed") is not False:
        raise RunnerError(f"{stage} did not explicitly disable local fallback")
    if (
        not provider
        or not model
        or "[redacted]" in {provider, model}
        or _looks_non_real(provider)
        or _looks_non_real(model)
    ):
        raise RunnerError(f"{stage} does not identify a real provider and model")
    if required_provider and provider.casefold() != required_provider:
        raise RunnerError(f"{stage} did not use the required provider")
    attempts = usage.get("attempts")
    if not isinstance(attempts, list):
        raise RunnerError(f"{stage} has no public routing-attempt proof")
    successful = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict)
        and str(attempt.get("status") or "") == "success"
        and str(attempt.get("provider") or "").strip().casefold() == provider.casefold()
    ]
    if not successful:
        raise RunnerError(f"{stage} has no successful attempt for its reported provider")
    if required_provider and not any(
        str(attempt.get("provider") or "").strip().casefold() == required_provider
        for attempt in successful
    ):
        raise RunnerError(f"{stage} has no successful attempt for the required provider")
    return provider, model


def _has_successful_model_usage(decision: dict[str, Any]) -> bool:
    usage = decision.get("modelUsage")
    attempts = usage.get("attempts") if isinstance(usage, dict) else None
    return isinstance(attempts, list) and any(
        isinstance(attempt, dict)
        and str(attempt.get("status") or "").casefold() == "success"
        for attempt in attempts
    )


def _audit_model_provenance(
    messages: Sequence[dict[str, Any]],
    session_cards: Sequence[dict[str, Any]],
    *,
    required_provider: str,
) -> list[dict[str, Any]]:
    identities: dict[tuple[str, str, str], int] = {}

    understanding_cards = [
        message
        for message in messages
        if message.get("role") == "card" and message.get("kind") == "goal_understanding"
    ]
    if not understanding_cards:
        raise RunnerError("Thread replay has no Goal Understanding model proof")
    for message in understanding_cards:
        payload = message.get("payload")
        if not isinstance(payload, dict) or str(payload.get("source") or "") != "llm":
            raise RunnerError("Goal Understanding was not sourced from a real llm")
        provider, model = _verify_model_usage(
            payload.get("modelUsage"),
            stage="goal_understanding",
            required_provider=required_provider,
        )
        key = ("goal_understanding", provider, model)
        identities[key] = identities.get(key, 0) + 1

    for stage, agent, accepted_decisions in REQUIRED_MODEL_STAGES:
        decisions = []
        for message in session_cards:
            if message.get("kind") != "agent_decision":
                continue
            data = _payload_data(message)
            output_ids = data.get("outputArtifactIds")
            if (
                str(data.get("agent") or "") == agent
                and str(data.get("decision") or "") in accepted_decisions
                and isinstance(output_ids, list)
                and bool(output_ids)
                and _has_successful_model_usage(data)
            ):
                decisions.append(data)
        if not decisions:
            raise RunnerError(f"{stage} has no public artifact-producing agent decision")
        for decision in decisions:
            provider, model = _verify_model_usage(
                decision.get("modelUsage"),
                stage=stage,
                required_provider=required_provider,
            )
            key = (stage, provider, model)
            identities[key] = identities.get(key, 0) + 1

    stage_order = {
        stage: index
        for index, stage in enumerate(
            ("goal_understanding", *(item[0] for item in REQUIRED_MODEL_STAGES))
        )
    }
    return [
        {"stage": stage, "provider": provider, "model": model, "proofCount": count}
        for (stage, provider, model), count in sorted(
            identities.items(), key=lambda item: (stage_order[item[0][0]], item[0][1], item[0][2])
        )
    ]


def _parse_timestamp(value: Any) -> datetime | None:
    rendered = str(value or "").strip()
    if not rendered:
        return None
    try:
        return datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return None


def _model_usage_records(
    messages: Sequence[dict[str, Any]],
    session_cards: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return successful artifact usage and deduplicated failed route usage.

    Artifact-producing decisions are authoritative and are retained even when
    two repair versions happen to have identical metadata. Standalone usage
    cards and repeated blocked-status snapshots are added only when they do not
    duplicate an authoritative usage or an earlier failure snapshot.
    """

    def fingerprint(usage: dict[str, Any]) -> str:
        attempts = []
        for attempt in usage.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            attempts.append(
                {
                    "provider": _safe_public_label(attempt.get("provider")),
                    "model": _safe_public_label(attempt.get("model")),
                    "status": _safe_public_label(attempt.get("status"), max_length=40),
                    "errorType": _safe_public_label(attempt.get("errorType"), max_length=80),
                    "latencyMs": attempt.get("latencyMs")
                    if isinstance(attempt.get("latencyMs"), (int, float))
                    else None,
                    "automaticRetry": attempt.get("automaticRetry") is True,
                    "retryReason": _safe_public_label(
                        attempt.get("retryReason"), max_length=80
                    ),
                }
            )
        return json.dumps(
            {
                "taskType": _safe_public_label(usage.get("taskType"), max_length=80),
                "provider": _safe_public_label(usage.get("provider")),
                "model": _safe_public_label(usage.get("model")),
                "latencyMs": usage.get("latencyMs")
                if isinstance(usage.get("latencyMs"), (int, float))
                else None,
                "generationMode": _safe_public_label(
                    usage.get("generationMode"), max_length=40
                ),
                "attempts": attempts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    records: list[dict[str, Any]] = []
    authoritative_fingerprints: set[str] = set()
    for message in messages:
        if message.get("role") != "card" or message.get("kind") != "goal_understanding":
            continue
        payload = message.get("payload")
        usage = payload.get("modelUsage") if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            records.append(usage)
            authoritative_fingerprints.add(fingerprint(usage))

    accepted_by_agent = {
        agent: accepted for _stage, agent, accepted in REQUIRED_MODEL_STAGES
    }
    for message in session_cards:
        if message.get("kind") != "agent_decision":
            continue
        data = _payload_data(message)
        agent = str(data.get("agent") or "")
        if str(data.get("decision") or "") not in accepted_by_agent.get(agent, frozenset()):
            continue
        output_ids = data.get("outputArtifactIds")
        usage = data.get("modelUsage")
        if (
            isinstance(output_ids, list)
            and output_ids
            and isinstance(usage, dict)
            and isinstance(usage.get("attempts"), list)
        ):
            records.append(usage)
            authoritative_fingerprints.add(fingerprint(usage))

    supplemental_fingerprints: set[str] = set()
    for message in messages:
        if message.get("role") != "card" or message.get("kind") != "model_usage":
            continue
        payload = message.get("payload")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            continue
        usage_fingerprint = fingerprint(usage)
        if (
            usage_fingerprint in authoritative_fingerprints
            or usage_fingerprint in supplemental_fingerprints
        ):
            continue
        records.append(usage)
        supplemental_fingerprints.add(usage_fingerprint)

    failure_fingerprints: set[str] = set()
    for message in session_cards:
        if message.get("kind") != "planning_session_status":
            continue
        payload = message.get("payload")
        failure = payload.get("modelFailure") if isinstance(payload, dict) else None
        attempts = failure.get("attempts") if isinstance(failure, dict) else None
        if not isinstance(attempts, list):
            continue
        failure_usage = {
            "taskType": failure.get("stage") or failure.get("resumeNode") or "unknown",
            "attempts": attempts,
        }
        usage_fingerprint = fingerprint(failure_usage)
        if usage_fingerprint in failure_fingerprints:
            continue
        records.append(failure_usage)
        failure_fingerprints.add(usage_fingerprint)
    return records


def _request_intervals(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Approximate browser request intervals without counting human think time."""

    user_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    intervals: list[dict[str, str]] = []
    for position, start_index in enumerate(user_indexes):
        start = _parse_timestamp(messages[start_index].get("createdAt"))
        if start is None:
            continue
        stop_index = user_indexes[position + 1] if position + 1 < len(user_indexes) else len(messages)
        response_times = [
            timestamp
            for message in messages[start_index + 1 : stop_index]
            if message.get("role") != "user"
            and (timestamp := _parse_timestamp(message.get("createdAt"))) is not None
        ]
        if not response_times:
            continue
        end = max(response_times)
        if end < start:
            continue
        if end == start:
            # SQLite timestamps can be coarser than a fast mock response. Keep
            # the request observable without inventing material wall time.
            end = start + timedelta(milliseconds=1)
        intervals.append(
            {
                "startedAt": start.astimezone(timezone.utc).isoformat(),
                "completedAt": end.astimezone(timezone.utc).isoformat(),
            }
        )
    return intervals


def _audit_performance(
    messages: Sequence[dict[str, Any]],
    session_cards: Sequence[dict[str, Any]],
    result: ScenarioResult,
) -> None:
    first_user = next(
        (message for message in messages if message.get("role") == "user"),
        None,
    )
    final_status = next(
        (
            message
            for message in reversed(session_cards)
            if message.get("kind") == "planning_session_status"
        ),
        None,
    )
    started = _parse_timestamp(first_user.get("createdAt") if isinstance(first_user, dict) else None)
    completed = _parse_timestamp(final_status.get("createdAt") if isinstance(final_status, dict) else None)
    if started is not None:
        result.started_at = started.astimezone(timezone.utc).isoformat()
    if completed is not None:
        result.completed_at = completed.astimezone(timezone.utc).isoformat()
    if started is not None and completed is not None and completed >= started:
        result.wall_time_ms = int((completed - started).total_seconds() * 1000)
    result.request_intervals = _request_intervals(messages)

    generation_modes: set[str] = set()
    stage_totals: dict[str, dict[str, Any]] = {}
    for usage in _model_usage_records(messages, session_cards):
        stage = _safe_public_label(usage.get("taskType"), max_length=80) or "unknown"
        stage_total = stage_totals.setdefault(
            stage,
            {
                "stage": stage,
                "modelRequestCount": 0,
                "modelLatencyMs": 0,
                "rateLimitCount": 0,
                "truncationCount": 0,
                "contractRepairCount": 0,
                "automaticRetryCount": 0,
                "generationModes": set(),
            },
        )
        mode = str(usage.get("generationMode") or "").strip()
        if mode:
            generation_modes.add(mode)
            stage_total["generationModes"].add(_safe_public_label(mode, max_length=40))
        attempts = usage.get("attempts")
        actual_attempts = [
            attempt
            for attempt in (attempts if isinstance(attempts, list) else [])
            if isinstance(attempt, dict)
            and str(attempt.get("status") or "").casefold() in {"success", "error"}
        ]
        attempt_latencies = [
            int(attempt["latencyMs"])
            for attempt in actual_attempts
            if isinstance(attempt.get("latencyMs"), (int, float))
            and attempt["latencyMs"] >= 0
        ]
        if attempt_latencies:
            usage_latency = sum(attempt_latencies)
        else:
            latency = usage.get("latencyMs")
            usage_latency = int(latency) if isinstance(latency, (int, float)) and latency >= 0 else 0
        result.model_latency_ms += usage_latency
        result.model_request_count += len(actual_attempts)
        stage_total["modelLatencyMs"] += usage_latency
        stage_total["modelRequestCount"] += len(actual_attempts)
        for attempt in actual_attempts:
            error_type = str(attempt.get("errorType") or "").strip()
            if error_type == "rate_limit":
                result.rate_limit_count += 1
                stage_total["rateLimitCount"] += 1
            if error_type == "model_output_truncated":
                result.truncation_count += 1
                stage_total["truncationCount"] += 1
            if attempt.get("automaticRetry") is True:
                result.automatic_retry_count += 1
                stage_total["automaticRetryCount"] += 1
            if str(attempt.get("retryReason") or "") == "contract_validation":
                result.contract_repair_count += 1
                stage_total["contractRepairCount"] += 1
    result.generation_modes = sorted(generation_modes)
    # Sets are useful while aggregating but never cross the report boundary.
    result.stage_performance = [
        {
            **{key: value for key, value in item.items() if key != "generationModes"},
            "generationModes": sorted(item["generationModes"]),
        }
        for _stage, item in sorted(stage_totals.items())
    ]


def _batch_performance(results: Sequence[ScenarioResult]) -> dict[str, Any]:
    overall_intervals: list[tuple[datetime, datetime]] = []
    request_intervals: list[tuple[datetime, datetime]] = []
    for result in results:
        started = _parse_timestamp(result.started_at)
        completed = _parse_timestamp(result.completed_at)
        if started is not None and completed is not None and completed >= started:
            overall_intervals.append((started, completed))
        for interval in result.request_intervals:
            request_started = _parse_timestamp(interval.get("startedAt"))
            request_completed = _parse_timestamp(interval.get("completedAt"))
            if (
                request_started is not None
                and request_completed is not None
                and request_completed > request_started
            ):
                request_intervals.append((request_started, request_completed))

    batch_wall_time_ms = 0
    max_observed_concurrent = 0
    concurrency_utilization = 0.0
    batch_started_at = ""
    batch_completed_at = ""
    if overall_intervals:
        batch_start = min(start for start, _end in overall_intervals)
        batch_end = max(end for _start, end in overall_intervals)
        batch_started_at = batch_start.astimezone(timezone.utc).isoformat()
        batch_completed_at = batch_end.astimezone(timezone.utc).isoformat()
        batch_wall_time_ms = int((batch_end - batch_start).total_seconds() * 1000)

    if request_intervals:
        # End events sort before start events at the same timestamp so touching
        # intervals are not reported as concurrent work.
        events = sorted(
            [
                event
                for start, end in request_intervals
                for event in ((start, 1), (end, -1))
            ],
            key=lambda item: (item[0], item[1]),
        )
        active = 0
        active_area_ms = 0
        previous: datetime | None = None
        for timestamp, delta in events:
            if previous is not None and timestamp >= previous:
                active_area_ms += int((timestamp - previous).total_seconds() * 1000) * active
            active += delta
            max_observed_concurrent = max(max_observed_concurrent, active)
            previous = timestamp
        denominator = batch_wall_time_ms * PLANNING_CONCURRENCY_LIMIT
        if denominator > 0:
            concurrency_utilization = round(active_area_ms / denominator, 4)

    return {
        "batchStartedAt": batch_started_at,
        "batchCompletedAt": batch_completed_at,
        "batchWallTimeMs": batch_wall_time_ms,
        "modelRequestCount": sum(item.model_request_count for item in results),
        "modelLatencyMs": sum(item.model_latency_ms for item in results),
        "rateLimitCount": sum(item.rate_limit_count for item in results),
        "truncationCount": sum(item.truncation_count for item in results),
        "contractRepairCount": sum(item.contract_repair_count for item in results),
        "automaticRetryCount": sum(item.automatic_retry_count for item in results),
        "generationModes": sorted({mode for item in results for mode in item.generation_modes}),
        "maxObservedConcurrent": max_observed_concurrent,
        "concurrencyLimit": PLANNING_CONCURRENCY_LIMIT,
        "concurrencyUtilization": concurrency_utilization,
    }


class PlanningScenarioRunner:
    def __init__(
        self,
        transport: ReplayTransport,
        *,
        observer: Observer | None = None,
        max_turns: int = 20,
        max_model_retries_per_stage: int = 2,
        context_date: str = "2026-07-12",
        required_provider: str | None = None,
    ) -> None:
        self.transport = transport
        self.observer = observer or (lambda _event: None)
        self.max_turns = max_turns
        self.max_model_retries_per_stage = max_model_retries_per_stage
        self.context_date = context_date
        self.required_provider = (required_provider or "").strip().casefold()

    def _notify(self, **payload: Any) -> None:
        self.observer(payload)

    def run(self, scenario: Scenario) -> ScenarioResult:
        result = ScenarioResult(key=scenario.key, direction=scenario.direction)
        all_events: list[dict[str, Any]] = []
        first_turn_events: list[dict[str, Any]] = []
        retry_counts: dict[str, int] = {}
        next_message = scenario.initial_message
        approved_strategy_artifact_ids: set[str] = set()
        turn = 0

        try:
            while turn < self.max_turns:
                payload: dict[str, Any] = {
                    "message": next_message,
                    "mode": "auto",
                    "permission": "low",
                    "context": {
                        "date": self.context_date,
                        "language": "zh-CN",
                        "source": "zero_start_live_e2e",
                        "scenario": scenario.key,
                    },
                }
                if result.thread_id:
                    payload["threadId"] = result.thread_id
                elif turn != 0:
                    raise RunnerError("A follow-up was attempted without the first turn's threadId")

                self._notify(event="turn_started", scenario=scenario.key, turn=turn + 1)
                chat = getattr(self.transport, "chat", None)
                if not callable(chat):
                    raise RunnerError(
                        "The in-process state-machine runner requires a test transport"
                    )
                events = chat(payload)
                self._validate_ndjson_envelope(events)
                if turn == 0:
                    first_turn_events = list(events)
                all_events.extend(events)

                returned_thread_id = self._thread_id(events)
                if not returned_thread_id:
                    raise RunnerError("Command stream did not return a threadId")
                if result.thread_id and returned_thread_id != result.thread_id:
                    raise RunnerError("A follow-up switched to a different thread")
                result.thread_id = returned_thread_id
                if any(returned_thread_id.startswith(prefix) for prefix in EXCLUDED_OLD_THREAD_IDS):
                    raise RunnerError("The runner reused an excluded legacy test thread")

                session_ids = {
                    str(event.get("sessionId"))
                    for event in events
                    if event.get("sessionId")
                }
                if len(session_ids) > 1:
                    raise RunnerError("One command turn emitted more than one Planning Session")
                if session_ids:
                    session_id = next(iter(session_ids))
                    if result.session_id and result.session_id != session_id:
                        raise RunnerError("The scenario switched to a different Planning Session")
                    result.session_id = session_id

                status_event = _last_event(events, "planning_session_status")
                status = str(status_event.get("status") or "") if status_event else ""
                self._notify(
                    event="turn_finished",
                    scenario=scenario.key,
                    turn=turn + 1,
                    status=status or "goal_understanding",
                    eventTypes=sorted({_event_type(item) for item in events}),
                )

                stream_error = _last_event(events, "error")
                if stream_error:
                    # Do not copy the server's detail/error text into logs or reports.
                    raise RunnerError("Command stream emitted an error event")

                if status == "MODEL_UNAVAILABLE":
                    safe_failure = _safe_model_failure(status_event or {})
                    if safe_failure:
                        result.model_failures.append(safe_failure)
                    stage = str(safe_failure.get("resumeNode") or safe_failure.get("stage") or "unknown")
                    count = retry_counts.get(stage, 0)
                    if not safe_failure.get("retryable") or count >= self.max_model_retries_per_stage:
                        raise RunnerError(f"Planning model did not recover at stage {stage}")
                    retry_counts[stage] = count + 1
                    next_message = RETRY_MESSAGE
                    turn += 1
                    continue

                ambiguous = any(
                    _event_type(event) == "goal_understanding"
                    and str(event.get("intentState") or "") == "ambiguous_goal"
                    for event in events
                )
                if ambiguous or status == "needs_goal_clarification":
                    result.clarification_rounds += 1
                    if result.clarification_rounds > 4:
                        raise RunnerError("Goal clarification did not converge within four fixed-fact replies")
                    next_message = scenario.persona_response
                    turn += 1
                    continue

                if status == "waiting_design_approval":
                    strategy_event = _last_event(events, "strategy_portfolio_ready") or _last_event(
                        all_events, "strategy_portfolio_ready"
                    )
                    strategy = _event_data(strategy_event)
                    recommended = str(strategy.get("recommendedStrategyId") or "")
                    strategy_ids = {
                        str(item.get("id") or "")
                        for item in strategy.get("strategies") or []
                        if isinstance(item, dict)
                    }
                    if not recommended or recommended not in strategy_ids:
                        raise RunnerError("The Strategy portfolio has no valid recommendedStrategyId")
                    strategy_decisions = [
                        _event_data(event)
                        for event in events
                        if _event_type(event) == "agent_decision"
                        and str(_event_data(event).get("agent") or "") == "Strategy Agent"
                        and str(_event_data(event).get("decision") or "") == "request_user_input"
                    ]
                    if not strategy_decisions:
                        raise RunnerError("Design approval did not expose a new Strategy artifact decision")
                    strategy_outputs = strategy_decisions[-1].get("outputArtifactIds")
                    if not isinstance(strategy_outputs, list) or len(strategy_outputs) != 1:
                        raise RunnerError("Strategy approval target is not a single public artifact version")
                    strategy_artifact_id = str(strategy_outputs[0] or "")
                    if not strategy_artifact_id:
                        raise RunnerError("Strategy approval target has no artifact id")
                    if strategy_artifact_id in approved_strategy_artifact_ids:
                        raise RunnerError("The same Strategy artifact was presented for duplicate approval")
                    result.recommended_strategy_id = recommended
                    approved_strategy_artifact_ids.add(strategy_artifact_id)
                    result.strategy_approval_count += 1
                    next_message = STRATEGY_APPROVAL_MESSAGE
                    turn += 1
                    continue

                if status == "waiting_execution_approval":
                    if not approved_strategy_artifact_ids:
                        raise RunnerError("Execution appeared before the runner approved Strategy")
                    break

                if status == "execution_revision":
                    raise RunnerError("Critic repair loop ended without a passing Execution Blueprint")

                if status:
                    raise RunnerError(f"Unexpected planning status: {status}")
                understanding_unavailable = any(
                    _event_type(event) == "model_usage"
                    and str(event.get("feature") or "") == "goal_understanding"
                    and str(event.get("source") or "") == "model_unavailable"
                    for event in events
                )
                if understanding_unavailable:
                    count = retry_counts.get("goal_understanding", 0)
                    if count >= self.max_model_retries_per_stage:
                        raise RunnerError("Goal Understanding did not recover before Session creation")
                    retry_counts["goal_understanding"] = count + 1
                    next_message = scenario.initial_message
                    turn += 1
                    continue
                if not ambiguous:
                    raise RunnerError("The turn produced neither a planning status nor a clarification")
            else:
                raise RunnerError(f"Scenario exceeded {self.max_turns} Command turns")

            if not result.session_id:
                raise RunnerError("No Planning Session was created")
            if scenario.requires_clarification and result.clarification_rounds == 0:
                raise RunnerError("The sparse first turn did not trigger real model clarification")

            self._audit_first_turn(first_turn_events, result)
            replay = self.transport.get_thread(result.thread_id)
            self._audit_replay(scenario, result, replay)
            result.passed = not result.failures
        except RunnerError as exc:
            result.failures.append(str(exc))
            result.passed = False
        except httpx.HTTPError:
            result.failures.append("Command transport returned an HTTP error")
            result.passed = False
        except Exception as exc:  # pragma: no cover - last-resort live-run containment
            result.failures.append(f"Unexpected runner failure: {type(exc).__name__}")
            result.passed = False

        self._notify(
            event="scenario_finished",
            scenario=scenario.key,
            passed=result.passed,
            threadId=result.thread_id,
            sessionId=result.session_id,
            status=result.final_status,
            failures=list(result.failures),
        )
        return result

    def audit_thread(self, scenario: Scenario, thread_id: str) -> ScenarioResult:
        """Audit a browser-created Thread without advancing it.

        This path deliberately performs exactly one replay GET and never calls
        a write-capable transport. The unique Planning Session is discovered
        from persisted cards so the manifest needs to contain only scenario
        keys and Thread ids.
        """

        result = ScenarioResult(
            key=scenario.key,
            direction=scenario.direction,
            thread_id=str(thread_id or "").strip(),
        )
        try:
            if not result.thread_id:
                raise RunnerError("Read-only audit manifest contains an empty threadId")
            if any(result.thread_id.startswith(prefix) for prefix in EXCLUDED_OLD_THREAD_IDS):
                raise RunnerError("The audit manifest references an excluded legacy test thread")
            replay = self.transport.get_thread(result.thread_id)
            result.session_id = self._session_id_from_replay(replay)
            result.clarification_rounds = self._clarification_rounds_from_replay(replay)
            if scenario.requires_clarification and result.clarification_rounds == 0:
                raise RunnerError("The sparse first turn did not trigger persisted model clarification")
            self._audit_replay(scenario, result, replay)
            result.passed = not result.failures
        except RunnerError as exc:
            result.failures.append(str(exc))
            result.passed = False
        except httpx.HTTPError:
            result.failures.append("Thread replay returned an HTTP error")
            result.passed = False
        except Exception as exc:  # pragma: no cover - last-resort live-run containment
            result.failures.append(f"Unexpected runner failure: {type(exc).__name__}")
            result.passed = False

        self._notify(
            event="scenario_audited",
            scenario=scenario.key,
            passed=result.passed,
            threadId=result.thread_id,
            sessionId=result.session_id,
            status=result.final_status,
            failures=list(result.failures),
        )
        return result

    @staticmethod
    def _validate_ndjson_envelope(events: Sequence[dict[str, Any]]) -> None:
        if not events:
            raise RunnerError("Command stream returned no NDJSON events")
        if _event_type(events[0]) != "thread":
            raise RunnerError("The first NDJSON event was not the thread envelope")
        done_events = [event for event in events if _event_type(event) == "done"]
        if len(done_events) != 1 or events[-1] is not done_events[0]:
            raise RunnerError("Command stream did not end with exactly one done event")

    @staticmethod
    def _thread_id(events: Sequence[dict[str, Any]]) -> str:
        ids = {
            str(event.get("threadId"))
            for event in events
            if _event_type(event) in {"thread", "done"} and event.get("threadId")
        }
        if len(ids) != 1:
            return ""
        return next(iter(ids))

    @staticmethod
    def _session_id_from_replay(replay: dict[str, Any]) -> str:
        messages = replay.get("messages")
        if not isinstance(messages, list):
            raise RunnerError("Thread replay has no message list")
        session_ids = {
            str(payload.get("sessionId"))
            for message in messages
            if isinstance(message, dict)
            and isinstance((payload := message.get("payload")), dict)
            and payload.get("sessionId")
        }
        if len(session_ids) != 1:
            raise RunnerError("Thread replay must contain exactly one Planning Session")
        return next(iter(session_ids))

    @staticmethod
    def _clarification_rounds_from_replay(replay: dict[str, Any]) -> int:
        messages = replay.get("messages")
        if not isinstance(messages, list):
            return 0
        user_messages = [
            message
            for message in messages
            if isinstance(message, dict)
            and message.get("role") == "user"
            and message.get("kind") == "text"
        ]
        if not user_messages:
            return 0
        rounds = 0
        for message in user_messages[1:]:
            content = str(message.get("content") or "").strip()
            if content == STRATEGY_APPROVAL_MESSAGE:
                break
            if content != RETRY_MESSAGE:
                rounds += 1
        return rounds

    @staticmethod
    def _audit_first_turn(events: Sequence[dict[str, Any]], result: ScenarioResult) -> None:
        first_thread = _last_event(events, "thread")
        if not first_thread or str(first_thread.get("threadId") or "") != result.thread_id:
            raise RunnerError("The first request did not establish the final scenario thread")

    def _audit_replay(
        self,
        scenario: Scenario,
        result: ScenarioResult,
        replay: dict[str, Any],
    ) -> None:
        if str(replay.get("id") or "") != result.thread_id:
            raise RunnerError("Thread replay returned a different thread")
        messages = replay.get("messages")
        if not isinstance(messages, list):
            raise RunnerError("Thread replay has no message list")

        first_user_message = next(
            (message for message in messages if message.get("role") == "user"),
            None,
        )
        if (
            not isinstance(first_user_message, dict)
            or first_user_message.get("kind") != "text"
            or first_user_message.get("content") != scenario.initial_message
        ):
            raise RunnerError("The first persisted user message does not equal the scenario opening")

        cards = [message for message in messages if message.get("role") == "card"]
        session_cards = [
            message
            for message in cards
            if isinstance(message.get("payload"), dict)
            and str(message["payload"].get("sessionId") or "") == result.session_id
        ]
        foreign_session_cards = [
            message
            for message in cards
            if isinstance(message.get("payload"), dict)
            and message["payload"].get("sessionId")
            and str(message["payload"].get("sessionId")) != result.session_id
        ]
        if foreign_session_cards:
            raise RunnerError("Fresh thread replay contains inherited Planning Session artifacts")

        kinds = {str(message.get("kind") or "") for message in session_cards}
        missing = sorted(REQUIRED_REPLAY_KINDS - kinds)
        if missing:
            raise RunnerError(f"Thread replay is missing required artifacts: {', '.join(missing)}")
        forbidden = sorted(FORBIDDEN_REPLAY_KINDS & {str(message.get("kind") or "") for message in messages})
        if forbidden:
            raise RunnerError(f"Thread replay contains forbidden execution/calendar artifacts: {', '.join(forbidden)}")
        if replay.get("currentDraft") is not None:
            raise RunnerError("Cognitive planning unexpectedly created a legacy Command draft")

        first_stage_indexes: list[int] = []
        for _label, kind in REQUIRED_ARTIFACT_SEQUENCE:
            first_stage_indexes.append(
                next(
                    index
                    for index, message in enumerate(messages)
                    if message.get("kind") == kind
                    and isinstance(message.get("payload"), dict)
                    and str(message["payload"].get("sessionId") or "") == result.session_id
                )
            )
        if first_stage_indexes != sorted(first_stage_indexes) or len(
            set(first_stage_indexes)
        ) != len(first_stage_indexes):
            rendered = " -> ".join(label for label, _kind in REQUIRED_ARTIFACT_SEQUENCE)
            raise RunnerError(f"Planning artifacts are not persisted in strict order: {rendered}")

        result.model_stage_summary = _audit_model_provenance(
            messages,
            session_cards,
            required_provider=self.required_provider,
        )
        _audit_performance(messages, session_cards, result)

        start_indexes = [
            index for index, message in enumerate(messages)
            if message.get("kind") == "planning_session_started"
            and isinstance(message.get("payload"), dict)
            and message["payload"].get("sessionId") == result.session_id
        ]
        if len(start_indexes) != 1:
            raise RunnerError("Fresh scenario must have exactly one planning_session_started card")
        first_artifact_index = min(
            index for index, message in enumerate(messages)
            if message.get("kind") in REQUIRED_REPLAY_KINDS
            and isinstance(message.get("payload"), dict)
            and message["payload"].get("sessionId") == result.session_id
        )
        if start_indexes[0] > first_artifact_index:
            raise RunnerError("Planning artifacts appeared before the fresh session started")

        status_message = next(
            message for message in reversed(session_cards)
            if message.get("kind") == "planning_session_status"
        )
        status_payload = status_message.get("payload") or {}
        result.final_status = str(status_payload.get("status") or "")
        result.business_status = str(status_payload.get("businessStatus") or "")
        result.runtime_status = str(status_payload.get("runtimeStatus") or "")
        if (
            result.final_status,
            result.business_status,
            result.runtime_status,
        ) != ("waiting_execution_approval", "execution_pending", "idle"):
            raise RunnerError(
                "Final status tuple is not waiting_execution_approval/execution_pending/idle"
            )

        strategy_message = next(
            message for message in reversed(session_cards)
            if message.get("kind") == "strategy_portfolio_ready"
        )
        strategy = _payload_data(strategy_message)
        recommended = str(strategy.get("recommendedStrategyId") or "")
        strategy_ids = [
            str(item.get("id") or "")
            for item in strategy.get("strategies") or []
            if isinstance(item, dict)
        ]
        if not recommended or recommended not in strategy_ids or len(strategy_ids) != len(set(strategy_ids)):
            raise RunnerError("Replayed Strategy recommendation is missing or invalid")
        if result.recommended_strategy_id and result.recommended_strategy_id != recommended:
            raise RunnerError("Replayed Strategy differs from the strategy the runner approved")
        result.recommended_strategy_id = recommended

        execution_message = next(
            message for message in reversed(session_cards)
            if message.get("kind") == "execution_blueprint_ready"
        )
        critique_message = next(
            message for message in reversed(session_cards)
            if message.get("kind") == "critique_report_ready"
        )
        execution = _payload_data(execution_message)
        critique = _payload_data(critique_message)
        self._audit_execution(scenario, result, execution)
        self._audit_critique(result, critique)
        self._audit_artifact_lineage(messages, session_cards, result)

        result.replay_verified = True

    @staticmethod
    def _audit_execution(
        scenario: Scenario,
        result: ScenarioResult,
        execution: dict[str, Any],
    ) -> None:
        tasks = execution.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise RunnerError("Execution Blueprint has no tasks")
        ids = [str(task.get("id") or "") for task in tasks if isinstance(task, dict)]
        if len(ids) != len(tasks) or any(not task_id for task_id in ids) or len(ids) != len(set(ids)):
            raise RunnerError("Execution task ids are missing or duplicated")
        positions = {task_id: index for index, task_id in enumerate(ids)}

        total_minutes = 0
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise RunnerError("Execution contains a non-object task")
            required_text = ("title", "purpose", "whyNow", "deliverable", "fallbackAction")
            if any(not str(task.get(field) or "").strip() for field in required_text):
                raise RunnerError(f"Execution task {ids[index]} is missing required narrative fields")
            if not isinstance(task.get("actionSteps"), list) or not task["actionSteps"]:
                raise RunnerError(f"Execution task {ids[index]} has no action steps")
            if not isinstance(task.get("completionEvidence"), list) or not task["completionEvidence"]:
                raise RunnerError(f"Execution task {ids[index]} has no completion evidence")
            if not isinstance(task.get("resources"), list) or not task["resources"]:
                raise RunnerError(f"Execution task {ids[index]} has no resources")
            if not isinstance(task.get("risks"), list) or not task["risks"]:
                raise RunnerError(f"Execution task {ids[index]} has no risks")
            if "dependencies" not in task or not isinstance(task.get("dependencies"), list):
                raise RunnerError(f"Execution task {ids[index]} has no dependency field")
            minutes = task.get("estimatedMinutes")
            if not isinstance(minutes, int) or minutes <= 0:
                raise RunnerError(f"Execution task {ids[index]} has invalid estimated minutes")
            total_minutes += minutes
            for dependency in task.get("dependencies") or []:
                dependency_id = str(dependency)
                if dependency_id not in positions or positions[dependency_id] >= index:
                    raise RunnerError(f"Execution task {ids[index]} has an invalid dependency")

        coverage = str(execution.get("resourceCoverage") or "")
        if coverage not in {"strong", "partial"}:
            raise RunnerError("Execution resource coverage is weak or missing")
        if execution.get("status") != "draft":
            raise RunnerError("Execution Blueprint must remain an unapproved draft")

        domain_fields = []
        supporting_fields = [
            execution.get("narrative"),
            execution.get("checkpoints"),
            execution.get("assumptions"),
            execution.get("budgetSummary"),
        ]
        for task in tasks:
            task_domain_fields = [
                task.get("title"),
                task.get("actionSteps"),
                task.get("deliverable"),
                task.get("completionEvidence"),
            ]
            domain_fields.extend(task_domain_fields)
            supporting_fields.extend(
                [
                    *task_domain_fields,
                    task.get("scheduleWindow"),
                    task.get("risks"),
                    task.get("fallbackAction"),
                ]
            )
        normalized = _normalize(domain_fields)
        missing_groups = [
            group for group in scenario.keyword_groups
            if not any(_normalize(term).strip('"') in normalized for term in group)
        ]
        if missing_groups:
            labels = ["/".join(group) for group in missing_groups]
            raise RunnerError(f"Execution misses domain requirements: {', '.join(labels)}")
        normalized_supporting = _normalize(supporting_fields)
        missing_supporting_groups = [
            group for group in scenario.supporting_keyword_groups
            if not any(
                _normalize(term).strip('"') in normalized_supporting
                for term in group
            )
        ]
        if missing_supporting_groups:
            labels = ["/".join(group) for group in missing_supporting_groups]
            raise RunnerError(f"Execution misses supporting domain requirements: {', '.join(labels)}")
        contaminated = [
            group for group in scenario.forbidden_keyword_groups
            if any(
                _contains_non_negated_term(normalized, _normalize(term).strip('"'))
                for term in group
            )
        ]
        if contaminated:
            labels = ["/".join(group) for group in contaminated]
            raise RunnerError(f"Execution contains cross-domain contamination: {', '.join(labels)}")

        if scenario.time_capacity_minutes is not None:
            minimum = (scenario.time_capacity_minutes * 3 + 3) // 4
            if not minimum <= total_minutes <= scenario.time_capacity_minutes:
                raise RunnerError("Execution total minutes fall outside 75%-100% of fixed capacity")

        if scenario.spending_limit_cny is not None:
            budget_summary = execution.get("budgetSummary")
            if not isinstance(budget_summary, dict):
                raise RunnerError("Execution is missing the required budgetSummary")
            spending_limit = budget_summary.get("spendingLimitCny")
            if (
                not isinstance(spending_limit, int)
                or isinstance(spending_limit, bool)
                or spending_limit != scenario.spending_limit_cny
            ):
                raise RunnerError("Execution budgetSummary does not preserve the fixed spending limit")
            allocations = budget_summary.get("allocations")
            if not isinstance(allocations, list) or not allocations:
                raise RunnerError("Execution budgetSummary has no allocations")
            categories: set[str] = set()
            allocated_total = 0
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    raise RunnerError("Execution budgetSummary contains a non-object allocation")
                category = unicodedata.normalize(
                    "NFKC", str(allocation.get("category") or "")
                ).strip().casefold()
                amount = allocation.get("amountCny")
                if not category:
                    raise RunnerError("Execution budgetSummary has an empty allocation category")
                if category in categories:
                    raise RunnerError("Execution budgetSummary has duplicate allocation categories")
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    raise RunnerError("Execution budgetSummary has an invalid allocation amount")
                categories.add(category)
                allocated_total += amount
            if allocated_total > scenario.spending_limit_cny:
                raise RunnerError("Execution budget allocations exceed the fixed spending limit")
            result.budget_allocated_cny = allocated_total

        result.task_count = len(tasks)
        result.total_estimated_minutes = total_minutes
        result.resource_coverage = coverage

    @staticmethod
    def _audit_critique(result: ScenarioResult, critique: dict[str, Any]) -> None:
        if critique.get("status") != "passed":
            raise RunnerError("Final independent Critic status is not passed")
        issues = critique.get("issues") or []
        if any(
            isinstance(issue, dict) and issue.get("severity") in {"blocker", "major"}
            for issue in issues
        ):
            raise RunnerError("Final Critic still contains a blocker or major issue")
        if critique.get("repairRequests"):
            raise RunnerError("Final Critic still contains repair requests")
        if critique.get("calendarWritable") is not True:
            raise RunnerError("Final Critic did not mark the Blueprint Calendar-writable")
        score = critique.get("score")
        if not isinstance(score, int):
            raise RunnerError("Final Critic score is missing")
        if score < MIN_CRITIC_SCORE:
            raise RunnerError(
                f"Final Critic score is below the high-quality threshold of {MIN_CRITIC_SCORE}"
            )
        result.critic_score = score

    @staticmethod
    def _audit_artifact_lineage(
        messages: Sequence[dict[str, Any]],
        session_cards: Sequence[dict[str, Any]],
        result: ScenarioResult,
    ) -> None:
        decisions = [
            _payload_data(message)
            for message in session_cards
            if message.get("kind") == "agent_decision"
        ]

        strategy_requests = [
            decision
            for decision in decisions
            if str(decision.get("agent") or "") == "Strategy Agent"
            and str(decision.get("decision") or "") == "request_user_input"
        ]
        strategy_approvals = [
            decision
            for decision in decisions
            if str(decision.get("agent") or "") == "Strategy Agent"
            and str(decision.get("decision") or "") == "approve"
        ]
        request_ids: list[str] = []
        for decision in strategy_requests:
            output_ids = decision.get("outputArtifactIds")
            if not isinstance(output_ids, list) or len(output_ids) != 1 or not str(output_ids[0] or ""):
                raise RunnerError("Strategy request_user_input does not produce one artifact version")
            request_ids.append(str(output_ids[0]))
        approval_ids: list[str] = []
        for decision in strategy_approvals:
            input_ids = decision.get("inputArtifactIds")
            if not isinstance(input_ids, list) or len(input_ids) != 1 or not str(input_ids[0] or ""):
                raise RunnerError("Strategy approval does not bind one artifact version")
            approval_ids.append(str(input_ids[0]))
        if (
            not request_ids
            or len(request_ids) != len(set(request_ids))
            or len(approval_ids) != len(set(approval_ids))
            or set(request_ids) != set(approval_ids)
        ):
            raise RunnerError("Strategy artifact versions are not uniquely paired with approvals")

        user_approval_count = sum(
            1
            for message in messages
            if message.get("role") == "user"
            and str(message.get("content") or "").strip() == STRATEGY_APPROVAL_MESSAGE
        )
        if user_approval_count != len(approval_ids):
            raise RunnerError("Durable Strategy approvals do not match ordinary user control messages")
        if result.strategy_approval_count and result.strategy_approval_count != len(approval_ids):
            raise RunnerError("Stream and replay disagree on the number of Strategy approvals")
        result.strategy_approval_count = len(approval_ids)

        execution_decisions = [
            decision
            for decision in decisions
            if str(decision.get("agent") or "") == "Execution Agent"
            and str(decision.get("decision") or "") == "produce_artifact"
        ]
        execution_outputs: dict[str, dict[str, Any]] = {}
        for decision in execution_decisions:
            output_ids = decision.get("outputArtifactIds")
            if not isinstance(output_ids, list) or len(output_ids) != 1 or not str(output_ids[0] or ""):
                raise RunnerError("Execution Agent did not produce one auditable artifact version")
            output_id = str(output_ids[0])
            if output_id in execution_outputs:
                raise RunnerError("Execution artifact version was produced more than once")
            execution_outputs[output_id] = decision
            strategy_inputs = set(str(item) for item in decision.get("inputArtifactIds") or []) & set(request_ids)
            if len(strategy_inputs) != 1:
                raise RunnerError("Execution artifact is not bound to one approved Strategy version")
        if not execution_outputs:
            raise RunnerError("No Execution artifact lineage is available")
        if any(
            not any(
                strategy_id in {str(item) for item in decision.get("inputArtifactIds") or []}
                for decision in execution_decisions
            )
            for strategy_id in request_ids
        ):
            raise RunnerError("An approved Strategy version never reached Execution")

        critic_decisions = [
            decision
            for decision in decisions
            if str(decision.get("agent") or "") == "Critic Agent"
            and str(decision.get("decision") or "") in {"approve", "block", "request_agent_revision"}
            and _has_successful_model_usage(decision)
        ]
        execution_order = list(execution_outputs)
        critique_output_ids: set[str] = set()
        execution_review_counts = {execution_id: 0 for execution_id in execution_order}
        critic_execution_ids: dict[int, str] = {}
        for decision in critic_decisions:
            output_ids = decision.get("outputArtifactIds")
            if (
                not isinstance(output_ids, list)
                or len(output_ids) != 1
                or not str(output_ids[0] or "")
            ):
                raise RunnerError("Each Critic decision must produce one auditable Critique artifact")
            critique_id = str(output_ids[0])
            if critique_id in critique_output_ids:
                raise RunnerError("Critic decisions reused the same Critique artifact output")
            critique_output_ids.add(critique_id)

            input_ids = {str(item) for item in decision.get("inputArtifactIds") or []}
            critiqued_execution_ids = [
                execution_id for execution_id in execution_order if execution_id in input_ids
            ]
            if len(critiqued_execution_ids) != 1:
                raise RunnerError("Each Critic decision must reference exactly one Execution artifact version")
            execution_id = critiqued_execution_ids[0]
            execution_review_counts[execution_id] += 1
            critic_execution_ids[id(decision)] = execution_id

        if any(count != 1 for count in execution_review_counts.values()):
            raise RunnerError(
                "Each Execution artifact version must have exactly one independent Critic decision"
            )

        final_approvals = [
            decision for decision in critic_decisions
            if str(decision.get("decision") or "") == "approve"
        ]
        if len(final_approvals) != 1:
            raise RunnerError("Final Critic approval is missing or ambiguous")
        final_execution_id = critic_execution_ids[id(final_approvals[0])]
        if final_execution_id != execution_order[-1]:
            raise RunnerError("Critic approved a stale Execution artifact version")

        repair_count = 0
        for decision in critic_decisions:
            if str(decision.get("decision") or "") not in {"block", "request_agent_revision"}:
                continue
            critiqued_ids = set(execution_outputs) & {
                str(item) for item in decision.get("inputArtifactIds") or []
            }
            if critiqued_ids:
                repair_count += 1

        status_message = next(
            message for message in reversed(session_cards)
            if message.get("kind") == "planning_session_status"
        )
        status_payload = status_message.get("payload") if isinstance(status_message.get("payload"), dict) else {}
        public_counts: list[int] = []
        for container in (
            status_payload,
            status_payload.get("data") if isinstance(status_payload.get("data"), dict) else {},
            status_payload.get("cognitiveMetadata")
            if isinstance(status_payload.get("cognitiveMetadata"), dict)
            else {},
        ):
            value = container.get("repairCount")
            if isinstance(value, int) and value >= 0:
                public_counts.append(value)
        if public_counts and any(value != repair_count for value in public_counts):
            raise RunnerError("Public repair count disagrees with the artifact decision graph")
        result.repair_count = max([repair_count, *public_counts])
        if result.repair_count > 2:
            raise RunnerError("Critic repair loop exceeded two rounds")

        forbidden_user_controls = {
            "确认执行计划",
            "approve execution",
            "写入日历",
            "确认写入日历",
        }
        if any(
            message.get("role") == "user"
            and str(message.get("content") or "").strip().casefold()
            in {item.casefold() for item in forbidden_user_controls}
            for message in messages
        ):
            raise RunnerError("Runner crossed the Execution or Calendar approval boundary")


def run_batch(
    scenarios: Iterable[Scenario],
    runner: PlanningScenarioRunner,
) -> list[ScenarioResult]:
    results = [runner.run(scenario) for scenario in scenarios]
    _reject_reused_batch_ids(results)
    return results


def audit_manifest_batch(
    scenarios: Iterable[Scenario],
    runner: PlanningScenarioRunner,
    manifest: dict[str, str],
) -> list[ScenarioResult]:
    """Replay-audit browser-created Threads without sending chat messages."""

    results = [
        runner.audit_thread(scenario, manifest.get(scenario.key, ""))
        for scenario in scenarios
    ]
    _reject_reused_batch_ids(results)
    return results


def current_source_fingerprint() -> str:
    """Hash runtime source only, without reading settings, data, or credentials."""

    repository_root = Path(__file__).resolve().parents[1]
    source_files: list[Path] = []
    for relative_root in SOURCE_FINGERPRINT_ROOTS:
        root = repository_root / relative_root
        if root.is_file():
            source_files.append(root)
        elif root.is_dir():
            source_files.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
    digest = hashlib.sha256()
    for path in sorted(source_files, key=lambda item: item.relative_to(repository_root).as_posix()):
        relative = path.relative_to(repository_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _reject_reused_batch_ids(results: Sequence[ScenarioResult]) -> None:
    thread_ids = [item.thread_id for item in results if item.thread_id]
    session_ids = [item.session_id for item in results if item.session_id]
    if len(thread_ids) != len(set(thread_ids)) or len(session_ids) != len(set(session_ids)):
        for result in results:
            result.passed = False
            result.failures.append("The live batch reused a Thread or Planning Session")


def _load_manifest_document(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError("Thread manifest is missing or is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RunnerError("Thread manifest must be a JSON object")
    return parsed


def load_thread_manifest(path: Path) -> dict[str, str]:
    """Load either a direct scenario mapping or ``{"threads": {...}}``."""

    parsed = _load_manifest_document(path)
    raw_mapping = parsed.get("threads") if "threads" in parsed else parsed
    if not isinstance(raw_mapping, dict):
        raise RunnerError("Thread manifest threads field must be an object")
    known_keys = {scenario.key for scenario in SCENARIOS}
    unknown = sorted(str(key) for key in raw_mapping if str(key) not in known_keys)
    if unknown:
        raise RunnerError(f"Thread manifest contains unknown scenarios: {', '.join(unknown)}")
    manifest: dict[str, str] = {}
    for raw_key, raw_thread_id in raw_mapping.items():
        key = str(raw_key)
        if not isinstance(raw_thread_id, str) or not raw_thread_id.strip():
            raise RunnerError(f"Thread manifest scenario {key} has an invalid threadId")
        manifest[key] = raw_thread_id.strip()
    if len(manifest.values()) != len(set(manifest.values())):
        raise RunnerError("Thread manifest reuses a Thread across scenarios")
    return manifest


def load_manifest_source_fingerprint(path: Path) -> str:
    parsed = _load_manifest_document(path)
    fingerprint = str(parsed.get("sourceFingerprint") or "").strip().casefold()
    return fingerprint if SOURCE_FINGERPRINT_RE.fullmatch(fingerprint) else ""


def _canary_order_passed(results: Sequence[ScenarioResult]) -> bool:
    travel = next((item for item in results if item.key == "travel"), None)
    if travel is None:
        return False
    travel_completed = _parse_timestamp(travel.completed_at)
    other_starts = [
        _parse_timestamp(item.started_at)
        for item in results
        if item.key != "travel"
    ]
    return bool(
        travel_completed is not None
        and other_starts
        and all(start is not None and start >= travel_completed for start in other_starts)
    )


def build_report(
    results: Sequence[ScenarioResult],
    *,
    base_url: str,
    smoke_only: bool = False,
    required_provider: str | None = None,
    read_only_audit: bool = False,
    source_fingerprint: str = "",
    frozen_source_verified: bool = False,
) -> dict[str, Any]:
    passed = sum(1 for result in results if result.passed)
    normalized_required_provider = (required_provider or "").strip().casefold()
    report_required_provider = (
        normalized_required_provider
        if normalized_required_provider in KNOWN_REQUIRED_PROVIDERS
        else None
    )
    result_keys = [result.key for result in results]
    result_directions = [result.direction for result in results]
    expected_keys = {scenario.key for scenario in SCENARIOS}
    canary_order_passed = _canary_order_passed(results)
    safe_source_fingerprint = (
        source_fingerprint.strip().casefold()
        if SOURCE_FINGERPRINT_RE.fullmatch(source_fingerprint.strip().casefold())
        else ""
    )
    full_acceptance_passed = (
        not smoke_only
        and read_only_audit
        and frozen_source_verified
        and bool(safe_source_fingerprint)
        and canary_order_passed
        and len(results) == len(SCENARIOS)
        and len(result_keys) == len(set(result_keys))
        and len(result_directions) == len(set(result_directions))
        and set(result_keys) == expected_keys
        and passed == len(results)
        and report_required_provider == "deepseek"
    )
    performance = _batch_performance(results)
    performance_target_ms = PERFORMANCE_TARGET_MINUTES * 60 * 1000
    performance_target_passed = bool(
        full_acceptance_passed
        and performance["batchWallTimeMs"] > 0
        and performance["batchWallTimeMs"] <= performance_target_ms
        and performance["maxObservedConcurrent"] > 0
        and performance["maxObservedConcurrent"] <= PLANNING_CONCURRENCY_LIMIT
    )
    serialized_results = []
    for result in results:
        serialized = asdict(result)
        serialized.pop("request_intervals", None)
        serialized["budgetAllocatedCny"] = serialized.pop("budget_allocated_cny")
        serialized_results.append(serialized)
    return {
        "schemaVersion": 3,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "baseUrl": _sanitize_base_url(base_url),
        "sourceFingerprint": safe_source_fingerprint,
        "advanceInterface": "browser:/command" if read_only_audit else "/api/command/chat",
        "readOnlyAudit": read_only_audit,
        "auditInterface": "/api/command/thread/{threadId}",
        "requiredProvider": report_required_provider,
        "excludedOldThreads": [
            {"threadIdPrefix": item, "reason": "stopped_before_execution_approval"}
            for item in EXCLUDED_OLD_THREAD_IDS
        ],
        "summary": {
            "passed": passed,
            "total": len(results),
            "allPassed": bool(results) and passed == len(results),
            "smokeOnly": smoke_only,
            "fullAcceptancePassed": full_acceptance_passed,
            "canaryOrderPassed": canary_order_passed,
            "frozenSourceVerified": bool(frozen_source_verified and safe_source_fingerprint),
            "performanceTargetMinutes": PERFORMANCE_TARGET_MINUTES,
            "minimumCriticScore": MIN_CRITIC_SCORE,
            "performanceTargetPassed": performance_target_passed,
            **performance,
        },
        "results": serialized_results,
    }


def _stdout_observer(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-manifest",
        type=Path,
        help=(
            "Read-only mode: GET and audit browser-created Threads from a JSON "
            "scenario-to-threadId mapping; never POST Command chat."
        ),
    )
    parser.add_argument(
        "--print-source-fingerprint",
        action="store_true",
        help="Print the credential-free runtime-source fingerprint used to freeze a browser batch.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--required-provider",
        choices=KNOWN_REQUIRED_PROVIDERS,
        help="Require every public modelUsage proof and successful route to use this provider.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=[scenario.key for scenario in SCENARIOS],
        help="Run only one named scenario. Repeat to select several.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the sanitized JSON report here (defaults to the OS temp directory).",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--max-model-retries", type=int, default=2)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.print_source_fingerprint:
        print(current_source_fingerprint())
        return 0
    if args.audit_manifest is None:
        print("--audit-manifest is required unless --print-source-fingerprint is used.", file=sys.stderr)
        return 2
    selected = [
        scenario for scenario in SCENARIOS
        if not args.only or scenario.key in set(args.only)
    ]
    if not selected:
        print("No scenarios selected.", file=sys.stderr)
        return 2

    report_path = args.report or Path("data") / "e2e-reports" / (
        f"planix-zero-start-e2e-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    transport = HttpAuditTransport(args.base_url)
    try:
        transport.check_health()
        runner = PlanningScenarioRunner(
            transport,
            observer=_stdout_observer,
            max_turns=args.max_turns,
            max_model_retries_per_stage=args.max_model_retries,
            required_provider=args.required_provider,
        )
        manifest = load_thread_manifest(args.audit_manifest)
        declared_source_fingerprint = load_manifest_source_fingerprint(args.audit_manifest)
        frozen_source_verified = bool(
            declared_source_fingerprint
            and declared_source_fingerprint == current_source_fingerprint()
        )
        missing = [scenario.key for scenario in selected if scenario.key not in manifest]
        if missing:
            raise RunnerError(
                f"Thread manifest is missing selected scenarios: {', '.join(missing)}"
            )
        results = audit_manifest_batch(selected, runner, manifest)
    except (httpx.HTTPError, RunnerError) as exc:
        if isinstance(exc, RunnerError):
            print(str(exc), file=sys.stderr)
        else:
            print("Planix backend is unavailable or returned an HTTP error.", file=sys.stderr)
        return 2
    finally:
        transport.close()

    report = build_report(
        results,
        base_url=args.base_url,
        smoke_only=bool(args.only),
        required_provider=args.required_provider,
        read_only_audit=True,
        source_fingerprint=declared_source_fingerprint,
        frozen_source_verified=frozen_source_verified,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "event": "batch_finished",
                "passed": report["summary"]["passed"],
                "total": report["summary"]["total"],
                "allPassed": report["summary"]["allPassed"],
                "smokeOnly": report["summary"]["smokeOnly"],
                "fullAcceptancePassed": report["summary"]["fullAcceptancePassed"],
                "performanceTargetPassed": report["summary"]["performanceTargetPassed"],
                "batchWallTimeMs": report["summary"]["batchWallTimeMs"],
                "report": str(report_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    acceptance_key = "allPassed" if report["summary"]["smokeOnly"] else "fullAcceptancePassed"
    return 0 if report["summary"][acceptance_key] else 1


if __name__ == "__main__":
    raise SystemExit(main())
