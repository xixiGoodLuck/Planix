from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..db import get_conn
from ..schemas import (
    CreatePlanningSessionRequest,
    ExecutionPlanDraft,
    ExecutionPlanQualityChecks,
    ExecutionPlanQualityReport,
    ExecutionTask,
    ExecutionTaskResourceCoverage,
    LearningImmediatePatch,
    LearningMemoryUpdate,
    LearningPatch,
    LearningReflection,
    LongTermLearning,
    MemoryCreate,
    MemoryHit,
    MemoryInsightBrief,
    MemoryInsightHits,
    PlanDesignPhase,
    PlanDesignProposal,
    PendingPlanningQuestion,
    PlanningLearningSlots,
    PlanningInsights,
    PlanningSessionResponse,
    PlanningSessionStatus,
    PlanningSessionTextRequest,
    PlanningSlotState,
    PlanningTravelSlots,
    ResourceBrief,
    ResourceCandidate,
    ResourceCoverage,
    ResourceFitScore,
    TaskLearningResource,
    TaskResourceBundle,
    UserNeedContract,
)
from .memory_store import MemoryService


LEGACY_PLANNING_ENGINE_VERSION = "legacy-template-v1"
from .plans import list_plans
from .planning_agent_runtime import PlanningAgentRuntime


ACTIVE_SESSION_STATUSES = {
    "needs_goal_clarification",
    "waiting_design_approval",
    "design_revision",
    "waiting_execution_approval",
    "execution_revision",
    "ready_to_write_calendar",
    "waiting_calendar_write_approval",
    "learning_from_feedback",
}

RESOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "match": ["python"],
        "id": "catalog:python:control-flow",
        "title": "Python Tutorial - Control Flow",
        "sourceType": "official_doc",
        "domain": "Python",
        "topics": ["control flow", "functions", "loops"],
        "url": "https://docs.python.org/3/tutorial/controlflow.html",
        "section": "Control Flow Tools",
        "howToUse": "Read only the if/for/function examples, then write one tiny script.",
        "expectedOutput": "A runnable Python script using if, for, and one function.",
    },
    {
        "match": ["python"],
        "id": "practice:python:basics",
        "title": "Python if/for/function practice",
        "sourceType": "practice_bank",
        "domain": "Python",
        "topics": ["if/else", "for loop", "function"],
        "searchKeyword": "Python beginner if for function exercises",
        "howToUse": "Complete three small input/output exercises before reading more theory.",
        "expectedOutput": "Three small runnable scripts.",
    },
    {
        "match": ["fastapi"],
        "id": "catalog:fastapi:first-steps",
        "title": "FastAPI Tutorial - First Steps",
        "sourceType": "official_doc",
        "domain": "FastAPI",
        "topics": ["API", "routing", "GET endpoint"],
        "url": "https://fastapi.tiangolo.com/tutorial/first-steps/",
        "section": "First Steps",
        "howToUse": "Recreate only the first GET example and test it locally.",
        "expectedOutput": "A running FastAPI GET endpoint.",
    },
    {
        "match": ["react"],
        "id": "catalog:react:state",
        "title": "React Docs - State",
        "sourceType": "official_doc",
        "domain": "React",
        "topics": ["state", "component", "useState"],
        "url": "https://react.dev/learn/state-a-components-memory",
        "section": "State: A Component's Memory",
        "howToUse": "Read one useState example and turn it into a small toggle component.",
        "expectedOutput": "A component with visible state changes.",
    },
    {
        "match": ["typescript", "ts"],
        "id": "catalog:typescript:handbook",
        "title": "TypeScript Handbook - Everyday Types",
        "sourceType": "official_doc",
        "domain": "TypeScript",
        "topics": ["types", "interfaces", "functions"],
        "url": "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html",
        "section": "Everyday Types",
        "howToUse": "Convert one plain JavaScript function into typed TypeScript.",
        "expectedOutput": "A typed function and interface example.",
    },
    {
        "match": ["llm", "api", "deepseek", "kimi", "openai"],
        "id": "template:llm:structured-output",
        "title": "Structured LLM output demo",
        "sourceType": "project_template",
        "domain": "LLM API",
        "topics": ["chat completions", "JSON", "validation"],
        "searchKeyword": "OpenAI compatible chat completions JSON output example",
        "howToUse": "Build one request, parse JSON, and validate the required fields.",
        "expectedOutput": "A minimal structured-output script.",
    },
    {
        "match": ["rag", "资料", "检索"],
        "id": "template:rag:local-search",
        "title": "Local RAG mini demo",
        "sourceType": "project_template",
        "domain": "RAG",
        "topics": ["local search", "retrieval", "answer synthesis"],
        "searchKeyword": "local RAG SQLite text search mini project",
        "howToUse": "Index three notes, search by keyword, and synthesize one answer.",
        "expectedOutput": "A local retrieval demo with three documents.",
    },
    {
        "match": ["agent", "ai agent", "智能体"],
        "id": "template:agent:trace",
        "title": "Agent trace and tool preview demo",
        "sourceType": "project_template",
        "domain": "AI Agent",
        "topics": ["trace", "tool preview", "approval"],
        "searchKeyword": "AI agent tool trace approval pattern",
        "howToUse": "Model a two-step agent trace and add one approval gate.",
        "expectedOutput": "A trace card plus approval preview.",
    },
    {
        "match": ["sqlite", "database", "数据库"],
        "id": "catalog:sqlite:basic-crud",
        "title": "SQLite CRUD practice",
        "sourceType": "practice_bank",
        "domain": "SQLite",
        "topics": ["table", "insert", "query", "update"],
        "searchKeyword": "SQLite CRUD beginner exercise",
        "howToUse": "Create one table and implement insert/query/update with sample data.",
        "expectedOutput": "A working CRUD script.",
    },
    {
        "match": ["tauri"],
        "id": "catalog:tauri:command",
        "title": "Tauri command bridge practice",
        "sourceType": "practice_bank",
        "domain": "Tauri",
        "topics": ["command", "frontend bridge"],
        "searchKeyword": "Tauri command frontend invoke example",
        "howToUse": "Create one backend command and invoke it from the UI.",
        "expectedOutput": "A visible frontend call to a Tauri command.",
    },
    {
        "match": ["简历", "resume", "interview", "面试", "实习", "portfolio", "作品集"],
        "id": "practice:career:star",
        "title": "STAR resume bullet rewrite practice",
        "sourceType": "practice_bank",
        "domain": "career",
        "topics": ["resume", "interview", "project packaging"],
        "searchKeyword": "STAR resume bullet project rewrite examples",
        "howToUse": "Rewrite one project action into three result-oriented bullets.",
        "expectedOutput": "Three resume bullets and one interview answer outline.",
    },
]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _context_date(context: dict[str, Any] | None = None) -> str:
    value = context.get("date") if isinstance(context, dict) else ""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return datetime.now().date().isoformat()


def _contains(text: str, *words: str) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _title_from_goal(text: str) -> str:
    cleaned = re.sub(r"^(帮我|请|我要|我想|给我|制定|规划|做一个|做一份)+", "", text.strip(), flags=re.I).strip(" ：:，,。.")
    return cleaned[:80] or "新的深度规划"


def _duration_days(text: str) -> int:
    match = re.search(r"(\d{1,3})\s*(天|day|days)", text, flags=re.I)
    if match:
        return max(1, min(365, int(match.group(1))))
    match = re.search(r"(\d{1,2})\s*(周|week|weeks)", text, flags=re.I)
    if match:
        return max(7, min(365, int(match.group(1)) * 7))
    match = re.search(r"(\d{1,2})\s*(月|month|months)", text, flags=re.I)
    if match:
        return max(14, min(365, int(match.group(1)) * 30))
    return 30


def _available_time(text: str) -> str | None:
    match = re.search(r"(每天|每日|day|daily).{0,8}?(\d+(?:\.\d+)?)\s*(小时|h|hour|hours|分钟|minute|minutes|min)", text, flags=re.I)
    if not match:
        return None
    amount = match.group(2)
    unit = match.group(3)
    return f"每天 {amount} {unit}"


def _goal_is_clear(text: str) -> bool:
    has_domain = bool(re.search(r"[A-Za-z]{2,}|Python|FastAPI|React|AI|实习|项目|考试|面试|简历", text, re.I))
    has_outcome = _contains(text, "实习", "项目", "考试", "面试", "作品集", "求职", "portfolio", "interview", "exam", "project")
    has_horizon = bool(re.search(r"\d+\s*(天|周|月|day|week|month)", text, re.I))
    has_time = _available_time(text) is not None
    return has_domain and (has_outcome or has_horizon or has_time)


def _user_need_contract(user_input: str) -> UserNeedContract:
    hard_constraints: list[str] = []
    if match := re.search(r"(每天|每日).{0,12}?(\d+(?:\.\d+)?\s*(小时|分钟))", user_input):
        hard_constraints.append(match.group(0))
    if match := re.search(r"\d{1,3}\s*(天|周|月)", user_input):
        hard_constraints.append(match.group(0))
    if _contains(user_input, "不要纯理论", "项目驱动", "必须", "只能"):
        hard_constraints.extend([part.strip() for part in re.split(r"[，。,;；]", user_input) if any(word in part for word in ("不要", "项目", "必须", "只能"))])

    soft_preferences: list[str] = []
    if _contains(user_input, "项目", "作品集", "实战"):
        soft_preferences.append("偏好项目驱动和可展示产出。")
    if _contains(user_input, "轻松", "不要太累", "慢慢"):
        soft_preferences.append("偏好低压力、可持续节奏。")

    missing: list[str] = []
    questions: list[str] = []
    if not re.search(r"(零基础|基础|学过|入门|进阶|advanced|beginner)", user_input, re.I):
        missing.append("currentLevel")
        questions.append("你现在是零基础、学过一点，还是已经能做小项目？")
    if _available_time(user_input) is None:
        missing.append("availableTime")
        questions.append("你每天或每周大概能投入多少时间？")
    if not _contains(user_input, "实习", "项目", "考试", "面试", "作品集", "求职", "portfolio", "interview", "exam", "project"):
        missing.append("desiredOutcome")
        questions.append("你学这个主要是为了做项目、找实习、考试，还是提升工作能力？")

    can_move = _goal_is_clear(user_input)
    return UserNeedContract(
        rawUserInput=user_input,
        interpretedGoal=_title_from_goal(user_input),
        desiredOutcome="AI 实习/作品集准备" if _contains(user_input, "实习", "作品集", "portfolio") else None,
        currentLevel="用户未明确，按初学到进阶过渡处理" if "currentLevel" in missing else "用户已提供基础水平线索",
        deadline=(re.search(r"\d{1,3}\s*(天|周|月)", user_input).group(0) if re.search(r"\d{1,3}\s*(天|周|月)", user_input) else None),
        availableTime=_available_time(user_input),
        hardConstraints=hard_constraints,
        softPreferences=soft_preferences,
        missingInformation=[] if can_move else missing,
        userWordsThatMustBeRespected=hard_constraints,
        canMoveToDesign=can_move,
        clarificationQuestions=[] if can_move else questions[:3],
    )


def _memory_hit(item, relevance: str) -> MemoryHit:
    return MemoryHit(id=item.id, kind=item.kind, title=item.title, summary=item.summary or item.content[:180], relevance=relevance)


def _title_from_goal(text: str) -> str:
    cleaned = re.sub(
        r"^(帮我|请帮我|我要|我想|给我|制定|规划|做一个|做一份|plan|create|make)\s*",
        "",
        text.strip(),
        flags=re.I,
    ).strip(" ，,。.")
    return cleaned[:80] or "新的深度规划"


def _resource_topic_from_goal(goal: str) -> str:
    cleaned = re.sub(r"补充信息[:：].*$", "", goal, flags=re.S).strip()
    first_line = next((line.strip() for line in cleaned.splitlines() if line.strip()), "")
    topic = _title_from_goal(first_line or cleaned)
    topic = re.sub(r"\s+", " ", topic).strip(" ，,。.：:")
    if len(topic) > 24:
        topic = topic[:24].rstrip()
    return topic or "当前主题"


def _duration_days(text: str) -> int:
    match = re.search(r"(\d{1,3})\s*(天|日|day|days|d)\b?", text, flags=re.I)
    if match:
        return max(1, min(365, int(match.group(1))))
    match = re.search(r"(\d{1,2})\s*(周|week|weeks|w)\b?", text, flags=re.I)
    if match:
        return max(7, min(365, int(match.group(1)) * 7))
    match = re.search(r"(\d{1,2})\s*(月|month|months|m)\b?", text, flags=re.I)
    if match:
        return max(14, min(365, int(match.group(1)) * 30))
    return 30


def _available_time(text: str) -> str | None:
    match = re.search(
        r"(每天|每日|每晚|daily|per day|each day).{0,10}?(\d+(?:\.\d+)?)\s*(小时|h|hour|hours|分钟|minute|minutes|min)",
        text,
        flags=re.I,
    )
    if not match:
        return None
    return f"每天 {match.group(2)} {match.group(3)}"


def _has_horizon(text: str) -> bool:
    return bool(re.search(r"\d+\s*(天|日|周|月|day|days|week|weeks|month|months)", text, re.I))


def _has_outcome(text: str) -> bool:
    return _contains(
        text,
        "实习",
        "项目",
        "考试",
        "面试",
        "作品集",
        "简历",
        "求职",
        "portfolio",
        "interview",
        "exam",
        "project",
        "internship",
        "job",
    )


def _goal_is_clear(text: str) -> bool:
    has_domain = bool(re.search(r"[A-Za-z]{2,}|Python|FastAPI|React|AI|实习|项目|考试|面试|简历", text, re.I))
    return has_domain and (_has_outcome(text) or _has_horizon(text) or _available_time(text) is not None)


def _user_need_contract(user_input: str) -> UserNeedContract:
    hard_constraints: list[str] = []
    if match := re.search(r"(每天|每日|每周|daily|per day).{0,14}?(\d+(?:\.\d+)?\s*(小时|分钟|h|hour|minutes|min))", user_input, re.I):
        hard_constraints.append(match.group(0))
    if match := re.search(r"\d{1,3}\s*(天|周|月|day|days|week|weeks|month|months)", user_input, re.I):
        hard_constraints.append(match.group(0))
    if _contains(user_input, "不要纯理论", "项目驱动", "必须", "只能", "no pure theory", "project driven"):
        parts = [part.strip() for part in re.split(r"[，,。.;；]", user_input) if part.strip()]
        hard_constraints.extend(
            part for part in parts if _contains(part, "不要", "项目", "必须", "只能", "no pure theory", "project driven")
        )

    soft_preferences: list[str] = []
    if _contains(user_input, "项目", "作品集", "实战", "project", "portfolio"):
        soft_preferences.append("偏好项目驱动和可展示产出。")
    if _contains(user_input, "轻松", "不要太累", "慢慢", "low pressure"):
        soft_preferences.append("偏好低压力、可持续节奏。")

    missing: list[str] = []
    questions: list[str] = []
    if not re.search(r"(零基础|基础|学过|入门|进阶|advanced|beginner|intermediate)", user_input, re.I):
        missing.append("currentLevel")
        questions.append("你现在是零基础、学过一点，还是已经能做小项目？")
    if _available_time(user_input) is None:
        missing.append("availableTime")
        questions.append("你每天或每周大概能投入多少时间？")
    if not _has_outcome(user_input):
        missing.append("desiredOutcome")
        questions.append("你学习这个主要是为了做项目、找实习、考试，还是提升工作能力？")

    can_move = _goal_is_clear(user_input)
    horizon = re.search(r"\d{1,3}\s*(天|周|月|day|days|week|weeks|month|months)", user_input, re.I)
    return UserNeedContract(
        rawUserInput=user_input,
        interpretedGoal=_title_from_goal(user_input),
        desiredOutcome="AI 实习/作品集准备" if _contains(user_input, "实习", "作品集", "portfolio", "internship") else None,
        currentLevel="用户未明确，按初学到进阶过渡处理" if "currentLevel" in missing else "用户已提供基础水平线索",
        deadline=horizon.group(0) if horizon else None,
        availableTime=_available_time(user_input),
        hardConstraints=list(dict.fromkeys(hard_constraints)),
        softPreferences=soft_preferences,
        missingInformation=[] if can_move else missing,
        userWordsThatMustBeRespected=list(dict.fromkeys(hard_constraints)),
        canMoveToDesign=can_move,
        clarificationQuestions=[] if can_move else questions[:3],
    )

