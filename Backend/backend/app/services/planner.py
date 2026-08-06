import json
from typing import Any

from ..db import load_memory, save_event
from ..schemas import AiPayload
from .llm import LlmClient
from .memory_store import MemoryService
from .tools import list_tools


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _normalize_tasks(data: dict[str, Any]) -> list[dict[str, str]]:
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return []
    normalized = []
    for item in tasks[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("text") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "time": str(item.get("time") or "09:00")[:5],
                "title": title,
                "reason": str(item.get("reason") or "This task supports the current goal."),
            }
        )
    return normalized


def _load_preference_memory() -> str:
    item = MemoryService().get_by_source_key("preference", "preferences:local-user")
    if item and item.content.strip():
        return item.content
    return load_memory()


class PlannerAgent:
    def plan(self, payload: AiPayload) -> dict[str, object]:
        preferences = payload.preferences or _load_preference_memory()
        save_event("plan_request", payload.model_dump_json(by_alias=True))
        llm_result, _ = LlmClient().complete(
            "planner_plan",
            (
                "You are an AI study and internship planning agent. Return only valid JSON, no markdown. "
                'Required shape: {"summary":"...","phases":[{"title":"...","detail":"..."}],'
                '"tasks":[{"time":"HH:MM","title":"...","reason":"..."}]}. '
                "Use exactly 3 phases and exactly 3 tasks. Do not nest tasks inside phases."
            ),
            json.dumps(
                {
                    "goal": payload.goal,
                    "deadline": payload.deadline,
                    "dailyHours": payload.daily_hours,
                    "materials": payload.materials[:3000],
                    "preferences": preferences,
                    "date": payload.date,
                },
                ensure_ascii=False,
            ),
            max_tokens=2400,
            temperature=0.2,
            task_type="plan_generation",
        )
        if llm_result:
            parsed = _json_object(llm_result.content)
            if parsed:
                parsed["mode"] = "llm"
                parsed["provider"] = llm_result.provider
                parsed["model"] = llm_result.model
                parsed["tasks"] = _normalize_tasks(parsed)
                parsed.setdefault("tools", list_tools())
                return parsed
        return self._mock_plan(payload, preferences)

    def review(self, payload: AiPayload) -> dict[str, object]:
        save_event("review_request", payload.model_dump_json(by_alias=True))
        day = payload.data.get(payload.date, {}) if payload.data else {}
        plans = day.get("plans", [])
        done = len([plan for plan in plans if plan.get("done")])
        llm_result, _ = LlmClient().complete(
            "planner_review",
            (
                "You are an AI daily review assistant. Return only valid JSON, no markdown. "
                'Required shape: {"summary":"...","suggestions":["..."]}. '
                "Use at most 4 short suggestions."
            ),
            json.dumps(
                {
                    "date": payload.date,
                    "plans": plans,
                    "doneCount": done,
                    "totalCount": len(plans),
                    "preferences": payload.preferences or _load_preference_memory(),
                },
                ensure_ascii=False,
            ),
            max_tokens=1200,
            temperature=0.2,
            task_type="chat",
        )
        if llm_result:
            parsed = _json_object(llm_result.content)
            if parsed:
                parsed["mode"] = "llm"
                parsed["provider"] = llm_result.provider
                parsed["model"] = llm_result.model
                return parsed
        return {
            "mode": "mock",
            "summary": f"今天完成 {done}/{len(plans)} 项。",
            "suggestions": [
                "把未完成任务拆成更小的 30-45 分钟块。",
                "保留一个能证明进度的产出，例如 commit、截图或笔记。",
                "把复盘结果写入明天的第一项任务，减少重新启动成本。",
            ],
        }

    def _mock_plan(self, payload: AiPayload, preferences: str) -> dict[str, object]:
        goal = payload.goal or "AI application internship"
        return {
            "mode": "mock",
            "summary": f"已围绕“{goal}”生成阶段计划，每天投入 {payload.daily_hours:g} 小时。",
            "phases": [
                {"title": "阶段 1：岗位能力对齐", "detail": "拆解目标岗位 JD 高频技能，建立学习清单和刷题节奏。"},
                {"title": "阶段 2：AI 应用项目冲刺", "detail": "完成 RAG、Agent、Memory、Eval 和部署闭环。"},
                {"title": "阶段 3：投递与复盘", "detail": "把项目讲法、简历 bullet 和面试题库联动优化。"},
            ],
            "tasks": [
                {"time": "09:00", "title": "阅读目标岗位 JD 并提取 5 个关键词", "reason": "让当天任务和真实岗位要求对齐。"},
                {"time": "14:30", "title": "实现或优化一个 AI 应用功能", "reason": "每天保留可展示的工程产出。"},
                {"time": "20:30", "title": "记录完成情况并生成明日调整", "reason": "形成动态复盘闭环。"},
            ],
            "preferencesUsed": preferences,
            "toolCalls": [
                {"name": "search_materials", "arguments": {"query": goal, "top_k": 3}},
                {"name": "get_today_plans", "arguments": {"date": payload.date}},
                {"name": "propose_tasks", "arguments": {"goal": goal, "date": payload.date}},
            ],
            "tools": list_tools(),
        }