def _duration_days(text: str) -> int:
    for pattern, multiplier, minimum in (
        ("(\\d{1,3})\\s*(?:\\u5929|\\u65e5|day|days)", 1, 1),
        ("(\\d{1,2})\\s*(?:\\u5468|week|weeks)", 7, 7),
        ("(\\d{1,2})\\s*(?:\\u6708|month|months)", 30, 14),
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return max(minimum, min(365, int(match.group(1)) * multiplier))
    return 30


def _available_time(text: str) -> str | None:
    value, _scope = _available_time_with_scope(text)
    return value


def _available_time_with_scope(text: str) -> tuple[str | None, str | None]:
    pattern = (
        r"(?:(每天|每日|每晚|daily|per day|each day|每周|每星期|一周|weekly|per week|each week).{0,12}?)?"
        r"(\d+(?:\.\d+)?)\s*(小时|h|hour|hours|分钟|minute|minutes|min)"
    )
    match = re.search(pattern, text, flags=re.I)
    if not match:
        match = re.search(
            r"(?:(\u6bcf\u5929|\u6bcf\u65e5|\u6bcf\u5468|daily|weekly).{0,12}?)?([\u96f6\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u534a]+)\s*(\u5c0f\u65f6|\u5206\u949f|hour|hours|minute|minutes|min)",
            text,
            flags=re.I,
        )
    if not match:
        return None, None
    frequency = (match.group(1) or "").lower()
    amount = match.group(2)
    raw_unit = match.group(3).lower()
    unit = "分钟" if raw_unit in {"分钟", "minute", "minutes", "min"} else "小时"
    if frequency in {"每天", "每日", "每晚", "daily", "per day", "each day"}:
        return f"每天{amount}{unit}", "daily"
    if frequency in {"每周", "每星期", "一周", "weekly", "per week", "each week"}:
        return f"每周{amount}{unit}", "weekly"
    return f"{amount}{unit}", "unknown"


def _has_horizon(text: str) -> bool:
    return bool(re.search("(\\d+)\\s*(?:\\u5929|\\u65e5|\\u5468|\\u6708|day|days|week|weeks|month|months)", text, re.I))


def _has_outcome(text: str) -> bool:
    return _contains(
        text,
        "\u5b9e\u4e60",
        "\u9879\u76ee",
        "\u8003\u8bd5",
        "\u9762\u8bd5",
        "\u4f5c\u54c1\u96c6",
        "\u7b80\u5386",
        "\u6c42\u804c",
        "portfolio",
        "interview",
        "exam",
        "project",
        "internship",
        "job",
    )


def _goal_is_clear(text: str) -> bool:
    has_domain = bool(re.search("[A-Za-z]{2,}|Python|FastAPI|React|AI|\u5b9e\u4e60|\u9879\u76ee|\u8003\u8bd5|\u9762\u8bd5|\u7b80\u5386", text, re.I))
    return has_domain and (_has_outcome(text) or _has_horizon(text) or _available_time(text) is not None)


def _user_need_contract(user_input: str) -> UserNeedContract:
    hard_constraints: list[str] = []
    time_match = re.search(
        "(?:\\u6bcf\\u5929|\\u6bcf\\u65e5|\\u6bcf\\u5468|daily|per day).{0,14}?(\\d+(?:\\.\\d+)?\\s*(?:\\u5c0f\\u65f6|\\u5206\\u949f|h|hour|minutes|min))",
        user_input,
        re.I,
    )
    if time_match:
        hard_constraints.append(time_match.group(0))
    horizon = re.search("(\\d{1,3}\\s*(?:\\u5929|\\u5468|\\u6708|day|days|week|weeks|month|months))", user_input, re.I)
    if horizon:
        hard_constraints.append(horizon.group(0))
    if _contains(user_input, "\u4e0d\u8981\u7eaf\u7406\u8bba", "\u9879\u76ee\u9a71\u52a8", "\u5fc5\u987b", "\u53ea\u80fd", "no pure theory", "project driven"):
        hard_constraints.append(user_input[:120])

    soft_preferences: list[str] = []
    if _contains(user_input, "\u9879\u76ee", "\u4f5c\u54c1\u96c6", "\u5b9e\u6218", "project", "portfolio"):
        soft_preferences.append("Prefer project-driven work with visible deliverables.")
    if _contains(user_input, "\u8f7b\u677e", "\u4e0d\u8981\u592a\u7d2f", "\u6162\u6162", "low pressure"):
        soft_preferences.append("Prefer a sustainable low-pressure pace.")

    missing: list[str] = []
    questions: list[str] = []
    if not re.search("\u96f6\u57fa\u7840|\u57fa\u7840|\u5b66\u8fc7|\u5165\u95e8|\u8fdb\u9636|\u7cbe\u901a|\u638c\u63e1|\u719f\u7ec3|\u9ad8\u7ea7|advanced|beginner|intermediate", user_input, re.I):
        missing.append("currentLevel")
        questions.append("\u4f60\u73b0\u5728\u662f\u96f6\u57fa\u7840\u3001\u5b66\u8fc7\u4e00\u70b9\uff0c\u8fd8\u662f\u5df2\u7ecf\u80fd\u505a\u5c0f\u9879\u76ee\uff1f")
    if _available_time(user_input) is None:
        missing.append("availableTime")
        questions.append("\u4f60\u6bcf\u5929\u6216\u6bcf\u5468\u5927\u6982\u80fd\u6295\u5165\u591a\u5c11\u65f6\u95f4\uff1f")
    if not _has_outcome(user_input):
        missing.append("desiredOutcome")
        questions.append("\u4f60\u5b66\u4e60\u8fd9\u4e2a\u4e3b\u8981\u662f\u4e3a\u4e86\u505a\u9879\u76ee\u3001\u627e\u5b9e\u4e60\u3001\u8003\u8bd5\uff0c\u8fd8\u662f\u63d0\u5347\u5de5\u4f5c\u80fd\u529b\uff1f")

    can_move = _goal_is_clear(user_input)
    return UserNeedContract(
        rawUserInput=user_input,
        interpretedGoal=_title_from_goal(user_input),
        desiredOutcome="AI internship / portfolio preparation" if _contains(user_input, "\u5b9e\u4e60", "\u4f5c\u54c1\u96c6", "portfolio", "internship") else None,
        currentLevel="\u7528\u6237\u672a\u660e\u786e\uff0c\u9700\u8981\u5148\u786e\u8ba4\u5f53\u524d\u6c34\u5e73\u3002" if "currentLevel" in missing else "\u7528\u6237\u5df2\u63d0\u4f9b\u5f53\u524d\u6c34\u5e73\u7ebf\u7d22\u3002",
        deadline=horizon.group(0) if horizon else None,
        availableTime=_available_time(user_input),
        hardConstraints=list(dict.fromkeys(hard_constraints)),
        softPreferences=soft_preferences,
        missingInformation=[] if can_move else missing,
        userWordsThatMustBeRespected=list(dict.fromkeys(hard_constraints)),
        canMoveToDesign=can_move,
        clarificationQuestions=[] if can_move else questions[:3],
    )


_LEARNING_SUBJECTS = {
    "go": "Go",
    "golang": "Go",
    "python": "Python",
    "fastapi": "FastAPI",
    "react": "React",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "rag": "RAG",
    "agent": "AI Agent",
}

_TRAVEL_PLACES = (
    "赛里木湖",
    "喀纳斯",
    "伊犁",
    "乌鲁木齐",
    "禾木",
    "那拉提",
    "可可托海",
    "独库公路",
    "新疆",
)


def _unique_strings(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(item.strip() for item in values if item and item.strip())]


def _detect_slot_domain(text: str, previous: str | None = None) -> str:
    lowered = text.lower()
    if _contains(text, "旅行", "旅游", "出行", "行程", "机票", "飞机", "火车", "自驾", "预算", "赛里木湖", "喀纳斯", "新疆"):
        return "travel"
    if re.search(r"\b(go|golang|python|fastapi|react|typescript|rag)\b", lowered) or _contains(text, "学", "学习", "精通", "掌握", "入门", "练习", "项目"):
        return "learning"
    return previous or "learning"


def _extract_learning_subject(text: str, current: str = "") -> str:
    lowered = text.lower()
    for token, label in _LEARNING_SUBJECTS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered) or token in lowered:
            return label
    return current


def _extract_learning_purpose(text: str, current: str = "", current_text: str = "") -> tuple[str, str]:
    if _contains(text, "实习", "求职", "找工作", "就业", "面试", "internship", "job", "interview"):
        return "internship", "找实习/求职"
    if _contains(text, "项目", "作品集", "实战", "project", "portfolio"):
        return "project", "做项目/作品集"
    if _contains(text, "考试", "考证", "exam"):
        return "exam", "考试/考证"
    if _contains(text, "工作", "后端", "云原生", "提升能力", "提升", "work", "backend"):
        return "skill_improvement", "提升工作能力"
    if _contains(text, "兴趣", "爱好", "好玩", "hobby"):
        return "interest", "兴趣学习"
    return current, current_text


def _extract_learning_level(
    text: str,
    current_level: str = "",
    target_level: str = "",
    current_level_text: str = "",
) -> tuple[str, str, str]:
    if _contains(text, "零基础", "小白", "没学过", "完全不会", "刚开始", "beginner"):
        current_level = "zero_beginner"
        current_level_text = "零基础"
    elif _contains(text, "学过一点", "有点基础", "入门"):
        current_level = "beginner"
        current_level_text = "学过一点/入门"
    elif _contains(text, "有基础", "能做小项目", "有项目经验"):
        current_level = "intermediate"
        current_level_text = "已有基础/能做小项目"
    elif _contains(text, "进阶", "熟练", "高级"):
        current_level = current_level or "intermediate"
        current_level_text = current_level_text or "已有基础"
        target_level = target_level or "进阶/熟练"
    if _contains(text, "精通"):
        current_level = current_level or "intermediate"
        current_level_text = current_level_text or "已有基础，目标是进阶到精通"
        target_level = "精通"
    elif _contains(text, "掌握"):
        target_level = target_level or "掌握"
    return current_level, target_level, current_level_text


def _extract_duration_text(text: str, current: str = "") -> str:
    if _contains(text, "两个星期", "两周"):
        return "14天"
    if match := re.search(r"(\d{1,3})\s*(天|日|周|月|day|days|week|weeks|month|months)", text, flags=re.I):
        return match.group(0)
    return current


def _extract_travel_duration_days(text: str, current: int | None = None) -> int | None:
    if _contains(text, "两个星期", "两周"):
        return 14
    if match := re.search(r"(\d{1,3})\s*(天|日|day|days)", text, flags=re.I):
        return max(1, min(90, int(match.group(1))))
    if match := re.search(r"(\d{1,2})\s*(周|week|weeks)", text, flags=re.I):
        return max(1, min(90, int(match.group(1)) * 7))
    return current


def _extract_travel_month(text: str, current: str = "") -> str:
    zh_months = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if match := re.search(r"(\d{1,2})\s*月", text):
        month = max(1, min(12, int(match.group(1))))
        return f"{month}月"
    for label, number in zh_months.items():
        if f"{label}月" in text:
            return f"{number}月"
    return current


def _extract_travel_budget(text: str, current: str = "") -> str:
    if _contains(text, "一万", "1万"):
        return "1万元"
    if match := re.search(r"(\d+(?:\.\d+)?)\s*(万|元|块|rmb|cny)", text, flags=re.I):
        return match.group(0)
    return current


def _extract_travel_transport(text: str, current: str = "") -> str:
    for word in ("飞机", "火车", "高铁", "自驾", "包车", "公共交通"):
        if word in text:
            return word
    return current


def _extract_travel_places(text: str, current: list[str]) -> list[str]:
    places = list(current)
    for place in _TRAVEL_PLACES:
        if place in text:
            places.append(place)
    return _unique_strings(places)


def _extract_fitness(text: str, current: str = "") -> str:
    if _contains(text, "体能很好", "体力很好", "能徒步", "体能好"):
        return "体能较好"
    if _contains(text, "体能一般", "体力一般", "不想太累"):
        return "体能一般，避免高强度"
    return current


def _filled_and_missing_slots(slot: PlanningSlotState) -> tuple[list[str], list[str]]:
    if slot.domain == "travel":
        filled: list[str] = []
        missing: list[str] = []
        travel = slot.travel
        checks = [
            ("destination", bool(travel.destination or travel.places)),
            ("durationDays", travel.duration_days is not None),
            ("month", bool(travel.month or travel.year)),
            ("transport", bool(travel.transport)),
            ("budget", bool(travel.budget)),
            ("fitnessLevel", bool(travel.fitness_level)),
        ]
        for key, has_value in checks:
            (filled if has_value else missing).append(key)
        return filled, missing
    learning = slot.learning
    checks = [
        ("subject", bool(learning.subject)),
        ("currentLevel", bool(learning.current_level)),
        ("targetLevel", bool(learning.target_level)),
        ("dailyTime", bool(learning.daily_time)),
        ("duration", bool(learning.duration)),
        ("purpose", bool(learning.purpose)),
    ]
    filled = [key for key, has_value in checks if has_value]
    missing = [key for key, has_value in checks if not has_value]
    return filled, missing


def _slot_can_move_to_design(slot: PlanningSlotState) -> bool:
    if slot.domain == "travel":
        travel = slot.travel
        return bool((travel.destination or travel.places) and travel.duration_days and (travel.month or travel.year))
    learning = slot.learning
    return bool(
        learning.subject
        and (learning.target_level or learning.purpose)
        and (learning.duration or learning.daily_time)
        and learning.current_level
    )


def _contract_missing_information(slot: PlanningSlotState) -> list[str]:
    if slot.domain == "travel":
        return slot.missing_slots
    learning = slot.learning
    missing: list[str] = []
    if not learning.subject:
        missing.append("subject")
    if not learning.current_level:
        missing.append("currentLevel")
    if not (learning.daily_time or learning.duration):
        missing.append("availableTime")
    if not learning.purpose:
        missing.append("desiredOutcome")
    return missing


def _slot_questions(slot: PlanningSlotState) -> list[str]:
    if slot.domain == "travel":
        questions: list[str] = []
        travel = slot.travel
        if not (travel.destination or travel.places):
            questions.append("你主要想去新疆哪些城市或景点？比如赛里木湖、喀纳斯、伊犁。")
        if travel.duration_days is None:
            questions.append("这次旅行大概安排几天？")
        if not (travel.month or travel.year):
            questions.append("你计划几月出发？")
        if not travel.transport:
            questions.append("你倾向飞机、火车、自驾还是包车？")
        if not travel.budget:
            questions.append("预算大概是多少？是总预算还是每天预算？")
        return questions[:3]
    learning = slot.learning
    questions = []
    if not learning.current_level:
        questions.append(f"你现在的 {learning.subject or '这个主题'} 水平是零基础、学过一点，还是已经能做小项目？")
    if not (learning.daily_time or learning.duration):
        questions.append("你每天或每周大概能投入多少时间？希望规划多久？")
    if not (learning.target_level or learning.purpose):
        questions.append("你学习的目标是什么：做项目、找实习、提升工作能力，还是达到精通？")
    return questions[:3]


def _build_pending_question(slot: PlanningSlotState) -> PendingPlanningQuestion | None:
    questions = _slot_questions(slot)
    if not questions:
        return None
    return PendingPlanningQuestion(
        askedFields=slot.missing_slots[:3],
        expectedAnswerType=slot.domain or "planning",
        questionText=" ".join(questions),
        questions=questions,
    )


def _slot_state_from_text(text: str, previous: PlanningSlotState | None = None) -> PlanningSlotState:
    slot = previous.model_copy(deep=True) if previous else PlanningSlotState()
    domain = _detect_slot_domain(text, slot.domain)
    slot.domain = domain
    slot.goal = slot.goal or _title_from_goal(text)
    slot.last_updated_from_user_input = text

    if domain == "travel":
        travel = slot.travel or PlanningTravelSlots()
        travel.places = _extract_travel_places(text, travel.places)
        if not travel.destination and ("新疆" in text or "新疆" in travel.places):
            travel.destination = "新疆"
        travel.duration_days = _extract_travel_duration_days(text, travel.duration_days)
        travel.month = _extract_travel_month(text, travel.month)
        travel.transport = _extract_travel_transport(text, travel.transport)
        travel.budget = _extract_travel_budget(text, travel.budget)
        travel.fitness_level = _extract_fitness(text, travel.fitness_level)
        slot.travel = travel
        if travel.destination:
            slot.goal = f"{travel.destination}旅行规划"
    else:
        learning = slot.learning or PlanningLearningSlots()
        learning.subject = _extract_learning_subject(text, learning.subject)
        learning.current_level, learning.target_level, learning.current_level_text = _extract_learning_level(
            text,
            learning.current_level,
            learning.target_level,
            learning.current_level_text,
        )
        available_time, available_time_scope = _available_time_with_scope(text)
        if available_time:
            learning.daily_time = available_time
            learning.available_time_scope = available_time_scope or learning.available_time_scope
        learning.duration = _extract_duration_text(text, learning.duration)
        learning.purpose, learning.purpose_text = _extract_learning_purpose(text, learning.purpose, learning.purpose_text)
        if (
            not learning.current_level
            and learning.subject
            and (learning.target_level or learning.purpose)
            and (learning.daily_time or learning.duration)
        ):
            learning.current_level = "assumed_beginner_to_intermediate"
            learning.current_level_text = "未说明当前水平，先按入门到进阶的保守假设"
        slot.learning = learning
        if learning.subject:
            slot.goal = f"{learning.subject}学习规划"

    if _contains(text, "项目驱动", "不要纯理论", "不想太累", "轻松"):
        slot.preferences = _unique_strings([*slot.preferences, text[:120]])
    if slot.domain == "learning" and slot.learning.daily_time:
        slot.constraints = _unique_strings([*slot.constraints, slot.learning.daily_time])
    filled, missing = _filled_and_missing_slots(slot)
    slot.filled_slots = filled
    slot.missing_slots = missing
    return slot


def _slot_contract(raw_input: str, slot: PlanningSlotState) -> UserNeedContract:
    can_move = _slot_can_move_to_design(slot)
    pending = None if can_move else _build_pending_question(slot)
    learning = slot.learning
    travel = slot.travel
    if slot.domain == "travel":
        outcome = f"{travel.destination or '目的地'} {travel.duration_days or ''}天旅行方案".strip()
        current_level = travel.fitness_level or None
        available_time = f"{travel.duration_days}天" if travel.duration_days else None
        deadline = travel.month or None
    else:
        outcome = learning.purpose_text or learning.target_level or learning.purpose or None
        current_level = learning.current_level_text or learning.current_level or None
        available_time = learning.daily_time or learning.duration or None
        deadline = learning.duration or None
    return UserNeedContract(
        rawUserInput=raw_input,
        interpretedGoal=slot.goal or _title_from_goal(raw_input),
        desiredOutcome=outcome,
        currentLevel=current_level,
        deadline=deadline,
        availableTime=available_time,
        hardConstraints=slot.constraints,
        softPreferences=slot.preferences,
        missingInformation=[] if can_move else _contract_missing_information(slot),
        userWordsThatMustBeRespected=slot.constraints,
        canMoveToDesign=can_move,
        clarificationQuestions=[] if can_move or pending is None else pending.questions,
        slotState=slot,
        pendingQuestion=pending,
    )


def _number_from_text(value: str) -> float | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    if match := re.search(r"\d+(?:\.\d+)?", value):
        return float(match.group(0))
    zh_digits = {
        "\u96f6": 0,
        "\u4e00": 1,
        "\u4e8c": 2,
        "\u4e24": 2,
        "\u4e09": 3,
        "\u56db": 4,
        "\u4e94": 5,
        "\u516d": 6,
        "\u4e03": 7,
        "\u516b": 8,
        "\u4e5d": 9,
        "\u534a": 0.5,
    }
    if "\u5341" in value:
        parts = value.split("\u5341", 1)
        tens = zh_digits.get(parts[0], 1 if not parts[0] else 0)
        ones = zh_digits.get(parts[1][:1], 0) if parts[1] else 0
        return float(tens * 10 + ones)
    for char, number in zh_digits.items():
        if char in value:
            return float(number)
    return None


def _minutes_from_time_text(value: str | None) -> int | None:
    if not value:
        return None
    amount = _number_from_text(value)
    if amount is None:
        return None
    lowered = value.lower()
    if "\u5206\u949f" in value or "minute" in lowered or "min" in lowered:
        return max(1, int(amount))
    return max(1, int(amount * 60))


def _daily_available_minutes(contract: UserNeedContract | None) -> int | None:
    if not contract:
        return None
    slot = contract.slot_state
    if slot and slot.domain == "learning":
        scope = slot.learning.available_time_scope or ""
        minutes = _minutes_from_time_text(slot.learning.daily_time)
        if minutes and scope == "daily":
            return minutes
        if minutes and scope == "weekly":
            return max(15, minutes // 5)
    value = contract.available_time or ""
    minutes = _minutes_from_time_text(value)
    if minutes and ("\u6bcf\u5929" in value or "daily" in value.lower() or "per day" in value.lower()):
        return minutes
    return minutes


def _learning_plan_subject(contract: UserNeedContract | None, fallback: str) -> str:
    slot = contract.slot_state if contract else None
    if slot and slot.domain == "learning" and slot.learning.subject:
        return slot.learning.subject
    text = f"{fallback} {contract.interpreted_goal if contract else ''}".lower()
    if "python" in text:
        return "Python"
    if re.search(r"\bgo\b|golang", text):
        return "Go"
    return contract.interpreted_goal if contract else _title_from_goal(fallback)


def _is_internship_learning_plan(contract: UserNeedContract | None, fallback: str) -> bool:
    text = " ".join(
        [
            fallback,
            contract.interpreted_goal if contract else "",
            contract.desired_outcome if contract and contract.desired_outcome else "",
            contract.raw_user_input if contract else "",
        ]
    )
    slot = contract.slot_state if contract else None
    purpose = slot.learning.purpose if slot and slot.domain == "learning" else ""
    return purpose in {"internship", "job"} or _contains(
        text,
        "\u5b9e\u4e60",
        "\u6c42\u804c",
        "\u7b80\u5386",
        "\u9762\u8bd5",
        "internship",
        "job",
        "resume",
        "interview",
    )


def _task_text(task: ExecutionTask) -> str:
    return " ".join(
        [
            task.title,
            task.description,
            task.deliverable,
            task.why_this_task_matters,
            " ".join(task.acceptance_criteria),
            " ".join(task.knowledge_points),
        ]
    )


def _primary_resource_title(task: ExecutionTask) -> str:
    resource = task.resource_bundle.primary or task.resource_bundle.practice
    return resource.title if resource else ""


def _generic_task_title(title: str) -> bool:
    lowered = title.lower()
    generic_phrases = (
        "\u5b66\u4e60\u5e76\u590d\u73b0",
        "\u5b8c\u6210\u4e00\u4e2a\u53ef\u68c0\u67e5\u4ea7\u51fa",
        "\u786e\u8ba4\u57fa\u7840\u4e0e\u6700\u5c0f\u8def\u5f84",
        "\u9879\u76ee\u9a71\u52a8\u7ec3\u4e60",
        "\u5305\u88c5\u4e0e\u590d\u76d8",
        "learn and reproduce",
        "checkable output",
    )
    return any(phrase in lowered or phrase in title for phrase in generic_phrases)


def _has_concrete_deliverable(task: ExecutionTask) -> bool:
    text = f"{task.title} {task.deliverable}".lower()
    concrete_markers = (
        ".py",
        ".md",
        "readme",
        "github",
        "repo",
        "json",
        "cli",
        "api",
        "todo",
        "bullet",
        "interview",
        "script",
        "\u811a\u672c",
        "\u9879\u76ee",
        "\u7b80\u5386",
        "\u9762\u8bd5",
        "\u4ed3\u5e93",
        "\u7b54\u6848",
        "\u7ec3\u4e60",
    )
    return any(marker in text for marker in concrete_markers) and len(task.deliverable.strip()) >= 6


def _topic_switch_pending(previous: PlanningSlotState, text: str) -> PendingPlanningQuestion | None:
    new_domain = _detect_slot_domain(text, previous.domain)
    if previous.domain and new_domain != previous.domain and previous.filled_slots:
        return PendingPlanningQuestion(
            askedFields=["topicSwitchConfirmation"],
            expectedAnswerType="confirmation",
            questionText="我发现你可能要切换到一个新主题。要先结束当前规划并重新开始吗？如果是，请说“重新开始一个计划”。",
            questions=["要切换到新主题并重新开始吗？如果是，请说“重新开始一个计划”。"],
        )
    return None


class DeepPlanningService:
    """Legacy template engine retained for rollout-off mode and replay compatibility."""

    engine_version = LEGACY_PLANNING_ENGINE_VERSION

    def __init__(self, memory: MemoryService | None = None):
        self.memory = memory or MemoryService()
        self.agent_runtime = PlanningAgentRuntime()

    def record_agent_artifact(self, session_id: str, *, owner_agent: str, artifact_type: str, content: Any, status: str = "draft"):
        return self.agent_runtime.record_artifact(
            session_id,
            owner_agent=owner_agent,
            artifact_type=artifact_type,
            content=content,
            status=status,
        )

    def _record_agent_step(
        self,
        session_id: str,
        *,
        agent: str,
        artifact_type: str | None,
        content: Any | None,
        artifact_status: str = "draft",
        decision: str,
        reason: str,
        summary: str,
        input_artifact_ids: list[str] | None = None,
    ) -> str | None:
        output_ids: list[str] = []
        artifact_id: str | None = None
        if artifact_type and content is not None:
            artifact = self.record_agent_artifact(
                session_id,
                owner_agent=agent,
                artifact_type=artifact_type,
                content=content,
                status=artifact_status,
            )
            artifact_id = artifact.id
            output_ids.append(artifact.id)
        self.agent_runtime.record_decision(
            session_id,
            agent=agent,
            decision=decision,
            reason=reason,
            summary=summary,
            input_artifact_ids=input_artifact_ids or [],
            output_artifact_ids=output_ids,
        )
        return artifact_id

    def _record_handoff(
        self,
        session_id: str,
        *,
        from_agent: str,
        to_agent: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> None:
        self.agent_runtime.record_message(
            session_id,
            from_agent=from_agent,
            to_agent=to_agent,
            message_type="handoff",
            reason=reason,
            payload=payload or {},
            resolved=resolved,
        )

    def create_session(self, payload: CreatePlanningSessionRequest) -> PlanningSessionResponse:
        slot_state = _slot_state_from_text(payload.user_input)
        contract = _slot_contract(payload.user_input, slot_state)
        pending_question = contract.pending_question
        now = _now()
        session_id = str(uuid4())
        status: PlanningSessionStatus = "needs_goal_clarification"
        memory_insight: MemoryInsightBrief | None = None
        resource_brief: ResourceBrief | None = None
        design: PlanDesignProposal | None = None

        if contract.can_move_to_design:
            memory_insight = self.build_memory_insight(contract, context=payload.context)
            resource_brief = self.build_resource_brief(contract, memory_insight)
            design = self.build_design_proposal(contract, memory_insight, resource_brief)
            status = "waiting_design_approval"

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO planning_sessions(
                  id, thread_id, entry_point, status, user_input,
                  user_need_contract_json, slot_state_json, pending_question_json,
                  memory_insight_json, resource_brief_json,
                  design_proposal_json, version, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    session_id,
                    payload.thread_id or "",
                    payload.entry_point,
                    status,
                    payload.user_input,
                    _dump(contract.model_dump(by_alias=True)),
                    _dump(slot_state.model_dump(by_alias=True)),
                    _dump(pending_question.model_dump(by_alias=True) if pending_question else {}),
                    _dump(memory_insight.model_dump(by_alias=True) if memory_insight else {}),
                    _dump(resource_brief.model_dump(by_alias=True) if resource_brief else {}),
                    _dump(design.model_dump(by_alias=True) if design else {}),
                    now,
                    now,
                ),
            )
        contract_artifact_id = self._record_agent_step(
            session_id,
            agent="User Advocate Agent",
            artifact_type="user_need_contract",
            content=contract,
            artifact_status="approved" if contract.can_move_to_design else "blocked",
            decision="approve" if contract.can_move_to_design else "request_user_input",
            reason="Goal is clear enough for planning." if contract.can_move_to_design else "Goal needs domain-specific clarification before planning.",
            summary="User Advocate checked the goal and protected missing user requirements.",
        )
        if contract.can_move_to_design:
            self._record_handoff(
                session_id,
                from_agent="User Advocate Agent",
                to_agent="Memory Insight Agent",
                reason="Goal contract approved; memory should influence planning.",
                payload={"artifactId": contract_artifact_id},
                resolved=True,
            )
            memory_artifact_id = self._record_agent_step(
                session_id,
                agent="Memory Insight Agent",
                artifact_type="memory_insight_brief",
                content=memory_insight,
                decision="produce_artifact",
                reason="Memory context was translated into planning constraints.",
                summary="Memory Insight produced planning rules and warnings.",
                input_artifact_ids=[contract_artifact_id] if contract_artifact_id else [],
            )
            self._record_handoff(
                session_id,
                from_agent="Memory Insight Agent",
                to_agent="Resource Intelligence Agent",
                reason="Memory rules are ready; resources can be matched.",
                payload={"artifactId": memory_artifact_id},
                resolved=True,
            )
            resource_artifact_id = self._record_agent_step(
                session_id,
                agent="Resource Intelligence Agent",
                artifact_type="resource_brief",
                content=resource_brief,
                decision="produce_artifact",
                reason="Resource candidates and coverage were selected for the approved goal.",
                summary="Resource Intelligence produced resource coverage and candidates.",
                input_artifact_ids=[item for item in (contract_artifact_id, memory_artifact_id) if item],
            )
            self._record_handoff(
                session_id,
                from_agent="Resource Intelligence Agent",
                to_agent="Plan Co-Designer Agent",
                reason="Resources are available for design proposal.",
                payload={"artifactId": resource_artifact_id},
                resolved=True,
            )
            self._record_agent_step(
                session_id,
                agent="Plan Co-Designer Agent",
                artifact_type="plan_design_proposal",
                content=design,
                decision="request_user_input",
                reason="A design proposal was produced and must be approved by the user before execution planning.",
                summary="Plan Co-Designer produced a planning direction and stopped at the design gate.",
                input_artifact_ids=[item for item in (contract_artifact_id, memory_artifact_id, resource_artifact_id) if item],
            )
        return self.get_session(session_id)

    def clarify(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        previous_slot = session.slot_state or _slot_state_from_text(session.user_input)
        topic_switch = _topic_switch_pending(previous_slot, payload.text)
        if topic_switch:
            contract = session.user_need_contract or _slot_contract(session.user_input, previous_slot)
            contract.pending_question = topic_switch
            contract.clarification_questions = topic_switch.questions
            contract.missing_information = ["topicSwitchConfirmation"]
            self._update_session(
                session_id,
                status="needs_goal_clarification",
                userNeedContract=contract,
                slotState=previous_slot,
                pendingQuestion=topic_switch,
                memoryInsight=None,
                resourceBrief=None,
                designProposal=None,
                executionDraft=None,
            )
            self._record_agent_step(
                session_id,
                agent="User Advocate Agent",
                artifact_type="user_need_contract",
                content=contract,
                artifact_status="blocked",
                decision="request_user_input",
                reason="A possible topic switch was detected and must be confirmed before mixing planning state.",
                summary="User Advocate blocked a topic switch until the user confirms restarting.",
            )
            return self.get_session(session_id)

        updated_slot = _slot_state_from_text(payload.text, previous_slot)
        combined = "\n".join(part.strip() for part in (session.user_input, payload.text) if part and part.strip())
        contract = _slot_contract(session.user_input, updated_slot)
        with get_conn() as conn:
            conn.execute(
                "UPDATE planning_sessions SET user_input = ?, updated_at = ? WHERE id = ?",
                (combined, _now(), session_id),
            )
        return self._rebuild_from_goal(session_id, slot_state=updated_slot, contract=contract)

    def revise_design(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        contract = session.user_need_contract or _user_need_contract(session.user_input)
        memory_insight = self.build_memory_insight(contract, context={})
        resource_brief = session.resource_brief or self.build_resource_brief(contract, memory_insight)
        design = self.build_design_proposal(contract, memory_insight, resource_brief, feedback=payload.text)
        design.status = "waiting_user_approval"
        self._update_session(
            session_id,
            status="waiting_design_approval",
            memoryInsight=memory_insight,
            resourceBrief=resource_brief,
            designProposal=design,
            executionDraft=None,
        )
        self._record_agent_step(
            session_id,
            agent="Plan Co-Designer Agent",
            artifact_type="plan_design_proposal",
            content=design,
            decision="revise_artifact",
            reason=payload.text or "The user requested a design revision.",
            summary="Plan Co-Designer revised the design proposal and stopped for user approval.",
        )
        return self.get_session(session_id)

    def approve_design(self, session_id: str) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        if session.status != "waiting_design_approval" or not session.design_proposal:
            raise HTTPException(status_code=409, detail={"message": "design proposal is not waiting for approval"})
        design = session.design_proposal
        design.status = "approved"
        memory_insight = self.build_memory_insight(session.user_need_contract or _user_need_contract(session.user_input), context={})
        resource_brief = session.resource_brief or self.build_resource_brief(session.user_need_contract or _user_need_contract(session.user_input), memory_insight)
        execution = self.build_execution_draft(session, design, resource_brief)
        self._update_session(
            session_id,
            status="waiting_execution_approval",
            memoryInsight=memory_insight,
            resourceBrief=resource_brief,
            designProposal=design,
            executionDraft=execution,
        )
        design_artifact_id = self._record_agent_step(
            session_id,
            agent="Plan Co-Designer Agent",
            artifact_type="plan_design_proposal",
            content=design,
            artifact_status="approved",
            decision="approve",
            reason="The user approved the design direction.",
            summary="Plan Co-Designer marked the design proposal as approved.",
        )
        self._record_handoff(
            session_id,
            from_agent="Plan Co-Designer Agent",
            to_agent="Execution Planner Agent",
            reason="Approved design can now be turned into execution tasks.",
            payload={"artifactId": design_artifact_id},
            resolved=True,
        )
        self._record_agent_step(
            session_id,
            agent="Execution Planner Agent",
            artifact_type="execution_plan_draft",
            content=execution,
            decision="produce_artifact",
            reason="Approved design and resource brief were converted into execution tasks.",
            summary="Execution Planner produced the execution plan draft with resource bundles.",
            input_artifact_ids=[design_artifact_id] if design_artifact_id else [],
        )
        return self.get_session(session_id)

    def approve_execution(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        if session.status != "waiting_execution_approval" or not session.execution_draft:
            raise HTTPException(status_code=409, detail={"message": "execution draft is not waiting for approval"})
        self.validate_execution_draft(session.execution_draft, accept_missing_resources=accept_missing_resources)
        execution = session.execution_draft
        execution.status = "approved"
        self._update_session(session_id, status="ready_to_write_calendar", executionDraft=execution)
        self._record_agent_step(
            session_id,
            agent="Execution Planner Agent",
            artifact_type="execution_plan_draft",
            content=execution,
            artifact_status="approved",
            decision="approve",
            reason="The user approved the execution plan and it passed the execution gate.",
            summary="Execution Planner marked the execution draft as ready for Calendar write.",
        )
        return self.get_session(session_id)

    def revise_execution(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        if not session.execution_draft:
            raise HTTPException(status_code=409, detail={"message": "execution draft is not available"})
        patch = self.build_learning_patch(payload.text, session)
        execution = self.apply_learning_patch(session.execution_draft, patch)
        execution.status = "waiting_user_approval"
        self.persist_learning_patch(patch, session)
        self._update_session(session_id, status="waiting_execution_approval", executionDraft=execution, learningPatch=patch)
        patch_artifact_id = self._record_agent_step(
            session_id,
            agent="Feedback Evolution Agent",
            artifact_type="learning_patch",
            content=patch,
            decision="handoff",
            reason="User feedback was translated into an immediate patch and long-term learning.",
            summary="Feedback Evolution produced a learning patch from user feedback.",
        )
        target_agent = "Resource Intelligence Agent" if patch.immediate_patch and patch.immediate_patch.action == "replace_resource" else "Execution Planner Agent"
        self.agent_runtime.record_message(
            session_id,
            from_agent="Feedback Evolution Agent",
            to_agent=target_agent,
            message_type="revision_request",
            reason=patch.immediate_patch.instruction if patch.immediate_patch else patch.insight,
            payload={"learningPatchId": patch_artifact_id, "feedbackType": patch.feedback_type},
            resolved=True,
        )
        self._record_agent_step(
            session_id,
            agent="Execution Planner Agent",
            artifact_type="execution_plan_draft",
            content=execution,
            artifact_status="needs_revision",
            decision="revise_artifact",
            reason="Execution draft was revised according to the feedback patch.",
            summary="Execution Planner revised the execution draft after feedback.",
            input_artifact_ids=[patch_artifact_id] if patch_artifact_id else [],
        )
        return self.get_session(session_id)

    def submit_feedback(self, session_id: str, payload: PlanningSessionTextRequest) -> PlanningSessionResponse:
        return self.revise_execution(session_id, payload)

    def prepare_calendar_write(self, session_id: str, *, accept_missing_resources: bool = False) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        if session.status != "ready_to_write_calendar" or not session.execution_draft:
            raise HTTPException(status_code=409, detail={"message": "execution draft must be approved before writing calendar"})
        self.validate_execution_draft(session.execution_draft, accept_missing_resources=accept_missing_resources, require_quality=True)
        self._update_session(session_id, status="waiting_calendar_write_approval")
        return self.get_session(session_id)

    def mark_calendar_written(self, session_id: str) -> None:
        self._update_session(session_id, status="written_to_calendar")

    def latest_for_thread(self, thread_id: str) -> PlanningSessionResponse | None:
        if not thread_id:
            return None
        with get_conn() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM planning_sessions
                WHERE thread_id = ? AND status IN ({','.join('?' for _ in ACTIVE_SESSION_STATUSES)})
                ORDER BY updated_at DESC LIMIT 1
                """,
                (thread_id, *sorted(ACTIVE_SESSION_STATUSES)),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def get_session(self, session_id: str) -> PlanningSessionResponse:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM planning_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"message": "planning session not found"})
        return self._row_to_session(row)

    def build_memory_insight(self, contract: UserNeedContract, *, context: dict[str, Any] | None = None) -> MemoryInsightBrief:
        query = " ".join([contract.interpreted_goal, contract.raw_user_input]).strip()
        hits: dict[str, list[MemoryHit]] = {}
        for kind in ("preference", "review", "planning_history", "material", "note"):
            items = self.memory.search_memories(query, kinds=[kind], limit=3)
            hits[kind] = [_memory_hit(item, f"与“{contract.interpreted_goal}”相关") for item in items]

        rules = [hit.summary for hit in hits["preference"][:3]]
        warnings = [hit.summary for hit in hits["review"][:3]]
        positives = [hit.summary for hit in hits["planning_history"][:2]]
        if not rules:
            rules.append("暂无明确长期偏好，采用轻量、可确认的默认节奏。")
        if "每天" in " ".join(contract.hard_constraints):
            rules.extend(contract.hard_constraints)

        calendar_constraints: list[str] = []
        try:
            plans = list_plans(_context_date(context))
            if plans:
                calendar_constraints.append(f"当天已有 {len(plans)} 个计划，新增任务应避免过密。")
        except Exception:
            calendar_constraints.append("Calendar 约束读取失败，本次按保守节奏安排。")

        total_hits = sum(len(value) for value in hits.values())
        return MemoryInsightBrief(
            memoryHits=MemoryInsightHits(
                preferences=hits["preference"],
                reviews=hits["review"],
                planningHistory=hits["planning_history"],
                materials=hits["material"],
                notes=hits["note"],
            ),
            planningInsights=PlanningInsights(
                userStyleRules=rules,
                pastFailureWarnings=warnings,
                positivePatterns=positives,
                constraintsToRespect=contract.hard_constraints,
            ),
            calendarConstraints=calendar_constraints,
            confidence=0.75 if total_hits else 0.35,
            missingMemoryWarning=None if total_hits else "未找到足够相关记忆，本次需要更多用户确认并使用默认假设。",
        )

    def build_resource_brief(self, contract: UserNeedContract, memory_insight: MemoryInsightBrief) -> ResourceBrief:
        candidates = self._resource_candidates(contract, memory_insight)
        missing_topics: list[str] = []
        if not candidates:
            missing_topics.append(contract.interpreted_goal)
        status = "strong" if len(candidates) >= 4 else "partial" if candidates else "weak"
        fallback = "use_builtin_catalog" if candidates else "use_ai_micro_material"
        return ResourceBrief(
            resourceCandidates=candidates,
            coverage=ResourceCoverage(
                status=status,
                missingTopics=missing_topics,
                explanation="已结合记忆库、内置资源目录、项目模板和练习库选择资源。" if candidates else "未找到足够资源，将使用 AI micro material 和搜索关键词兜底。",
                fallbackStrategy=fallback,
            ),
            resourceRulesForThisPlan=[
                "优先选择能直接产出作品或练习结果的资源。",
                "每个任务只给 1 个主资源和 1 个练习/兜底资源，避免资料过载。",
                "如果用户反馈资源太难，优先替换为项目模板或练习库。",
            ],
        )

    def build_design_proposal(
        self,
        contract: UserNeedContract,
        memory_insight: MemoryInsightBrief,
        resource_brief: ResourceBrief,
        *,
        feedback: str = "",
    ) -> PlanDesignProposal:
        goal = contract.interpreted_goal
        plan_style = "career_portfolio" if _contains(goal + contract.raw_user_input, "实习", "简历", "作品集") else "project_driven"
        if feedback and _contains(feedback, "太理论", "项目", "实战"):
            plan_style = "project_driven"
        resources = [item.title for item in resource_brief.resource_candidates[:3]]
        phases = [
            PlanDesignPhase(title="确认基础与最小路径", purpose="避免泛泛学习，先补齐完成目标所需的最小知识。", expectedOutput="一份清晰的学习起点和第一个小产出", resourcesToUse=resources[:2], whyNeeded="目标越具体，后续任务越容易执行。"),
            PlanDesignPhase(title="项目驱动练习", purpose="用小项目证明能力，而不是只看资料。", expectedOutput="可运行的小练习或项目片段", resourcesToUse=resources[:3], whyNeeded="项目产出更适合复盘、简历和面试表达。"),
            PlanDesignPhase(title="包装与复盘", purpose="把学习成果整理成可展示材料，并根据执行反馈修正节奏。", expectedOutput="README/简历 bullet/复盘记录", resourcesToUse=resources[-2:], whyNeeded="最终目标不是学完，而是能展示和持续改进。"),
        ]
        if feedback:
            phases[0].purpose = f"根据你的反馈“{feedback[:80]}”调整方向，先降低不匹配部分。"
        return PlanDesignProposal(
            designId=str(uuid4()),
            strategyName="作品集驱动的深度规划" if plan_style == "career_portfolio" else "项目驱动的稳步学习计划",
            targetOutcome=contract.desired_outcome or goal,
            planStyle=plan_style,
            phases=phases,
            designRationale="该方向结合了用户目标、记忆洞察和资源覆盖情况，先确认方向，再拆执行任务。",
            assumptions=contract.missing_information or ["按用户已提供信息推进，后续可继续修订。"],
            userBenefits=["先确认方向，避免直接生成泛泛任务。", "任务会绑定学习资源和交付物。", "反馈会影响当前计划和未来规划规则。"],
            tradeoffs=["第一版不默认联网搜索，因此外部资源使用静态目录和搜索关键词兜底。"],
            questionsForUser=["这个方向是否符合你的真实目标？", "是否需要降低任务密度或更偏项目实战？"],
            status="waiting_user_approval",
        )

    def build_execution_draft(self, session: PlanningSessionResponse, design: PlanDesignProposal, resource_brief: ResourceBrief) -> ExecutionPlanDraft:
        start = datetime.fromisoformat(_context_date({})).date()
        duration = _duration_days(session.user_input)
        spacing = max(1, duration // max(4, len(design.phases) * 2))
        candidates = resource_brief.resource_candidates
        tasks: list[ExecutionTask] = []
        index = 0
        for phase in design.phases:
            for step in ("学习并复现", "完成一个可检查产出"):
                candidate = candidates[index % len(candidates)] if candidates else self._ai_generated_candidate(session.user_input, index)
                practice = next((item for item in candidates if item.source_type in {"practice_bank", "project_template"}), candidate)
                bundle = TaskResourceBundle(
                    primary=self._resource_for_task(candidate, use_step="先按指定 section 或关键词完成最小阅读，不通读整份资料。"),
                    practice=self._resource_for_task(practice, use_step="直接做一个可运行/可提交的小练习。"),
                    fallback=self._resource_for_task(self._ai_generated_candidate(session.user_input, index), use_step="如果主资源看不懂，先按 AI micro material 做最小示例。"),
                )
                due = (start + timedelta(days=index * spacing)).isoformat()
                title = f"{phase.title}：{step}"
                tasks.append(
                    ExecutionTask(
                        title=title,
                        description=phase.purpose,
                        dueDate=due,
                        estimatedMinutes=45 if index < 2 else 60,
                        priority="high" if index < 2 else "medium",
                        whyThisTaskMatters=phase.why_needed,
                        acceptanceCriteria=["完成资源中的最小练习或示例。", "写下 3 条理解/卡点。", "产出物能被自己或他人检查。"],
                        deliverable=phase.expected_output,
                        fallbackAdjustment="如果做不动，先缩小到 20 分钟，只完成主资源的第一个示例并记录卡点。",
                        knowledgePoints=candidate.topics or [candidate.domain or session.user_input[:30]],
                        resourceBundle=bundle,
                        resourceCoverage=ExecutionTaskResourceCoverage(status="partial" if resource_brief.coverage.status == "weak" else resource_brief.coverage.status, explanation=resource_brief.coverage.explanation),
                    )
                )
                index += 1
        return ExecutionPlanDraft(
            designId=design.design_id,
            tasks=tasks,
            reviewCadence="每天结束后记录完成情况和卡点；每周根据反馈调整任务密度。",
            riskPlan=["任务过重时优先减少阅读量，保留可检查产出。", "资源看不懂时切换到 practice/fallback 资源。"],
            scheduleSummary=f"按 {duration} 天规划，先低密度推进，再根据反馈修订。",
            resourceCoverageSummary=resource_brief.coverage.explanation,
            status="waiting_user_approval",
        )

    def validate_execution_draft(
        self,
        execution: ExecutionPlanDraft,
        *,
        accept_missing_resources: bool = False,
        require_quality: bool = False,
    ) -> None:
        if not execution.tasks:
            raise HTTPException(status_code=422, detail={"message": "execution draft has no tasks"})
        for index, task in enumerate(execution.tasks, start=1):
            if not task.acceptance_criteria:
                raise HTTPException(status_code=422, detail={"message": f"task {index} missing acceptanceCriteria"})
            if not task.deliverable.strip():
                raise HTTPException(status_code=422, detail={"message": f"task {index} missing deliverable"})
            bundle = task.resource_bundle
            if not (bundle.primary or bundle.practice):
                raise HTTPException(status_code=422, detail={"message": f"task {index} missing resourceBundle"})
            if task.resource_coverage.status == "missing" and not accept_missing_resources:
                raise HTTPException(status_code=422, detail={"message": f"task {index} resource coverage is missing"})
        if require_quality and execution.quality_status != "passed":
            report = execution.quality_report
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "execution plan quality is not strong enough for Calendar write",
                    "qualityStatus": execution.quality_status or "needs_repair",
                    "blockers": report.blockers if report else ["missing execution plan quality report"],
                    "repairSuggestions": report.repair_suggestions if report else ["regenerate or revise the execution plan before writing Calendar"],
                },
            )

    def build_learning_patch(self, feedback: str, session: PlanningSessionResponse) -> LearningPatch:
        resource_feedback = _contains(feedback, "资源", "资料", "文档", "看不懂", "太理论")
        heavy_feedback = _contains(feedback, "太难", "太多", "任务太重", "没完成", "时间不够")
        if resource_feedback:
            feedback_type = "resource_feedback"
            scope = "resource_selection"
            immediate = LearningImmediatePatch(target="resource", action="replace_resource", instruction="把官方/理论资源降级为辅助资源，优先推荐项目模板或练习库。")
            rule = "初学或反馈看不懂时，不优先推荐纯理论资源；先给项目示例和小练习。"
        elif heavy_feedback:
            feedback_type = "execution_failure"
            scope = "current_plan"
            immediate = LearningImmediatePatch(target="execution_task", action="reduce_load", instruction="把任务拆小，并将单次任务压到 30 分钟以内。")
            rule = "用户反馈任务过重时，后续计划应减少单日任务量，并保留降级版本。"
        else:
            feedback_type = "preference" if _contains(feedback, "喜欢", "更适合", "偏好") else "positive"
            scope = "future_plans"
            immediate = None
            rule = f"未来规划参考用户反馈：{feedback[:120]}"
        return LearningPatch(
            feedbackType=feedback_type,
            affectedScope=scope,
            insight=f"用户反馈：{feedback}",
            reflection=LearningReflection(
                whatWentWrong="资源或任务强度与用户当前状态不匹配。" if resource_feedback or heavy_feedback else None,
                whyItHappened="规划阶段对用户可承受资源难度和任务密度估计偏乐观。" if resource_feedback or heavy_feedback else None,
                howToAvoidNextTime=rule,
            ),
            immediatePatch=immediate,
            longTermLearning=LongTermLearning(newRule=rule, confidence=0.75, evidence=feedback, appliesToDomains=[session.user_need_contract.interpreted_goal if session.user_need_contract else session.user_input]),
            memoryUpdates=[
                LearningMemoryUpdate(kind="preference" if feedback_type in {"preference", "resource_feedback"} else "review", title="规划反馈规则", content=rule),
                LearningMemoryUpdate(kind="review", title="执行反馈复盘", content=f"反馈：{feedback}\n反思：{rule}"),
            ],
        )

    def apply_learning_patch(self, execution: ExecutionPlanDraft, patch: LearningPatch) -> ExecutionPlanDraft:
        data = execution.model_dump(by_alias=True)
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        if patch.immediate_patch and patch.immediate_patch.action in {"reduce_load", "split_task"}:
            for task in tasks:
                if isinstance(task, dict):
                    task["estimatedMinutes"] = min(int(task.get("estimatedMinutes") or 45), 30)
                    task["fallbackAdjustment"] = "降级为 15-20 分钟，只完成最小可检查产出并记录卡点。"
        if patch.immediate_patch and patch.immediate_patch.action == "replace_resource":
            for task in tasks[:1]:
                if isinstance(task, dict):
                    bundle = task.get("resourceBundle") if isinstance(task.get("resourceBundle"), dict) else {}
                    bundle["primary"] = {
                        "title": "项目示例 + 小练习兜底资源",
                        "sourceType": "practice_bank",
                        "searchKeyword": f"{task.get('title', '')} 入门 练习",
                        "useStep": "先完成一个最小练习，再回头看官方文档。",
                        "estimatedMinutes": 20,
                        "whyThisResource": "用户反馈原资源太难，改为更可执行的小练习。",
                        "expectedOutput": "一个可检查的小练习结果。",
                        "fallbackIfTooHard": "只写下问题和下一步要查的关键词。",
                    }
                    task["resourceBundle"] = bundle
        data["tasks"] = tasks
        data["status"] = "revision_needed"
        return ExecutionPlanDraft.model_validate(data)

    def persist_learning_patch(self, patch: LearningPatch, session: PlanningSessionResponse) -> None:
        goal_text = session.user_need_contract.interpreted_goal if session.user_need_contract else session.user_input
        for update in patch.memory_updates:
            content = f"Goal: {goal_text}\n{update.content}"
            self.memory.create_memory(
                MemoryCreate(
                    kind=update.kind,
                    title=f"{update.title} - {goal_text[:48]}",
                    content=content,
                    summary=f"{patch.insight[:160]} Goal: {goal_text[:80]}",
                    source="ai",
                    sourceKey=f"planning-session:{session.session_id}:{update.kind}:{abs(hash(update.content))}",
                    metadata={
                        "planningSessionId": session.session_id,
                        "goal": goal_text,
                        "feedbackType": patch.feedback_type,
                        "longTermLearning": patch.long_term_learning.model_dump(by_alias=True) if patch.long_term_learning else None,
                    },
                )
            )

    def execution_to_structured_plan(self, session: PlanningSessionResponse) -> dict[str, Any]:
        if not session.execution_draft:
            raise HTTPException(status_code=409, detail={"message": "execution draft is not available"})
        title = session.user_need_contract.interpreted_goal if session.user_need_contract else _title_from_goal(session.user_input)
        tasks = []
        for task in session.execution_draft.tasks:
            tasks.append(
                {
                    "title": task.title,
                    "description": "\n".join(
                        [
                            task.description,
                            f"为什么做：{task.why_this_task_matters}",
                            f"产出物：{task.deliverable}",
                            f"做不动时：{task.fallback_adjustment}",
                            f"去哪学：{self._bundle_summary(task.resource_bundle)}",
                        ]
                    ),
                    "estimatedMinutes": task.estimated_minutes,
                    "dueDate": task.due_date,
                    "priority": task.priority,
                    "learningResources": self._legacy_learning_resources(task.resource_bundle),
                    "resourceBundle": task.resource_bundle.model_dump(by_alias=True),
                }
            )
        return {
            "goalTitle": title,
            "goalDescription": session.design_proposal.target_outcome if session.design_proposal else session.user_input,
            "durationDays": max(1, _duration_days(session.user_input)),
            "milestones": [
                {
                    "title": "已确认执行计划",
                    "description": session.execution_draft.schedule_summary,
                    "tasks": tasks,
                }
            ],
            "reviewPlan": {
                "frequency": "daily",
                "questions": ["今天完成了什么？", "资源是否合适？", "任务是否需要降级？"],
            },
        }

    def _rebuild_from_goal(
        self,
        session_id: str,
        *,
        slot_state: PlanningSlotState | None = None,
        contract: UserNeedContract | None = None,
    ) -> PlanningSessionResponse:
        session = self.get_session(session_id)
        slot_state = slot_state or session.slot_state or _slot_state_from_text(session.user_input)
        contract = contract or _slot_contract(session.user_input, slot_state)
        pending = contract.pending_question
        memory_insight = resource_brief = design = None
        status: PlanningSessionStatus = "needs_goal_clarification"
        if contract.can_move_to_design:
            memory_insight = self.build_memory_insight(contract, context={})
            resource_brief = self.build_resource_brief(contract, memory_insight)
            design = self.build_design_proposal(contract, memory_insight, resource_brief)
            status = "waiting_design_approval"
        self._update_session(
            session_id,
            status=status,
            userNeedContract=contract,
            slotState=slot_state,
            pendingQuestion=pending,
            memoryInsight=memory_insight,
            resourceBrief=resource_brief,
            designProposal=design,
            executionDraft=None,
        )
        contract_artifact_id = self._record_agent_step(
            session_id,
            agent="User Advocate Agent",
            artifact_type="user_need_contract",
            content=contract,
            artifact_status="approved" if contract.can_move_to_design else "blocked",
            decision="approve" if contract.can_move_to_design else "request_user_input",
            reason="Clarification made the goal clear enough." if contract.can_move_to_design else "Clarification was merged, but required planning slots are still missing.",
            summary="User Advocate updated the goal contract from the latest user reply.",
        )
        if contract.can_move_to_design:
            self._record_handoff(
                session_id,
                from_agent="User Advocate Agent",
                to_agent="Memory Insight Agent",
                reason="Updated contract approved; memory should be re-read.",
                payload={"artifactId": contract_artifact_id},
                resolved=True,
            )
            memory_artifact_id = self._record_agent_step(
                session_id,
                agent="Memory Insight Agent",
                artifact_type="memory_insight_brief",
                content=memory_insight,
                decision="produce_artifact",
                reason="Memory was refreshed after clarification.",
                summary="Memory Insight refreshed planning rules.",
                input_artifact_ids=[contract_artifact_id] if contract_artifact_id else [],
            )
            self._record_handoff(
                session_id,
                from_agent="Memory Insight Agent",
                to_agent="Resource Intelligence Agent",
                reason="Memory brief is ready for resource selection.",
                payload={"artifactId": memory_artifact_id},
                resolved=True,
            )
            resource_artifact_id = self._record_agent_step(
                session_id,
                agent="Resource Intelligence Agent",
                artifact_type="resource_brief",
                content=resource_brief,
                decision="produce_artifact",
                reason="Resources were refreshed after clarification.",
                summary="Resource Intelligence refreshed resource coverage.",
                input_artifact_ids=[item for item in (contract_artifact_id, memory_artifact_id) if item],
            )
            self._record_handoff(
                session_id,
                from_agent="Resource Intelligence Agent",
                to_agent="Plan Co-Designer Agent",
                reason="Resource brief is ready for design.",
                payload={"artifactId": resource_artifact_id},
                resolved=True,
            )
            self._record_agent_step(
                session_id,
                agent="Plan Co-Designer Agent",
                artifact_type="plan_design_proposal",
                content=design,
                decision="request_user_input",
                reason="A refreshed design proposal is ready for user approval.",
                summary="Plan Co-Designer refreshed the planning direction.",
                input_artifact_ids=[item for item in (contract_artifact_id, memory_artifact_id, resource_artifact_id) if item],
            )
        return self.get_session(session_id)

    def _resource_candidates(self, contract: UserNeedContract, memory_insight: MemoryInsightBrief) -> list[ResourceCandidate]:
        candidates: list[ResourceCandidate] = []
        for hit in memory_insight.memory_hits.materials[:3]:
            candidates.append(self._candidate(f"memory:{hit.id}", hit.title, "user_material", contract.interpreted_goal, [hit.title], "使用你已保存的资料，先读摘要并提取可执行练习。", hit.summary or "资料摘要"))

        text = f"{contract.raw_user_input} {contract.interpreted_goal}".lower()
        if "python" in text:
            candidates.extend(
                [
                    self._candidate("catalog:python:tutorial", "Python 官方教程 - Control Flow", "official_doc", "Python", ["Python", "控制流", "函数"], "只读 Control Flow 和 Functions 的最小示例。", "写出 if/for/function 小练习", url="https://docs.python.org/3/tutorial/controlflow.html", section="Control Flow Tools"),
                    self._candidate("practice:python:if-for", "Python if/for 小练习", "practice_bank", "Python", ["if/else", "for loop"], "完成 3 个输入输出小题，不追求完整项目。", "3 个可运行脚本", search="Python if for loop beginner exercises"),
                ]
            )
        if "fastapi" in text:
            candidates.append(self._candidate("catalog:fastapi:first-steps", "FastAPI Tutorial - First Steps", "official_doc", "FastAPI", ["API", "路由"], "只复现第一个 GET 接口示例。", "一个能运行的 GET endpoint", url="https://fastapi.tiangolo.com/tutorial/first-steps/", section="First Steps"))
        if "react" in text:
            candidates.append(self._candidate("catalog:react:state", "React Docs - State", "official_doc", "React", ["state", "component"], "只看 useState 示例并改一个按钮状态。", "一个状态切换组件", url="https://react.dev/learn/state-a-components-memory", section="State: A Component's Memory"))
        if _contains(text, "实习", "作品集", "简历", "portfolio"):
            candidates.extend(
                [
                    self._candidate("template:portfolio:readme", "项目 README 包装模板", "project_template", "portfolio", ["README", "项目表达"], "把项目目标、架构、亮点写成 4 段。", "一版 README 项目介绍"),
                    self._candidate("practice:resume:star", "STAR 简历 bullet 改写练习", "practice_bank", "resume", ["简历", "STAR"], "把一个项目动作改成结果导向 bullet。", "3 条简历 bullet"),
                ]
            )
        if not candidates:
            candidates.append(self._ai_generated_candidate(contract.interpreted_goal, 0))
        return candidates[:8]

    def _candidate(
        self,
        candidate_id: str,
        title: str,
        source_type: str,
        domain: str,
        topics: list[str],
        how_to_use: str,
        expected_output: str,
        *,
        url: str | None = None,
        section: str | None = None,
        search: str | None = None,
    ) -> ResourceCandidate:
        return ResourceCandidate(
            id=candidate_id,
            title=title,
            sourceType=source_type,
            url=url,
            section=section,
            searchKeyword=search,
            domain=domain,
            topics=topics,
            difficulty="beginner",
            language="mixed",
            estimatedMinutes=25,
            howToUse=how_to_use,
            expectedOutput=expected_output,
            fallbackIfTooHard="缩小到第一个示例或改用搜索关键词找入门教程。",
            fitScore=ResourceFitScore(total=78, levelFit=75, taskFit=80, userPreferenceFit=78, timeFit=75, credibility=80, actionability=80, reasons=["可直接产生练习结果"], risks=["如果基础不足，需要先做 fallback"]),
        )

    def _ai_generated_candidate(self, goal: str, index: int) -> ResourceCandidate:
        topic = _resource_topic_from_goal(goal)
        return self._candidate(
            f"ai-micro:{index}",
            f"{topic} 入门微练习",
            "ai_generated",
            topic,
            [topic],
            "使用 AI 生成的最小解释和示例，先完成一个可检查动作。",
            "一段笔记和一个最小练习结果",
            search=f"{topic} 入门 练习",
        )

    def _resource_for_task(self, candidate: ResourceCandidate, *, use_step: str) -> TaskLearningResource:
        return TaskLearningResource(
            title=candidate.title,
            sourceType=candidate.source_type,
            url=candidate.url,
            section=candidate.section,
            searchKeyword=candidate.search_keyword,
            useStep=use_step or candidate.how_to_use,
            estimatedMinutes=min(candidate.estimated_minutes, 45),
            whyThisResource=candidate.fit_score.reasons[0] if candidate.fit_score.reasons else candidate.how_to_use,
            expectedOutput=candidate.expected_output,
            fallbackIfTooHard=candidate.fallback_if_too_hard,
        )

    def _bundle_summary(self, bundle: TaskResourceBundle) -> str:
        names = [item.title for item in (bundle.primary, bundle.support, bundle.practice, bundle.fallback) if item]
        return "；".join(names)

    def _legacy_learning_resources(self, bundle: TaskResourceBundle) -> list[dict[str, Any]]:
        values = []
        for item in (bundle.primary, bundle.support, bundle.practice, bundle.fallback):
            if not item:
                continue
            values.append(
                {
                    "title": item.title,
                    "type": "official_doc" if item.source_type == "official_doc" else "search_keyword",
                    "url": item.url,
                    "searchKeyword": item.search_keyword,
                    "reason": item.why_this_resource,
                }
            )
        return values

    def build_memory_insight(self, contract: UserNeedContract, *, context: dict[str, Any] | None = None) -> MemoryInsightBrief:
        query = " ".join([contract.interpreted_goal, contract.raw_user_input]).strip()
        hits: dict[str, list[MemoryHit]] = {}
        for kind in ("preference", "review", "planning_history", "material", "note"):
            items = self.memory.search_memories(query, kinds=[kind], limit=3)
            hits[kind] = [_memory_hit(item, f"与「{contract.interpreted_goal}」相关") for item in items]

        rules = [hit.summary for hit in hits["preference"][:3]]
        warnings = [hit.summary for hit in hits["review"][:3]]
        positives = [hit.summary for hit in hits["planning_history"][:2]]
        if not rules:
            rules.append("暂未找到明确长期偏好，采用轻量、可确认、可降级的默认节奏。")
        if contract.hard_constraints:
            rules.extend(contract.hard_constraints)

        calendar_constraints: list[str] = []
        try:
            plans = list_plans(_context_date(context))
            if plans:
                calendar_constraints.append(f"当天已有 {len(plans)} 个计划，新任务应避免过密。")
        except Exception:
            calendar_constraints.append("Calendar 约束读取失败，本次按保守节奏安排。")

        total_hits = sum(len(value) for value in hits.values())
        return MemoryInsightBrief(
            memoryHits=MemoryInsightHits(
                preferences=hits["preference"],
                reviews=hits["review"],
                planningHistory=hits["planning_history"],
                materials=hits["material"],
                notes=hits["note"],
            ),
            planningInsights=PlanningInsights(
                userStyleRules=rules,
                pastFailureWarnings=warnings,
                positivePatterns=positives,
                constraintsToRespect=contract.hard_constraints,
            ),
            calendarConstraints=calendar_constraints,
            confidence=0.75 if total_hits else 0.35,
            missingMemoryWarning=None if total_hits else "未找到足够相关记忆，本次需要更多用户确认或使用默认假设。",
        )

    def build_resource_brief(self, contract: UserNeedContract, memory_insight: MemoryInsightBrief) -> ResourceBrief:
        candidates = self._resource_candidates(contract, memory_insight)
        real_candidates = [item for item in candidates if item.source_type != "ai_generated"]
        if len(real_candidates) >= 4:
            status = "strong"
        elif len(real_candidates) >= 2:
            status = "partial"
        elif candidates:
            status = "weak"
        else:
            status = "missing"
        fallback = "use_ai_micro_material" if status in {"weak", "missing"} else "use_builtin_catalog"
        missing_topics = [] if candidates else [contract.interpreted_goal]
        explanation = (
            "已结合记忆库、内置资源目录、官方文档静态映射、项目模板和练习库选择资源。"
            if candidates
            else "没有足够资源，必须先询问用户或使用 AI micro material 兜底。"
        )
        return ResourceBrief(
            resourceCandidates=candidates,
            coverage=ResourceCoverage(
                status=status,
                missingTopics=missing_topics,
                explanation=explanation,
                fallbackStrategy=fallback,
            ),
            resourceRulesForThisPlan=[
                "优先选择能直接产出作品或练习结果的资源。",
                "每个任务只给少量关键资源，避免资料过载。",
                "如果用户反馈资源太难，优先替换为项目模板或练习库。",
            ],
        )

    def build_design_proposal(
        self,
        contract: UserNeedContract,
        memory_insight: MemoryInsightBrief,
        resource_brief: ResourceBrief,
        *,
        feedback: str = "",
    ) -> PlanDesignProposal:
        goal = contract.interpreted_goal
        plan_style = "career_portfolio" if _contains(goal + contract.raw_user_input, "实习", "简历", "作品集", "portfolio", "internship") else "project_driven"
        if feedback and _contains(feedback, "太理论", "项目", "实战", "project"):
            plan_style = "project_driven"
        resources = [item.title for item in resource_brief.resource_candidates[:3]]
        phases = [
            PlanDesignPhase(
                title="确认基础与最小路径",
                purpose="避免泛泛学习，先补齐完成目标所需的最小知识。",
                expectedOutput="一份清晰的学习起点和第一个小产出。",
                resourcesToUse=resources[:2],
                whyNeeded="目标越具体，后续任务越容易执行。",
            ),
            PlanDesignPhase(
                title="项目驱动练习",
                purpose="用小项目证明能力，而不是只看资料。",
                expectedOutput="可运行的小练习或项目片段。",
                resourcesToUse=resources[:3],
                whyNeeded="项目产出更适合复盘、简历和面试表达。",
            ),
            PlanDesignPhase(
                title="包装与复盘",
                purpose="把学习成果整理成可展示材料，并根据执行反馈修正节奏。",
                expectedOutput="README、简历 bullet 或复盘记录。",
                resourcesToUse=resources[-2:],
                whyNeeded="最终目标不是学完，而是能展示和持续改进。",
            ),
        ]
        if feedback:
            phases[0].purpose = f"根据你的反馈「{feedback[:80]}」调整方向，先降低不匹配部分。"
        return PlanDesignProposal(
            designId=str(uuid4()),
            strategyName="作品集驱动的深度规划" if plan_style == "career_portfolio" else "项目驱动的稳步学习计划",
            targetOutcome=contract.desired_outcome or goal,
            planStyle=plan_style,
            phases=phases,
            designRationale="该方向结合了用户目标、记忆洞察和资源覆盖情况；先确认方向，再拆执行任务。",
            assumptions=contract.missing_information or ["按用户已提供信息推进，后续可继续修订。"],
            userBenefits=[
                "先确认方向，避免直接生成泛泛任务。",
                "任务会绑定学习资源和交付物。",
                "反馈会影响当前计划和未来规划规则。",
            ],
            tradeoffs=["第一版不默认联网搜索，因此外部资源使用静态目录和搜索关键词兜底。"],
            questionsForUser=["这个方向是否符合你的真实目标？", "是否需要降低任务密度或更偏项目实战？"],
            status="waiting_user_approval",
        )

    def build_execution_draft(self, session: PlanningSessionResponse, design: PlanDesignProposal, resource_brief: ResourceBrief) -> ExecutionPlanDraft:
        start = datetime.fromisoformat(_context_date({})).date()
        duration = _duration_days(session.user_input)
        spacing = max(1, duration // max(4, len(design.phases) * 2))
        candidates = resource_brief.resource_candidates or [self._ai_generated_candidate(session.user_input, 0)]
        tasks: list[ExecutionTask] = []
        index = 0
        for phase in design.phases:
            for step in ("学习并复现", "完成一个可检查产出"):
                candidate = candidates[index % len(candidates)]
                practice = next((item for item in candidates if item.source_type in {"practice_bank", "project_template"}), candidate)
                fallback_candidate = self._ai_generated_candidate(session.user_input, index)
                bundle = TaskResourceBundle(
                    primary=self._resource_for_task(candidate, use_step="先按指定 section 或关键词完成最小阅读，不通读整份资料。"),
                    practice=self._resource_for_task(practice, use_step="直接做一个可运行或可提交的小练习。"),
                    fallback=self._resource_for_task(fallback_candidate, use_step="如果主资源看不懂，先按 AI micro material 做最小示例。"),
                )
                coverage_status = resource_brief.coverage.status
                if candidate.source_type == "ai_generated" and coverage_status != "missing":
                    coverage_status = "weak"
                due = (start + timedelta(days=index * spacing)).isoformat()
                tasks.append(
                    ExecutionTask(
                        title=f"{phase.title}：{step}",
                        description=phase.purpose,
                        dueDate=due,
                        estimatedMinutes=30 if "30" in (session.user_need_contract.available_time or "") else (45 if index < 2 else 60),
                        priority="high" if index < 2 else "medium",
                        whyThisTaskMatters=phase.why_needed,
                        acceptanceCriteria=[
                            "完成资源中的最小练习或示例。",
                            "写下 3 条理解或卡点。",
                            "产出物能被自己或他人检查。",
                        ],
                        deliverable=phase.expected_output,
                        fallbackAdjustment="如果做不动，缩小到 20 分钟，只完成主资源的第一个示例并记录卡点。",
                        knowledgePoints=candidate.topics or [candidate.domain or session.user_input[:30]],
                        resourceBundle=bundle,
                        resourceCoverage=ExecutionTaskResourceCoverage(status=coverage_status, explanation=resource_brief.coverage.explanation),
                    )
                )
                index += 1
        return ExecutionPlanDraft(
            designId=design.design_id,
            tasks=tasks,
            reviewCadence="每天结束后记录完成情况和卡点；每周根据反馈调整任务密度。",
            riskPlan=["任务过重时优先减少阅读量，保留可检查产出。", "资源看不懂时切换到 practice/fallback 资源。"],
            scheduleSummary=f"按 {duration} 天规划，先低密度推进，再根据反馈修订。",
            resourceCoverageSummary=resource_brief.coverage.explanation,
            status="waiting_user_approval",
        )

    def build_learning_patch(self, feedback: str, session: PlanningSessionResponse) -> LearningPatch:
        resource_feedback = _contains(feedback, "资源", "资料", "文档", "看不懂", "太理论", "resource", "doc", "theory")
        heavy_feedback = _contains(feedback, "太难", "太多", "任务太重", "没完成", "时间不够", "30 分钟", "30分钟", "heavy", "too much")
        direction_feedback = _contains(feedback, "方向不对", "不是这个意思", "更项目", "更实战", "不想", "wrong direction")
        positive_feedback = _contains(feedback, "适合", "喜欢", "不错", "很好", "fits", "like")
        if resource_feedback:
            feedback_type = "resource_feedback"
            scope = "resource_selection"
            immediate = LearningImmediatePatch(target="resource", action="replace_resource", instruction="把纯理论资源降级为辅助资源，优先推荐项目模板或练习库。")
            rule = "初学或反馈看不懂时，不优先推荐纯理论资源；先给项目示例和小练习。"
        elif heavy_feedback:
            feedback_type = "execution_failure"
            scope = "current_plan"
            immediate = LearningImmediatePatch(target="execution_task", action="reduce_load", instruction="把任务拆小，并将单次任务压到 30 分钟以内。")
            rule = "用户反馈任务过重或时间不足时，后续计划应减少单日任务量，并保留降级版本。"
        elif direction_feedback:
            feedback_type = "negative"
            scope = "planning_style"
            immediate = LearningImmediatePatch(target="design", action="revise_design", instruction="重新调整规划方向，明确保留用户原话中的目标和强约束。")
            rule = "用户指出方向不对时，先回到规划方向确认，不继续扩展执行任务。"
        elif positive_feedback:
            feedback_type = "positive"
            scope = "future_plans"
            immediate = None
            rule = f"未来规划可复用这类体验：{feedback[:120]}"
        else:
            feedback_type = "preference"
            scope = "future_plans"
            immediate = None
            rule = f"未来规划参考用户反馈：{feedback[:120]}"
        return LearningPatch(
            feedbackType=feedback_type,
            affectedScope=scope,
            insight=f"用户反馈：{feedback}",
            reflection=LearningReflection(
                whatWentWrong="资源或任务强度与用户当前状态不匹配。" if resource_feedback or heavy_feedback else None,
                whyItHappened="规划阶段对用户可承受资源难度、任务密度或方向偏好估计不够保守。" if resource_feedback or heavy_feedback or direction_feedback else None,
                howToAvoidNextTime=rule,
            ),
            immediatePatch=immediate,
            longTermLearning=LongTermLearning(
                newRule=rule,
                confidence=0.8 if resource_feedback or heavy_feedback or direction_feedback else 0.65,
                evidence=feedback,
                appliesToDomains=[session.user_need_contract.interpreted_goal if session.user_need_contract else session.user_input],
            ),
            memoryUpdates=[
                LearningMemoryUpdate(kind="preference" if feedback_type in {"preference", "positive", "resource_feedback"} else "review", title="规划反馈规则", content=rule),
                LearningMemoryUpdate(kind="review", title="执行反馈复盘", content=f"反馈：{feedback}\n反思：{rule}"),
            ],
        )

    def apply_learning_patch(self, execution: ExecutionPlanDraft, patch: LearningPatch) -> ExecutionPlanDraft:
        data = execution.model_dump(by_alias=True)
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        if patch.immediate_patch and patch.immediate_patch.action in {"reduce_load", "split_task"}:
            for task in tasks:
                if isinstance(task, dict):
                    task["estimatedMinutes"] = min(int(task.get("estimatedMinutes") or 45), 30)
                    task["fallbackAdjustment"] = "降级为 15-20 分钟，只完成最小可检查产出并记录卡点。"
        if patch.immediate_patch and patch.immediate_patch.action == "replace_resource":
            for task in tasks[:2]:
                if isinstance(task, dict):
                    bundle = task.get("resourceBundle") if isinstance(task.get("resourceBundle"), dict) else {}
                    bundle["primary"] = {
                        "title": "项目示例 + 小练习兜底资源",
                        "sourceType": "practice_bank",
                        "searchKeyword": f"{task.get('title', '')} 入门 练习",
                        "useStep": "先完成一个最小练习，再回头看官方文档。",
                        "estimatedMinutes": 20,
                        "whyThisResource": "用户反馈原资源太难，改为更可执行的小练习。",
                        "expectedOutput": "一个可检查的小练习结果。",
                        "fallbackIfTooHard": "只写下问题和下一步要查的关键词。",
                    }
                    task["resourceBundle"] = bundle
                    coverage = task.get("resourceCoverage") if isinstance(task.get("resourceCoverage"), dict) else {}
                    coverage["status"] = "partial"
                    coverage["explanation"] = "已根据反馈替换为练习库/项目模板优先的资源。"
                    task["resourceCoverage"] = coverage
        if patch.immediate_patch and patch.immediate_patch.action == "revise_design":
            for task in tasks:
                if isinstance(task, dict):
                    task["description"] = f"根据方向反馈重新校准：{task.get('description', '')}"
        data["tasks"] = tasks
        data["status"] = "revision_needed"
        return ExecutionPlanDraft.model_validate(data)

    def execution_to_structured_plan(self, session: PlanningSessionResponse) -> dict[str, Any]:
        if not session.execution_draft:
            raise HTTPException(status_code=409, detail={"message": "execution draft is not available"})
        title = session.user_need_contract.interpreted_goal if session.user_need_contract else _title_from_goal(session.user_input)
        tasks = []
        for index, task in enumerate(session.execution_draft.tasks):
            tasks.append(
                {
                    "title": task.title,
                    "description": "\n".join(
                        [
                            task.description,
                            f"为什么做：{task.why_this_task_matters}",
                            f"产出物：{task.deliverable}",
                            f"完成标准：{'；'.join(task.acceptance_criteria)}",
                            f"做不动时：{task.fallback_adjustment}",
                            f"去哪学：{self._bundle_summary(task.resource_bundle)}",
                        ]
                    ),
                    "estimatedMinutes": task.estimated_minutes,
                    "dueDate": task.due_date,
                    "priority": task.priority,
                    "sourceKey": f"planning-session:{session.session_id}:t{index}",
                    "learningResources": self._legacy_learning_resources(task.resource_bundle),
                    "resourceBundle": task.resource_bundle.model_dump(by_alias=True),
                }
            )
        return {
            "goalTitle": title,
            "goalDescription": session.design_proposal.target_outcome if session.design_proposal else session.user_input,
            "durationDays": max(1, _duration_days(session.user_input)),
            "milestones": [
                {
                    "title": "已确认执行计划",
                    "description": session.execution_draft.schedule_summary,
                    "tasks": tasks,
                }
            ],
            "reviewPlan": {
                "frequency": "daily",
                "questions": ["今天完成了什么？", "资源是否合适？", "任务是否需要降级？"],
            },
        }

    def _resource_candidates(self, contract: UserNeedContract, memory_insight: MemoryInsightBrief) -> list[ResourceCandidate]:
        candidates: list[ResourceCandidate] = []
        for hit in memory_insight.memory_hits.materials[:3]:
            candidates.append(
                self._candidate(
                    f"memory:{hit.id}",
                    hit.title,
                    "user_material",
                    contract.interpreted_goal,
                    [hit.title],
                    "先读摘要并提取一个能立刻执行的小练习。",
                    hit.summary or "用户已保存资料摘要",
                )
            )

        text = f"{contract.raw_user_input} {contract.interpreted_goal}".lower()
        for entry in RESOURCE_CATALOG:
            matches = [str(item).lower() for item in entry.get("match", [])]
            if any(match and match in text for match in matches):
                candidates.append(
                    self._candidate(
                        str(entry["id"]),
                        str(entry["title"]),
                        str(entry["sourceType"]),
                        str(entry["domain"]),
                        [str(topic) for topic in entry.get("topics", [])],
                        str(entry["howToUse"]),
                        str(entry["expectedOutput"]),
                        url=entry.get("url") if isinstance(entry.get("url"), str) else None,
                        section=entry.get("section") if isinstance(entry.get("section"), str) else None,
                        search=entry.get("searchKeyword") if isinstance(entry.get("searchKeyword"), str) else None,
                    )
                )

        if _contains(text, "实习", "作品集", "简历", "面试", "portfolio", "internship", "resume"):
            for entry in RESOURCE_CATALOG:
                if str(entry.get("domain", "")).lower() == "career":
                    candidates.append(
                        self._candidate(
                            str(entry["id"]),
                            str(entry["title"]),
                            str(entry["sourceType"]),
                            str(entry["domain"]),
                            [str(topic) for topic in entry.get("topics", [])],
                            str(entry["howToUse"]),
                            str(entry["expectedOutput"]),
                            search=entry.get("searchKeyword") if isinstance(entry.get("searchKeyword"), str) else None,
                        )
                    )

        if not candidates:
            candidates.append(self._ai_generated_candidate(contract.interpreted_goal, 0))

        deduped: dict[str, ResourceCandidate] = {}
        for item in candidates:
            deduped.setdefault(item.id, item)
        return list(deduped.values())[:8]

    def _candidate(
        self,
        candidate_id: str,
        title: str,
        source_type: str,
        domain: str,
        topics: list[str],
        how_to_use: str,
        expected_output: str,
        *,
        url: str | None = None,
        section: str | None = None,
        search: str | None = None,
    ) -> ResourceCandidate:
        return ResourceCandidate(
            id=candidate_id,
            title=title,
            sourceType=source_type,
            url=url,
            section=section,
            searchKeyword=search,
            domain=domain,
            topics=topics,
            difficulty="beginner",
            language="mixed",
            estimatedMinutes=25,
            howToUse=how_to_use,
            expectedOutput=expected_output,
            fallbackIfTooHard="缩小到第一个示例或改用搜索关键词找入门练习。",
            fitScore=ResourceFitScore(
                total=82,
                levelFit=80,
                taskFit=84,
                userPreferenceFit=80,
                timeFit=78,
                credibility=82,
                actionability=86,
                reasons=["能直接产生可检查的练习结果。"],
                risks=["如果基础不足，先使用 fallback 资源。"],
            ),
        )

    def _ai_generated_candidate(self, goal: str, index: int) -> ResourceCandidate:
        topic = _resource_topic_from_goal(goal)
        return self._candidate(
            f"ai-micro:{index}",
            f"{topic} 入门微练习",
            "ai_generated",
            topic,
            [topic],
            "使用 AI 生成的最小解释和示例，先完成一个可检查动作。",
            "一段笔记和一个最小练习结果。",
            search=f"{topic} 入门 练习",
        )

    def _resource_for_task(self, candidate: ResourceCandidate, *, use_step: str) -> TaskLearningResource:
        return TaskLearningResource(
            title=candidate.title,
            sourceType=candidate.source_type,
            url=candidate.url,
            section=candidate.section,
            searchKeyword=candidate.search_keyword,
            useStep=use_step or candidate.how_to_use,
            estimatedMinutes=min(candidate.estimated_minutes, 45),
            whyThisResource=candidate.fit_score.reasons[0] if candidate.fit_score.reasons else candidate.how_to_use,
            expectedOutput=candidate.expected_output,
            fallbackIfTooHard=candidate.fallback_if_too_hard,
        )

    def _domain_candidate(
        self,
        candidate_id: str,
        title: str,
        source_type: str,
        topics: list[str],
        use_step: str,
        expected_output: str,
        *,
        domain: str = "Python",
        url: str | None = None,
        section: str | None = None,
        search: str | None = None,
    ) -> ResourceCandidate:
        return self._candidate(
            candidate_id,
            title,
            source_type,
            domain,
            topics,
            use_step,
            expected_output,
            url=url,
            section=section,
            search=search,
        )

    def _matching_candidate(
        self,
        candidates: list[ResourceCandidate],
        keywords: tuple[str, ...],
        fallback: ResourceCandidate,
    ) -> ResourceCandidate:
        lowered = [(item, f"{item.title} {' '.join(item.topics)} {item.domain}".lower()) for item in candidates]
        for item, haystack in lowered:
            if any(keyword.lower() in haystack for keyword in keywords):
                return item
        return fallback

    def _task_bundle(
        self,
        primary: ResourceCandidate,
        practice: ResourceCandidate | None,
        fallback: ResourceCandidate,
        *,
        use_step: str,
    ) -> TaskResourceBundle:
        return TaskResourceBundle(
            primary=self._resource_for_task(primary, use_step=use_step),
            practice=self._resource_for_task(practice, use_step="Do the smallest runnable exercise and keep the output checkable.") if practice else None,
            fallback=self._resource_for_task(fallback, use_step="If stuck, use this smaller fallback and record the blocker."),
        )

    def _python_internship_candidates(self, resource_brief: ResourceBrief) -> dict[str, ResourceCandidate]:
        existing = list(resource_brief.resource_candidates)
        control = self._matching_candidate(
            existing,
            ("control", "if", "loop", "function"),
            self._domain_candidate(
                "official:python:control-flow",
                "Python Tutorial - Control Flow",
                "official_doc",
                ["control flow", "functions", "loops"],
                "Read only the if/for/function examples, then code the matching exercise.",
                "A runnable Python script using if, loops, and a function.",
                url="https://docs.python.org/3/tutorial/controlflow.html",
                section="Control Flow Tools",
            ),
        )
        data_structures = self._domain_candidate(
            "official:python:data-structures",
            "Python Tutorial - Data Structures",
            "official_doc",
            ["list", "dict", "set", "tuple"],
            "Read the list and dict examples, then model a small data record.",
            "A runnable script using list and dict operations.",
            url="https://docs.python.org/3/tutorial/datastructures.html",
            section="Data Structures",
        )
        files_json = self._domain_candidate(
            "practice:python:files-json",
            "Python file and JSON mini practice",
            "practice_bank",
            ["file io", "json", "persistence"],
            "Write one script that saves and loads structured data from a JSON file.",
            "A JSON-backed Python script.",
            search="Python JSON file read write beginner exercise",
        )
        cli_project = self._domain_candidate(
            "project:python:todo-cli",
            "Python Todo CLI project template",
            "project_template",
            ["cli", "todo", "project"],
            "Build a tiny Todo command-line tool with add/list/done actions.",
            "todo_cli.py with a README usage example.",
            search="Python todo CLI beginner project argparse JSON",
        )
        readme = self._domain_candidate(
            "practice:project:readme",
            "README project packaging checklist",
            "practice_bank",
            ["README", "GitHub", "project packaging"],
            "Write install, usage, demo output, and next-step sections for the project.",
            "README.md that a recruiter can scan.",
            search="software project README checklist beginner",
        )
        resume = self._matching_candidate(
            existing,
            ("resume", "interview", "portfolio", "project packaging"),
            self._domain_candidate(
                "practice:career:star",
                "STAR resume bullet rewrite practice",
                "practice_bank",
                ["resume", "interview", "project packaging"],
                "Rewrite project work into three result-oriented bullets and one interview story.",
                "Three resume bullets and one interview answer outline.",
                domain="career",
                search="STAR resume bullet project rewrite examples",
            ),
        )
        interview = self._domain_candidate(
            "practice:python:interview",
            "Python internship interview drill",
            "practice_bank",
            ["interview", "Python basics", "project explanation"],
            "Answer six beginner Python questions and explain the Todo CLI project in three minutes.",
            "Six Q&A notes and a three-minute project pitch.",
            domain="career",
            search="Python internship interview questions beginner project explanation",
        )
        api = self._domain_candidate(
            "practice:python:http-api",
            "Python public API JSON practice",
            "practice_bank",
            ["requests", "api", "json"],
            "Call one public API, parse the JSON response, and save the useful fields.",
            "api_fetch.py plus saved JSON sample.",
            search="Python requests public API JSON beginner exercise",
        )
        return {
            "control": control,
            "data": data_structures,
            "files": files_json,
            "cli": cli_project,
            "readme": readme,
            "resume": resume,
            "interview": interview,
            "api": api,
        }

    def _build_python_internship_execution_draft(
        self,
        session: PlanningSessionResponse,
        design: PlanDesignProposal,
        resource_brief: ResourceBrief,
    ) -> ExecutionPlanDraft:
        start = datetime.fromisoformat(_context_date({})).date()
        duration = max(21, _duration_days(session.user_input))
        daily_minutes = _daily_available_minutes(session.user_need_contract) or 90
        task_minutes = max(75, min(150, daily_minutes))
        resources = self._python_internship_candidates(resource_brief)
        tasks_spec = [
            ("Set up Python and submit hello_python.py", "hello_python.py", resources["control"], resources["control"], ["python setup", "print", "terminal"]),
            ("Finish if/for/function drills and commit control_flow_practice.py", "control_flow_practice.py", resources["control"], resources["control"], ["control flow", "functions", "loops"]),
            ("Build list/dict practice scripts for internship basics", "data_structures_practice.py", resources["data"], resources["data"], ["list", "dict", "data structures"]),
            ("Write a notes parser that reads and writes text files", "file_io_notes.py", resources["files"], resources["files"], ["file io", "strings", "error handling"]),
            ("Save Todo data to JSON and reload it from disk", "todo_json_store.py", resources["files"], resources["cli"], ["json", "persistence", "data model"]),
            ("Implement a Todo CLI with add/list commands", "todo_cli.py", resources["cli"], resources["cli"], ["cli", "project", "functions"]),
            ("Add done/delete commands and basic error handling to Todo CLI", "todo_cli_v2.py", resources["cli"], resources["control"], ["error handling", "cli", "project iteration"]),
            ("Call a public API and save a JSON sample for portfolio evidence", "api_fetch.py and api_sample.json", resources["api"], resources["files"], ["requests", "api", "json"]),
            ("Refactor the project into clear files and add a usage demo", "python_todo_cli/ package with demo output", resources["cli"], resources["readme"], ["project structure", "demo", "packaging"]),
            ("Write README.md with install, usage, screenshots, and next steps", "README.md", resources["readme"], resources["cli"], ["README", "GitHub", "documentation"]),
            ("Prepare a GitHub repository checklist for the Python mini project", "GitHub repo checklist and commit plan", resources["readme"], resources["resume"], ["GitHub", "version control", "portfolio"]),
            ("Write three internship resume bullets from the project", "three resume bullets", resources["resume"], resources["readme"], ["resume", "STAR", "internship"]),
            ("Prepare six Python internship interview Q&A notes", "python_interview_qa.md", resources["interview"], resources["control"], ["interview", "Python basics", "project explanation"]),
            ("Record a three-minute project walkthrough script", "project_pitch.md", resources["interview"], resources["resume"], ["communication", "interview", "portfolio"]),
            ("Do a final review and produce an internship-ready action list", "internship_ready_review.md", resources["resume"], resources["interview"], ["review", "next actions", "applications"]),
        ]
        if duration < 28:
            tasks_spec = tasks_spec[:10]
        spacing = max(1, duration // max(1, len(tasks_spec) - 1))
        tasks: list[ExecutionTask] = []
        for index, (title, deliverable, primary, practice, knowledge) in enumerate(tasks_spec):
            due = (start + timedelta(days=min(duration - 1, index * spacing))).isoformat()
            fallback = self._ai_generated_candidate(f"Python internship task {index + 1}", index)
            bundle = self._task_bundle(
                primary,
                practice,
                fallback,
                use_step="Use the named section or search keyword first, then produce the deliverable before reading more theory.",
            )
            tasks.append(
                ExecutionTask(
                    title=title,
                    description="A concrete Python internship-prep step with a checkable artifact.",
                    dueDate=due,
                    estimatedMinutes=task_minutes if index < 10 else max(60, min(task_minutes, 120)),
                    priority="high" if index < 8 else "medium",
                    whyThisTaskMatters="It turns learning into portfolio or interview evidence instead of passive reading.",
                    acceptanceCriteria=[
                        f"{deliverable} exists and matches the task title.",
                        "The output can be checked by running a command, reading the file, or reviewing the written answer.",
                        "One blocker or next improvement is recorded for follow-up.",
                    ],
                    deliverable=deliverable,
                    fallbackAdjustment="If stuck, reduce to a 30-minute version: finish the smallest runnable example and record the exact blocker.",
                    knowledgePoints=knowledge,
                    resourceBundle=bundle,
                    resourceCoverage=ExecutionTaskResourceCoverage(
                        status="partial" if resource_brief.coverage.status in {"weak", "missing"} else resource_brief.coverage.status,
                        explanation="Resources are matched to each concrete Python internship task using docs, practice, project, README, resume, and interview assets.",
                    ),
                )
            )
        draft = ExecutionPlanDraft(
            designId=design.design_id,
            tasks=tasks,
            reviewCadence="Review progress every 3 days: keep runnable code, update README notes, and trim tasks that exceed the available daily time.",
            riskPlan=[
                "If basics feel hard, keep the project scope but shrink each step to one runnable script.",
                "If internship packaging feels early, still keep README/resume/interview tasks as lightweight drafts.",
            ],
            scheduleSummary=f"{duration} days with {len(tasks)} concrete Python internship tasks, paced around the user's available time.",
            resourceCoverageSummary="Uses Python docs, practice bank, project template, README/GitHub packaging, resume bullets, and interview drills instead of repeating one resource.",
            status="waiting_user_approval",
        )
        return self._attach_quality_report(draft, session)

    def _build_general_execution_draft(
        self,
        session: PlanningSessionResponse,
        design: PlanDesignProposal,
        resource_brief: ResourceBrief,
    ) -> ExecutionPlanDraft:
        start = datetime.fromisoformat(_context_date({})).date()
        duration = _duration_days(session.user_input)
        candidates = resource_brief.resource_candidates or [self._ai_generated_candidate(session.user_input, 0)]
        task_count = max(8, min(14, duration // 3 if duration >= 21 else len(design.phases) * 3))
        tasks: list[ExecutionTask] = []
        for index in range(task_count):
            phase = design.phases[min(len(design.phases) - 1, index * len(design.phases) // task_count)]
            candidate = candidates[index % len(candidates)]
            practice = next((item for item in candidates[index:] + candidates[:index] if item.source_type in {"practice_bank", "project_template"}), candidate)
            fallback = self._ai_generated_candidate(f"{phase.title} task {index + 1}", index)
            due = (start + timedelta(days=min(duration - 1, index * max(1, duration // max(task_count - 1, 1))))).isoformat()
            deliverable = f"{_resource_topic_from_goal(phase.expected_output)} artifact {index + 1}"
            tasks.append(
                ExecutionTask(
                    title=f"{phase.title}: produce artifact {index + 1} with {candidate.domain or 'resource'} practice",
                    description=phase.purpose,
                    dueDate=due,
                    estimatedMinutes=max(45, min(120, _daily_available_minutes(session.user_need_contract) or 60)),
                    priority="high" if index < 3 else "medium",
                    whyThisTaskMatters=phase.why_needed,
                    acceptanceCriteria=[
                        "Finish the named small practice or reading section.",
                        "Create a checkable output file, note, script, or answer.",
                        "Record one follow-up improvement.",
                    ],
                    deliverable=deliverable,
                    fallbackAdjustment="If stuck, reduce to one example or one written answer and log the blocker.",
                    knowledgePoints=candidate.topics or [candidate.domain or _resource_topic_from_goal(session.user_input)],
                    resourceBundle=self._task_bundle(
                        candidate,
                        practice,
                        fallback,
                        use_step="Use only the relevant section or search keyword, then produce the deliverable.",
                    ),
                    resourceCoverage=ExecutionTaskResourceCoverage(
                        status="partial" if resource_brief.coverage.status == "weak" else resource_brief.coverage.status,
                        explanation=resource_brief.coverage.explanation,
                    ),
                )
            )
        draft = ExecutionPlanDraft(
            designId=design.design_id,
            tasks=tasks,
            reviewCadence="Review every few days and keep each task tied to a checkable output.",
            riskPlan=[
                "If a task is too broad, reduce it to one concrete output.",
                "If resources are hard, switch to the practice or fallback resource first.",
            ],
            scheduleSummary=f"{duration} days with {len(tasks)} concrete execution tasks.",
            resourceCoverageSummary=resource_brief.coverage.explanation,
            status="waiting_user_approval",
        )
        return self._attach_quality_report(draft, session)

    def build_execution_draft(self, session: PlanningSessionResponse, design: PlanDesignProposal, resource_brief: ResourceBrief) -> ExecutionPlanDraft:
        subject = _learning_plan_subject(session.user_need_contract, session.user_input).lower()
        if "python" in subject and _is_internship_learning_plan(session.user_need_contract, session.user_input):
            return self._build_python_internship_execution_draft(session, design, resource_brief)
        return self._build_general_execution_draft(session, design, resource_brief)

    def _attach_quality_report(self, draft: ExecutionPlanDraft, session: PlanningSessionResponse) -> ExecutionPlanDraft:
        report = self._execution_quality_report(draft, session)
        data = draft.model_dump(by_alias=True)
        data["qualityReport"] = report.model_dump(by_alias=True)
        data["qualityStatus"] = report.status
        return ExecutionPlanDraft.model_validate(data)

    def _execution_quality_report(self, draft: ExecutionPlanDraft, session: PlanningSessionResponse) -> ExecutionPlanQualityReport:
        contract = session.user_need_contract
        duration = max(1, _duration_days(session.user_input))
        daily_minutes = _daily_available_minutes(contract)
        total_minutes = sum(task.estimated_minutes for task in draft.tasks)
        internship = _is_internship_learning_plan(contract, session.user_input)
        task_text = "\n".join(_task_text(task) for task in draft.tasks).lower()
        min_tasks = 12 if internship and duration >= 28 else (8 if duration >= 21 else 4)
        if daily_minutes and daily_minutes >= 120 and duration >= 28:
            min_minutes = min(1800, max(900, int(daily_minutes * duration * 0.25)))
        else:
            min_minutes = min_tasks * 45
        generic_titles = [task.title for task in draft.tasks if _generic_task_title(task.title)]
        concrete_deliverables = [task for task in draft.tasks if _has_concrete_deliverable(task)]
        primary_titles = [_primary_resource_title(task) for task in draft.tasks if _primary_resource_title(task)]
        most_common_primary = max((primary_titles.count(title) for title in set(primary_titles)), default=0)
        resource_diverse = not primary_titles or most_common_primary <= max(2, len(draft.tasks) // 2)
        internship_terms = ("readme", "github", "resume", "bullet", "interview", "\u7b80\u5386", "\u9762\u8bd5", "\u4f5c\u54c1", "\u9879\u76ee")
        internship_fit = (not internship) or sum(1 for term in internship_terms if term in task_text) >= 3
        checks = {
            "goalAlignment": bool(draft.tasks and _learning_plan_subject(contract, session.user_input).lower() in task_text),
            "timeFit": len(draft.tasks) >= min_tasks and total_minutes >= min_minutes,
            "taskSpecificity": not generic_titles,
            "resourceDiversity": resource_diverse,
            "deliverableQuality": len(concrete_deliverables) >= max(1, int(len(draft.tasks) * 0.8)),
            "internshipFit": internship_fit,
        }
        blockers: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        if not checks["goalAlignment"]:
            blockers.append("Execution tasks do not clearly align with the user's learning goal.")
            suggestions.append("Rewrite task titles and deliverables around the requested subject.")
        if not checks["timeFit"]:
            blockers.append("The plan is too sparse for the user's time horizon and availability.")
            suggestions.append("Increase task count and total planned minutes, or ask the user to confirm a lighter plan.")
        if not checks["taskSpecificity"]:
            blockers.append("Some task titles are generic and not directly actionable.")
            suggestions.append("Replace generic phase labels with concrete action + topic + output titles.")
        if not checks["resourceDiversity"]:
            blockers.append("More than half of the tasks reuse the same primary resource.")
            suggestions.append("Match resources to the task topic: basics, project, README, resume, interview, or API as needed.")
        if not checks["deliverableQuality"]:
            blockers.append("Too many tasks lack concrete deliverables.")
            suggestions.append("Each task should produce a file, repo update, README section, resume bullet, or interview answer.")
        if not internship_fit:
            blockers.append("The internship/job outcome is not reflected in project packaging, resume, GitHub, or interview tasks.")
            suggestions.append("Add portfolio packaging, README/GitHub, resume bullet, and interview preparation tasks.")
        if primary_titles and most_common_primary > 1:
            warnings.append(f"Most repeated primary resource appears {most_common_primary} times.")
        score = 100
        score -= len(blockers) * 12
        score -= max(0, min_tasks - len(draft.tasks)) * 2
        score = max(0, min(100, score))
        calendar_writable = not blockers and all(task.resource_coverage.status != "missing" for task in draft.tasks)
        status = "passed" if calendar_writable and score >= 80 else ("blocked" if len(blockers) >= 4 else "needs_repair")
        return ExecutionPlanQualityReport(
            status=status,
            score=score,
            blockers=blockers,
            warnings=warnings,
            repairSuggestions=suggestions,
            checks=ExecutionPlanQualityChecks(
                goalAlignment=checks["goalAlignment"],
                timeFit=checks["timeFit"],
                taskSpecificity=checks["taskSpecificity"],
                resourceDiversity=checks["resourceDiversity"],
                deliverableQuality=checks["deliverableQuality"],
                internshipFit=internship_fit if internship else None,
                calendarWritable=calendar_writable,
            ),
        )

    def _bundle_summary(self, bundle: TaskResourceBundle) -> str:
        names = [item.title for item in (bundle.primary, bundle.support, bundle.practice, bundle.fallback) if item]
        return " / ".join(names)

    def _legacy_learning_resources(self, bundle: TaskResourceBundle) -> list[dict[str, Any]]:
        values = []
        for item in (bundle.primary, bundle.support, bundle.practice, bundle.fallback):
            if not item:
                continue
            values.append(
                {
                    "title": item.title,
                    "type": "official_doc" if item.source_type == "official_doc" else "search_keyword",
                    "url": item.url,
                    "searchKeyword": item.search_keyword,
                    "reason": item.why_this_resource,
                }
            )
        return values

    def _update_session(self, session_id: str, *, status: PlanningSessionStatus | None = None, **fields: Any) -> None:
        column_map = {
            "userNeedContract": "user_need_contract_json",
            "slotState": "slot_state_json",
            "pendingQuestion": "pending_question_json",
            "memoryInsight": "memory_insight_json",
            "resourceBrief": "resource_brief_json",
            "designProposal": "design_proposal_json",
            "executionDraft": "execution_draft_json",
            "learningPatch": "latest_learning_patch_json",
        }
        assignments: list[str] = []
        params: list[Any] = []
        if status:
            assignments.append("status = ?")
            params.append(status)
        for key, column in column_map.items():
            if key not in fields:
                continue
            value = fields[key]
            assignments.append(f"{column} = ?")
            params.append(_dump(value.model_dump(by_alias=True) if value is not None else {}))
        assignments.extend(["version = version + 1", "updated_at = ?"])
        params.append(_now())
        params.append(session_id)
        with get_conn() as conn:
            conn.execute(f"UPDATE planning_sessions SET {', '.join(assignments)} WHERE id = ?", params)

    def _row_to_session(self, row) -> PlanningSessionResponse:
        contract = _json_object(row["user_need_contract_json"])
        slot = _json_object(row["slot_state_json"]) if "slot_state_json" in row.keys() else {}
        pending = _json_object(row["pending_question_json"]) if "pending_question_json" in row.keys() else {}
        memory = _json_object(row["memory_insight_json"])
        resources = _json_object(row["resource_brief_json"])
        design = _json_object(row["design_proposal_json"])
        execution = _json_object(row["execution_draft_json"])
        learning = _json_object(row["latest_learning_patch_json"])
        return PlanningSessionResponse(
            sessionId=row["id"],
            threadId=row["thread_id"],
            entryPoint=row["entry_point"],
            status=row["status"],
            userInput=row["user_input"],
            userNeedContract=UserNeedContract.model_validate(contract) if contract else None,
            slotState=PlanningSlotState.model_validate(slot) if slot else None,
            pendingQuestion=PendingPlanningQuestion.model_validate(pending) if pending else None,
            memoryInsight=MemoryInsightBrief.model_validate(memory) if memory else None,
            resourceBrief=ResourceBrief.model_validate(resources) if resources else None,
            designProposal=PlanDesignProposal.model_validate(design) if design else None,
            executionDraft=ExecutionPlanDraft.model_validate(execution) if execution else None,
            learningPatch=LearningPatch.model_validate(learning) if learning else None,
            artifacts=self.agent_runtime.list_artifacts(row["id"]),
            decisions=self.agent_runtime.list_decisions(row["id"]),
            messages=self.agent_runtime.list_messages(row["id"]),
            version=int(row["version"] or 1),
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )
