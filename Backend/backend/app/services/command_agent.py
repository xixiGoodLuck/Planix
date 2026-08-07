from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, date as date_type, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException

from ..db import get_conn
from ..schemas import (
    AgentRunRequest,
    CommandApproveRequest,
    CommandChatRequest,
    CommandDecision,
    CommandDraftOut,
    CommandMessageOut,
    CommandPermission,
    CommandThreadSummaryOut,
    CreatePlanningSessionRequest,
    GoalUnderstandingResult,
    MemoryCreate,
    ModelUsage,
    MonthNotePut,
    PlanCreate,
    PlanningSessionResponse,
    PlanningSessionTextRequest,
    PlanRefinedTaskUpdate,
    PlanUpdate,
    RefinedTask,
    RefineTaskRequest,
)
from .command_decision import CommandDecisionResult, CommandDecisionService, local_fallback_usage, usage_from_llm_result
from ..cognitive_planning.control_intent import detect_planning_control_intent
from .goal_understanding import GoalUnderstandingOutcome, GoalUnderstandingService
from ..cognitive_planning import get_planning_orchestrator
from .llm import LlmClient
from .memory_agent import MemoryAgentService, detect_query_kinds
from .memory_store import MemoryService
from .month_notes import get_month_note, upsert_month_note
from .permission_gate import command_action_requires_approval
from .plans import create_plan, delete_plan, save_plan_refined_task, update_plan
from .planning import PlanningService, build_refine_plan_context
from .rag import RagService
from .runtime import RuntimeOrchestrator

CommandIntent = Literal[
    "normal_chat",
    "goal_understanding_unavailable",
    "planning_request",
    "regenerate_draft",
    "modify_current_draft",
    "show_current_plan",
    "sync_to_calendar",
    "refine_current_plan",
    "query_plan",
    "query_memory",
    "query_notes",
    "patch_calendar_plan",
    "save_memory",
    "save_note",
    "clarify",
    "navigate_ui",
    "unsupported_command",
]

KEY_RUNTIME_TOOLS = {
    "get_memory",
    "get_today_plans",
    "search_materials",
    "enrich_with_model_knowledge",
    "propose_tasks",
}

CALENDAR_WRITE_ERROR_MESSAGE = "写入日历失败，请检查后端服务或计划数据。"
DRAFT_SAVE_ERROR_MESSAGE = "计划草稿保存失败，请重启后端或检查数据库迁移。"
COMMAND_STREAM_ERROR_MESSAGE = "Command 执行流中断，请重启后端服务后重试。"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now().date().isoformat()


def _ndjson(event: dict[str, Any]) -> str:
    return f"{json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n"


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_message(row) -> CommandMessageOut:
    return CommandMessageOut(
        id=row["id"],
        threadId=row["thread_id"],
        role=row["role"],
        content=row["content"],
        kind=row["kind"] if "kind" in row.keys() else "text",
        payload=_json_object(row["payload_json"] if "payload_json" in row.keys() else "{}"),
        createdAt=row["created_at"],
    )


def _row_to_draft(row) -> CommandDraftOut | None:
    if not row:
        return None
    return CommandDraftOut(
        id=row["id"],
        threadId=row["thread_id"],
        kind=row["kind"],
        version=int(row["version"] or 1),
        status=row["status"],
        title=row["title"],
        summary=row["summary"],
        payload=_json_object(row["payload_json"]),
        sourceRunId=row["source_run_id"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _looks_english(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    ascii_letters = [char for char in letters if ord(char) < 128]
    return len(ascii_letters) / max(1, len(letters)) > 0.8


def _looks_like_learning_plan_request(text: str) -> bool:
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    if re.search(
        r"(查看|查询|搜索|找|修改|改到|改成|移动|挪到|删除|取消|写入|保存|记录|记一下|记住|第\s*\d+\s*个|今天|明天|后天|本周|下周|周[一二三四五六日天]|change|move|delete|reschedule)",
        cleaned,
    ):
        return False
    learning_signal = re.search(
        r"(我要学|我想学|想学|学习|学会|掌握|精通|入门|进阶|帮我学|帮我规划|做个规划|学习计划|学习路线|roadmap|learn|master)",
        cleaned,
        re.I,
    )
    if not learning_signal:
        return False
    return bool(re.search(r"[a-z][a-z0-9+#.-]*|[\u4e00-\u9fff]{2,}", cleaned, re.I))


def _looks_like_goal_statement(text: str) -> bool:
    """Route goal-shaped language to planning without encoding domain templates."""
    cleaned = text.strip().lower()
    if not cleaned or re.search(r"(查看|查询|搜索|删除|修改|写入|保存|记录|什么计划|有哪些计划|query|search|delete|save)", cleaned):
        return False
    if re.search(r"(我要|我想|打算|准备|希望|目标是|计划在|i want to|i plan to|my goal is)", cleaned, re.I):
        return True
    constraint_patterns = (
        r"\d+\s*(?:天|周|个月|月|days?|weeks?|months?)",
        r"(?:每天|每周|daily|weekly)\s*\d+(?:\.\d+)?\s*(?:小时|分钟|hours?|minutes?)",
        r"\d+(?:\.\d+)?\s*(?:万|千)?\s*(?:元|人民币|rmb|cny)",
        r"(?:20\d{2}[年\-/])?\d{1,2}月",
        r"(?:截止|之前|以内|deadline|before|within)",
    )
    constraint_count = sum(bool(re.search(pattern, cleaned, re.I)) for pattern in constraint_patterns)
    return constraint_count >= 2 and bool(re.search(r"[a-z][a-z0-9+#.-]*|[\u4e00-\u9fff]{2,}", cleaned, re.I))


def detect_command_intent(message: str) -> CommandIntent:
    text = message.strip().lower()
    if not text:
        return "normal_chat"
    if _looks_like_learning_plan_request(text) or _looks_like_goal_statement(text):
        return "planning_request"
    if re.search(r"(记住|偏好|适合|不喜欢|只能|希望以后|记一下|记录一下|保存一下|保存成笔记|保存为笔记|记录一条记忆|记录记忆|save memory|remember)", text):
        return "save_memory"
    if re.search(r"(查一下|查询|搜索|找|看看).{0,16}(记忆|个人记录|笔记|资料|材料|文档|历史规划|规划档案|偏好|复盘|memory|note|material|document)", text):
        return "query_memory"
    if re.search(r"(保存|存下|记下|save).{0,12}(笔记|note)", text) or re.search(r"(笔记|note).{0,12}(保存|存下|save)", text):
        return "save_note"

    if re.search(r"(确认写入|confirm write|confirm .{0,16}write)", text):
        return "sync_to_calendar"
    if re.search(
        r"(写进|写入|加入|同步|保存).{0,12}(日历|日程|calendar|schedule)",
        text,
    ) or re.search(
        r"write .{0,24}calendar|sync .{0,24}calendar|save .{0,24}(calendar|schedule)|confirm .{0,16}write",
        text,
    ):
        return "sync_to_calendar"
    if re.search(
        r"(写进|写入|同步|保存).{0,12}(计划|规划|草稿|plan|draft)",
        text,
    ) or re.search(
        r"(计划|规划|草稿|this plan|current plan|draft).{0,12}(写进|写入|同步|保存|write|sync|save)",
        text,
    ) or re.search(
        r"(write|sync|save) .{0,16}(this|current|the) .{0,8}(plan|draft)",
        text,
    ):
        return "sync_to_calendar"
    if re.search(r"(细化|細化|拆细|一键细化|refine|refinement).{0,16}(任务|计划|全部|all|task|plan)?", text):
        return "refine_current_plan"
    if re.search(r"(重新生成|再生成|换个版本|另一个版本|更轻松|更激进|regenerate|another version)", text):
        return "regenerate_draft"
    if re.search(r"(展开|完整计划|查看计划|计划详情|show.*plan|full.*plan|detail)", text):
        return "show_current_plan"
    if re.search(r"(查|找|搜索|看看|query|search|find).{0,16}(笔记|资料|材料|note|notes|material|materials)", text):
        return "query_notes"
    if _looks_like_query(text):
        return "query_plan"
    if _looks_like_calendar_patch(text):
        return "patch_calendar_plan"
    if re.search(r"(减少|增加|修改|调整|压缩|保留|删除|改成|revise|modify|adjust|reduce|compress)", text):
        return "modify_current_draft"
    if re.search(
        r"(规划|计划|安排|制定|拆解|帮我学|我要学|准备|路线|日程|\bplan\b|\bplanning\b|\bschedule\b|\broadmap\b|break down)",
        text,
    ):
        return "planning_request"
    if re.search(r"(打开|跳转|进入|去到|navigate|open|go to)", text):
        return "navigate_ui"
    if re.search(r"(执行|删除|清空|设置|保存|execute|delete|clear|save|setting)", text):
        return "unsupported_command"
    return "normal_chat"


def _looks_like_current_draft_write(message: str) -> bool:
    text = message.strip().lower()
    if not text:
        return False
    if re.fullmatch(
        r"(写入|写进|保存|同步|确认|确认写入|执行写入|写入计划|保存计划|同步计划|write|save|sync|confirm|apply)",
        text,
    ):
        return True
    if re.search(
        r"(写入|写进|保存|同步|确认|执行写入).{0,10}(计划|规划|草稿|当前|这个|这份|刚才|上面|它|plan|draft)",
        text,
    ):
        return True
    return bool(
        re.search(
            r"(计划|规划|草稿|当前|这个|这份|刚才|上面|它|this plan|current plan|draft).{0,10}(写入|写进|保存|同步|确认|执行写入|write|save|sync|confirm|apply)",
            text,
        )
    )


def resolve_command_intent(
    message: str,
    intent: CommandIntent | None = None,
    *,
    has_current_draft: bool = False,
) -> CommandIntent:
    resolved = intent or detect_command_intent(message)
    if has_current_draft and resolved in {"normal_chat", "planning_request", "unsupported_command"}:
        if _looks_like_current_draft_write(message):
            return "sync_to_calendar"
    return resolved


def _decision_routing_task_type(intent: CommandIntent) -> str:
    if intent == "patch_calendar_plan":
        return "calendar_patch"
    if intent in {"query_memory", "query_notes"}:
        return "memory_query"
    if intent in {"save_memory", "save_note"}:
        return "memory_write"
    return "command_decision"


def _intent_reply(intent: CommandIntent, message: str) -> str:
    english = _looks_english(message)
    if english:
        replies = {
            "regenerate_draft": "I recognize this as a regeneration request. Phase 4.4 now uses the current hidden draft to regenerate a new version when a draft exists.",
            "modify_current_draft": "I recognize this as a draft modification request. If a current draft exists, Planix will regenerate it with your new instruction.",
            "show_current_plan": "I recognize this as a request to show the current plan. If a draft exists, I can expand it inline in this thread.",
            "sync_to_calendar": "I recognize this as a calendar write instruction. If a current draft exists, I can prepare a Calendar write request and apply the permission rule.",
            "refine_current_plan": "I recognize this as a task refinement request. If a current draft exists, I can refine the selected task or all draft tasks inline.",
            "query_plan": "I recognize this as a plan lookup. I can search calendar plans, materials, notes, and planning history from this thread.",
            "patch_calendar_plan": "I recognize this as a calendar plan change. I will preview the change before applying it.",
            "navigate_ui": "I recognize this as a navigation request. This phase keeps navigation as text only.",
            "unsupported_command": "I recognize this as an operation request, but this phase only supports calendar-plan draft control from P Mode.",
        }
        return replies.get(intent, "")
    replies = {
        "regenerate_draft": "我识别到这是重新生成计划的指令。如果当前线程已有计划草稿，Planix 会基于它生成一个新版本。",
        "modify_current_draft": "我识别到这是修改计划草稿的指令。如果当前线程已有计划草稿，Planix 会结合你的要求重新生成新版本。",
        "show_current_plan": "我识别到这是展开完整计划的指令。如果当前线程已有计划草稿，我会在对话里以内联卡片展示。",
        "sync_to_calendar": "我识别到这是写入日历的指令。如果当前线程已有计划草稿，我会生成日历写入请求并按权限处理。",
        "refine_current_plan": "我识别到这是细化任务的指令。如果当前线程已有计划草稿，我会细化指定任务；没有明确指定时会细化全部任务。",
        "query_plan": "我识别到这是查询计划的指令。我会从日历计划、资料、月笔记和历史规划里查找。",
        "patch_calendar_plan": "我识别到这是修改日历计划的指令。我会先生成修改预览，再按权限确认或执行。",
        "navigate_ui": "我识别到这是页面导航指令。本阶段仍只返回文字说明，不控制界面跳转。",
        "unsupported_command": "我识别到这是操作类指令。本阶段只支持 P Mode 的日历计划草稿控制闭环。",
    }
    return replies.get(intent, "")


def _chat_lock_reply(intent: CommandIntent, message: str) -> str:
    if intent == "normal_chat":
        return ""
    if _looks_english(message):
        return "Chat mode is on, so I will not execute instructions. Turn off chat mode to let Planix handle this as an agent command."
    return "当前是普通聊天模式，我不会执行规划、写入或其它操作。关闭聊天模式后，可以让 Planix 按指令处理。"


def _fallback_reply(message: str) -> str:
    if _looks_english(message):
        return "The model is temporarily unavailable. I can still discuss the request, but I will not execute actions or write data."
    return "模型暂时不可用。我可以先用本地回复继续讨论，但不会执行操作或写入数据。"


_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "zhipu_glm": "GLM",
    "kimi": "Kimi",
    "openai": "OpenAI",
    "custom": "Custom",
    "mock": "Mock",
}


def _goal_failure_providers(outcome: GoalUnderstandingOutcome | None, error_types: set[str]) -> list[str]:
    usage = outcome.usage if outcome else None
    result: list[str] = []
    for attempt in (usage.attempts if usage else []):
        if attempt.error_type not in error_types:
            continue
        label = _PROVIDER_LABELS.get(attempt.provider, attempt.provider)
        if label and label not in result:
            result.append(label)
    return result


def _joined_providers(providers: list[str], *, english: bool) -> str:
    if not providers:
        return "the configured providers" if english else "已配置的模型服务"
    if english and len(providers) > 1:
        return f"{', '.join(providers[:-1])} and {providers[-1]}"
    return (", " if english else "、").join(providers)


def _goal_understanding_unavailable_reply(
    message: str,
    outcome: GoalUnderstandingOutcome | None = None,
) -> str:
    english = _looks_english(message)
    usage = outcome.usage if outcome else None
    error_types = {
        attempt.error_type or "unknown"
        for attempt in (usage.attempts if usage else [])
    }
    if outcome and outcome.source == "invalid_model_output":
        error_types.add("invalid_model_output")

    auth = _goal_failure_providers(outcome, {"auth_error", "invalid_key_format"})
    missing = _goal_failure_providers(outcome, {"missing_api_key"})
    primary_missing = [provider for provider in missing if provider in {"DeepSeek", "GLM", "Kimi"}]
    balance = _goal_failure_providers(outcome, {"insufficient_balance"})
    limited = _goal_failure_providers(outcome, {"rate_limit"})
    timed_out = _goal_failure_providers(outcome, {"timeout"})
    network = _goal_failure_providers(outcome, {"network_error", "bad_base_url"})
    bad_model = _goal_failure_providers(outcome, {"bad_model"})
    bad_request = _goal_failure_providers(outcome, {"bad_request"})

    if english:
        prefix = "I received your goal, but the Goal Understanding model could not complete its check, so planning did not start."
        if auth:
            reason = f" {_joined_providers(auth, english=True)} rejected the saved API Key as invalid or expired."
            if primary_missing:
                reason += f" {_joined_providers(primary_missing, english=True)} does not have a saved Key."
            action = " Update at least one valid model Key in Settings, then retry."
        elif balance:
            reason = f" {_joined_providers(balance, english=True)} reported insufficient balance or quota."
            action = " Add quota or select another configured provider, then retry."
        elif limited:
            reason = f" {_joined_providers(limited, english=True)} is currently rate-limited."
            action = " Wait briefly or select another configured provider, then retry."
        elif timed_out:
            reason = f" {_joined_providers(timed_out, english=True)} timed out before returning a result."
            action = " Check the network or timeout setting, then retry."
        elif network:
            reason = f" {_joined_providers(network, english=True)} could not be reached with the configured endpoint."
            action = " Check the Base URL and network in Settings, then retry."
        elif bad_model:
            reason = f" {_joined_providers(bad_model, english=True)} rejected the configured model name."
            action = " Select a model available to that account, then retry."
        elif {"invalid_model_output", "model_output_truncated"} & error_types:
            reason = " The model response was truncated or did not satisfy the Goal Understanding structured contract."
            action = " Retry or select another configured model in Settings."
        elif bad_request:
            reason = f" {_joined_providers(bad_request, english=True)} rejected the Goal Understanding request."
            action = " Check the provider configuration or select another model, then retry."
        elif missing:
            reason = f" No usable API Key is saved; {_joined_providers(missing, english=True)} was skipped."
            action = " Save at least one model Key in Settings, then retry."
        else:
            reason = " The configured model service did not return a usable result."
            action = " Check model Settings and retry."
        return f"{prefix}{reason}{action} This is not caused by how you phrased the goal; your original input was saved."

    prefix = "我已经收到你的目标，但目标理解模型未能完成判断，因此没有启动规划。"
    if auth:
        reason = f"原因：{_joined_providers(auth, english=False)} 的 API Key 无效或已过期。"
        if primary_missing:
            reason += f"{_joined_providers(primary_missing, english=False)} 尚未配置 Key。"
        action = "请在设置中更新至少一个有效的模型 Key，然后重试。"
    elif balance:
        reason = f"原因：{_joined_providers(balance, english=False)} 返回余额或额度不足。"
        action = "请补充额度或选择其他已配置 Provider，然后重试。"
    elif limited:
        reason = f"原因：{_joined_providers(limited, english=False)} 当前触发频率限制。"
        action = "请稍后重试，或选择其他已配置 Provider。"
    elif timed_out:
        reason = f"原因：{_joined_providers(timed_out, english=False)} 在返回结果前超时。"
        action = "请检查网络或超时设置，然后重试。"
    elif network:
        reason = f"原因：无法通过当前地址连接 {_joined_providers(network, english=False)}。"
        action = "请在设置中检查 Base URL 与网络，然后重试。"
    elif bad_model:
        reason = f"原因：{_joined_providers(bad_model, english=False)} 不接受当前模型名称。"
        action = "请选择该账号可用的模型，然后重试。"
    elif {"invalid_model_output", "model_output_truncated"} & error_types:
        reason = "原因：模型返回内容被截断或不符合 Goal Understanding 结构化协议。"
        action = "请重试，或在设置中切换到其他已配置模型。"
    elif bad_request:
        reason = f"原因：{_joined_providers(bad_request, english=False)} 拒绝了本次目标理解请求。"
        action = "请检查 Provider 配置或切换模型，然后重试。"
    elif missing:
        reason = f"原因：没有可用的 API Key，{_joined_providers(missing, english=False)} 已被跳过。"
        action = "请在设置中至少保存一个模型 Key，然后重试。"
    else:
        reason = "原因：已配置的模型服务没有返回可用结果。"
        action = "请检查模型设置后重试。"
    return f"{prefix}{reason}{action}这不是你的目标表达有问题；原始输入已经保留。"


def _stream_failure_message(payload: CommandChatRequest, intent: CommandIntent | None = None) -> str:
    intent = intent or detect_command_intent(payload.message)
    if payload.mode == "auto" and intent == "sync_to_calendar":
        return CALENDAR_WRITE_ERROR_MESSAGE
    if payload.mode == "auto" and intent == "refine_current_plan":
        return "任务细化失败，请检查后端服务或计划数据。"
    if payload.mode == "auto" and intent == "show_current_plan":
        return "展开计划失败，请重启后端服务后重试。"
    if payload.mode == "auto" and intent == "query_plan":
        return "查询计划失败，请检查后端服务或本地数据。"
    if payload.mode == "auto" and intent == "query_notes":
        return "查询笔记失败，请检查后端服务或本地资料。"
    if payload.mode == "auto" and intent == "patch_calendar_plan":
        return "修改日历计划失败，请检查后端服务或计划数据。"
    if payload.mode == "auto" and intent == "save_note":
        return "保存笔记失败，请检查后端服务或月笔记数据。"
    if payload.mode == "auto" and intent in {"regenerate_draft", "modify_current_draft"}:
        return "重新生成计划失败，请重启后端服务后重试。"
    if payload.mode == "workbench":
        return DRAFT_SAVE_ERROR_MESSAGE
    return COMMAND_STREAM_ERROR_MESSAGE


def _approval_failure_message(action: Any | None) -> str:
    if action and action["target"] == "calendar":
        return CALENDAR_WRITE_ERROR_MESSAGE
    if action and action["target"] in {"notes", "memory"}:
        return "保存笔记失败，请检查后端服务或月笔记数据。"
    return COMMAND_STREAM_ERROR_MESSAGE


def _context_date(payload: CommandChatRequest) -> str:
    value = payload.context.get("date") if isinstance(payload.context, dict) else None
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return _today()


def _usage_payload(usage: ModelUsage | None) -> dict[str, Any] | None:
    if not usage:
        return None
    return usage.model_dump(by_alias=True, exclude_none=True)


def _decision_payload(decision: CommandDecision, source: str, error: str = "") -> dict[str, Any]:
    payload = decision.model_dump(by_alias=True, exclude_none=True)
    payload["source"] = source
    if error:
        payload["error"] = error
    return payload


def _decision_to_intent(decision: CommandDecision) -> CommandIntent:
    mapping: dict[str, CommandIntent] = {
        "create_plan": "planning_request",
        "save_plan_to_calendar": "sync_to_calendar",
        "query_plan": "query_plan",
        "query_memory": "query_memory",
        "query_notes": "query_memory",
        "patch_calendar_plan": "patch_calendar_plan",
        "refine_plan": "refine_current_plan",
        "refine_task": "refine_current_plan",
        "save_memory": "save_memory",
        "save_note": "save_memory",
        "modify_current_draft": "modify_current_draft",
        "chat": "normal_chat",
        "clarify": "clarify",
    }
    return mapping.get(decision.intent, "normal_chat")


def _fallback_decision(intent: CommandIntent, message: str) -> CommandDecision:
    summary = _intent_reply(intent, message) or ("我会先按本地规则处理这条请求。" if not _looks_english(message) else "I will handle this with a local fallback rule.")
    intent_map: dict[CommandIntent, str] = {
        "planning_request": "create_plan",
        "sync_to_calendar": "save_plan_to_calendar",
        "query_plan": "query_plan",
        "query_memory": "query_memory",
        "query_notes": "query_memory",
        "patch_calendar_plan": "patch_calendar_plan",
        "refine_current_plan": "refine_task",
        "regenerate_draft": "modify_current_draft",
        "modify_current_draft": "modify_current_draft",
        "show_current_plan": "chat",
        "save_memory": "save_memory",
        "save_note": "save_memory",
        "clarify": "clarify",
    }
    action_map: dict[CommandIntent, str] = {
        "planning_request": "create",
        "sync_to_calendar": "save",
        "query_plan": "query",
        "query_memory": "query",
        "query_notes": "query",
        "patch_calendar_plan": "update",
        "refine_current_plan": "refine",
        "regenerate_draft": "update",
        "modify_current_draft": "update",
        "save_memory": "save",
        "save_note": "save",
        "clarify": "answer",
    }
    return CommandDecision(
        intent=intent_map.get(intent, "chat"),
        confidence=0.55 if intent != "normal_chat" else 0.5,
        targetType="unknown",
        action=action_map.get(intent, "answer"),
        needsConfirmation=intent in {"sync_to_calendar", "patch_calendar_plan", "save_note", "save_memory"},
        needsClarification=intent == "clarify",
        clarificationQuestion="你想让我查计划、修改计划，还是做一个新规划？" if intent == "clarify" else None,
        decisionSummary=summary,
    )


WEEKDAY_INDEX = {
    "一": 0,
    "1": 0,
    "二": 1,
    "2": 1,
    "三": 2,
    "3": 2,
    "四": 3,
    "4": 3,
    "五": 4,
    "5": 4,
    "六": 5,
    "6": 5,
    "日": 6,
    "天": 6,
    "7": 6,
}

QUERY_FILLERS = [
    "帮我",
    "找一下",
    "查一下",
    "查询",
    "查",
    "找",
    "我",
    "我的",
    "把",
    "的",
    "这个",
    "这条",
    "当前",
    "今天",
    "明天",
    "后天",
    "大后天",
    "本周",
    "这周",
    "下周",
    "本月",
    "这个月",
    "有什么",
    "有哪些",
    "哪里",
    "在哪",
    "安排",
    "计划",
    "规划",
    "任务",
    "日历",
    "资料",
    "材料",
    "文档",
    "笔记",
    "之前",
    "上次",
    "最近",
    "未完成",
    "完成",
    "相关",
    "和",
    "有关",
    "改到",
    "改为",
    "改成",
    "调整到",
    "移到",
    "挪到",
    "推到",
    "删除",
    "删掉",
    "取消",
    "移除",
    "时长",
    "时间",
    "分钟",
    "小时",
]


def _parse_date(value: str) -> date_type:
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", value.strip())
    if match:
        year, month, day = (int(part) for part in match.groups())
        return date_type(year, month, day)
    return date_type.fromisoformat(value.strip()[:10])


def _base_date(base_iso: str) -> date_type:
    try:
        return _parse_date(base_iso)
    except (TypeError, ValueError):
        return date_type.today()


def _month_end(value: date_type) -> date_type:
    if value.month == 12:
        return date_type(value.year, 12, 31)
    return date_type(value.year, value.month + 1, 1) - timedelta(days=1)


def _weekday_date(prefix: str, day_token: str, base: date_type) -> date_type:
    monday = base - timedelta(days=base.weekday())
    offset = 7 if prefix in {"下周", "下星期", "next"} else 0
    return monday + timedelta(days=offset + WEEKDAY_INDEX.get(day_token, 0))


def _date_candidates(text: str, base: date_type) -> list[tuple[int, date_type, str]]:
    candidates: list[tuple[int, date_type, str]] = []
    for match in re.finditer(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", text):
        try:
            candidates.append((match.start(), _parse_date(match.group(1)), match.group(1)))
        except ValueError:
            pass
    phrase_offsets = [
        ("day after tomorrow", 2),
        ("tomorrow", 1),
        ("today", 0),
        ("大后天", 3),
        ("后天", 2),
        ("明天", 1),
        ("今日", 0),
        ("今天", 0),
    ]
    lower = text.lower()
    for phrase, offset in phrase_offsets:
        start = lower.find(phrase)
        if start >= 0:
            candidates.append((start, base + timedelta(days=offset), phrase))
    for match in re.finditer(r"(下周|本周|这周|下星期|星期|周)([一二三四五六日天1-7])", text):
        candidates.append((match.start(), _weekday_date(match.group(1), match.group(2), base), match.group(0)))
    for match in re.finditer(r"\b(next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower):
        weekday_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        candidates.append((match.start(), _weekday_date("next", str(weekday_names.index(match.group(2)) + 1), base), match.group(0)))
    return sorted(candidates, key=lambda item: item[0])


def _first_date_expression(text: str, base_iso: str) -> tuple[date_type, str] | None:
    base = _base_date(base_iso)
    candidates = _date_candidates(text, base)
    if not candidates:
        return None
    _, value, label = candidates[0]
    return value, label


def _date_range_from_text(text: str, base_iso: str) -> dict[str, str] | None:
    base = _base_date(base_iso)
    lowered = text.lower()
    direct = _first_date_expression(text, base.isoformat())
    if direct:
        value, label = direct
        return {"startDate": value.isoformat(), "endDate": value.isoformat(), "label": label}
    if re.search(r"(本周|这周|this week)", lowered):
        start = base - timedelta(days=base.weekday())
        end = start + timedelta(days=6)
        return {"startDate": start.isoformat(), "endDate": end.isoformat(), "label": "本周"}
    if re.search(r"(下周|next week)", lowered):
        start = base - timedelta(days=base.weekday()) + timedelta(days=7)
        end = start + timedelta(days=6)
        return {"startDate": start.isoformat(), "endDate": end.isoformat(), "label": "下周"}
    if re.search(r"(本月|这个月|this month)", lowered):
        start = date_type(base.year, base.month, 1)
        return {"startDate": start.isoformat(), "endDate": _month_end(base).isoformat(), "label": "本月"}
    if re.search(r"(最近|recent|recently)", lowered):
        return {
            "startDate": (base - timedelta(days=30)).isoformat(),
            "endDate": (base + timedelta(days=30)).isoformat(),
            "label": "最近",
        }
    return None


def _query_terms(message: str) -> list[str]:
    text = message.lower()
    text = re.sub(r"\d{4}-\d{1,2}-\d{1,2}", " ", text)
    text = re.sub(r"\d{1,2}[:：]\d{2}", " ", text)
    text = re.sub(r"\d+(?:\.\d+)?\s*(分钟|分鐘|min|mins|minutes?|小时|小時|hours?|h)", " ", text)
    for filler in QUERY_FILLERS:
        text = text.replace(filler.lower(), " ")
    text = re.sub(r"(周|星期)[一二三四五六日天1-7]", " ", text)
    tokens = re.findall(r"[a-z0-9+#.-]+|[\u4e00-\u9fff]{2,}", text)
    return [token.strip(".-") for token in tokens if token.strip(".-")]


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _planning_approval_text(text: str) -> bool:
    stripped = text.strip().lower()
    if stripped in {"1", "好", "好的", "可以", "可以的", "确认", "没问题", "嗯", "ok", "okay", "yes"}:
        return True
    return _contains_any(
        text,
        (
            "确认",
            "可以",
            "没问题",
            "方向对",
            "按这个",
            "就这样",
            "生成执行",
            "确认方向",
            "确认执行计划",
            "确认任务",
            "任务可以",
            "就这样",
            "approve",
            "confirm",
            "looks good",
            "go ahead",
        ),
    )


def _planning_revision_text(text: str) -> bool:
    return _contains_any(
        text,
        (
            "调整",
            "修改",
            "不对",
            "不是",
            "太理论",
            "更想",
            "换",
            "资源太难",
            "任务太重",
            "太多",
            "太难",
            "看不懂",
            "revise",
            "change",
            "too hard",
        ),
    )


def _planning_calendar_write_text(text: str) -> bool:
    return _contains_any(text, ("写入日历", "保存到日历", "同步到日历", "write calendar", "write to calendar", "save to calendar"))


def _planning_feedback_text(text: str) -> bool:
    return _contains_any(text, ("资源太难", "任务太重", "太理论", "看不懂", "没完成", "时间不够", "喜欢这种", "太多", "too hard"))


def _planning_restart_text(text: str) -> bool:
    return _contains_any(
        text,
        (
            "重新开始",
            "新建一个计划",
            "新建计划",
            "新的计划",
            "忽略上一个计划",
            "忽略上一个",
            "重新做一个",
            "重新规划",
            "换一个新计划",
            "从头来",
            "restart",
            "start over",
            "new plan",
            "ignore previous",
            "ignore the previous",
        ),
    )


def _plan_card_from_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["date"],
        "time": row["time"],
        "title": row["content"],
        "done": bool(row["done"]),
        "completion": row["result"],
        "priority": row["priority"],
        "estimatedMinutes": int(row["estimated_minutes"] or 0),
        "source": row["source"],
        "sourceKey": row["source_key"] if "source_key" in row.keys() else "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _plan_matches_terms(plan: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        str(plan.get(key) or "")
        for key in ("title", "completion", "date", "time", "source", "sourceKey")
    ).lower()
    return all(term.lower() in haystack for term in terms)


def _search_calendar_plans(message: str, base_iso: str, *, limit: int = 12) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    date_range = _date_range_from_text(message, base_iso)
    terms = _query_terms(message)
    unfinished_only = _contains_any(message, ("未完成", "没完成", "unfinished", "pending"))
    params: tuple[Any, ...]
    where = []
    if date_range:
        where.append("date >= ? AND date <= ?")
        params = (date_range["startDate"], date_range["endDate"])
    else:
        params = ()
    if unfinished_only:
        where.append("done = 0")
    sql = "SELECT * FROM plans"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date ASC, time ASC, created_at ASC"
    if not date_range:
        sql += " LIMIT 200"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    plans = [_plan_card_from_row(row) for row in rows]
    if terms:
        plans = [plan for plan in plans if _plan_matches_terms(plan, terms)]
    return plans[:limit], date_range


def _search_goal_history(message: str, *, limit: int = 5) -> list[dict[str, Any]]:
    terms = _query_terms(message)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, goal, deadline, summary, structured_plan_json, created_at, updated_at
            FROM planning_goals
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()
    results = []
    for row in rows:
        structured = _json_object(row["structured_plan_json"])
        haystack = " ".join(
            [
                row["goal"],
                row["summary"],
                str(structured.get("goalTitle") or ""),
                str(structured.get("goalDescription") or ""),
            ]
        ).lower()
        if terms and not any(term.lower() in haystack for term in terms):
            continue
        results.append(
            {
                "id": row["id"],
                "goal": row["goal"],
                "title": structured.get("goalTitle") or row["goal"],
                "summary": row["summary"],
                "deadline": row["deadline"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )
        if len(results) >= limit:
            break
    return results


def _search_month_notes(message: str, *, limit: int = 5) -> list[dict[str, Any]]:
    terms = _query_terms(message)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT year, month, content, updated_at
            FROM month_notes
            WHERE content != ''
            ORDER BY year DESC, month DESC
            LIMIT 120
            """
        ).fetchall()
    results = []
    for row in rows:
        content = row["content"]
        haystack = content.lower()
        if terms and not any(term.lower() in haystack for term in terms):
            continue
        results.append(
            {
                "year": row["year"],
                "month": row["month"],
                "content": content[:360],
                "updatedAt": row["updated_at"],
            }
        )
        if len(results) >= limit:
            break
    return results


def _query_result_text(result: dict[str, Any]) -> str:
    calendar_count = len(result.get("calendarPlans") or [])
    material_count = len(result.get("materials") or [])
    history_count = len(result.get("goalHistory") or [])
    note_count = len(result.get("monthNotes") or [])
    parts = []
    if calendar_count:
        parts.append(f"日历计划 {calendar_count} 个")
    if material_count:
        parts.append(f"资料 {material_count} 条")
    if history_count:
        parts.append(f"历史规划 {history_count} 条")
    if note_count:
        parts.append(f"月笔记 {note_count} 条")
    return "找到" + "、".join(parts) + "。" if parts else "没有找到匹配的计划、资料或笔记。"


def _looks_like_query(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"(今天|明天|后天|本周|这周|下周|本月|最近).{0,12}(什么|哪些|安排|计划|任务|日程)", lowered):
        return True
    return bool(
        re.search(
            r"(查|查询|找|搜索|在哪|哪里|有什么|有哪些|未完成|history|search|find|what.*(plan|schedule|task)|where.*plan)",
            lowered,
        )
    )


def _looks_like_calendar_patch(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"(重新生成|再生成|换个版本|更轻松|更激进|regenerate)", lowered):
        return False
    if _looks_like_generic_calendar_patch(lowered):
        return True
    if re.search(r"(删除|删掉|取消|移除|delete|remove|cancel).{0,20}(计划|任务|日程|安排|plan|task|schedule)?", lowered):
        return True
    patch_words = r"(改到|改为|改成|调整到|移到|挪到|推到|改名|标题|名称|时长|时间|分钟|小时|reschedule|move|rename|change|update)"
    if re.search(patch_words, lowered) and (
        _first_date_expression(text, _today()) is not None
        or re.search(r"(第\s*\d+|第[一二三四五六七八九十]+|first|second|third|这个|这条|this)", lowered)
    ):
        return True
    return False


def _looks_like_generic_calendar_patch(text: str) -> bool:
    lowered = text.lower().strip()
    if re.search(r"(修改|调整|改一下|改动|edit|modify|change|update).{0,8}(我的|当前|日历)?(计划|任务|日程|安排|plan|task|schedule)", lowered):
        return True
    return bool(re.fullmatch(r"(修改|调整|改一下|edit|modify|change|update)\s*(计划|任务|plan|task)", lowered))


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _valid_structured_plan(value: Any) -> bool:
    if not _is_record(value):
        return False
    milestones = value.get("milestones")
    if not isinstance(milestones, list):
        return False
    for milestone in milestones:
        if not _is_record(milestone):
            continue
        tasks = milestone.get("tasks")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if _is_record(task) and isinstance(task.get("title"), str) and task["title"].strip():
                return True
    return False


def _task_count(structured_plan: dict[str, Any]) -> int:
    count = 0
    for milestone in structured_plan.get("milestones") or []:
        if not _is_record(milestone):
            continue
        for task in milestone.get("tasks") or []:
            if _is_record(task) and isinstance(task.get("title"), str) and task["title"].strip():
                count += 1
    return count


def _milestone_count(structured_plan: dict[str, Any]) -> int:
    return len([item for item in structured_plan.get("milestones") or [] if _is_record(item)])


def _duration_days(structured_plan: dict[str, Any]) -> int:
    value = structured_plan.get("durationDays")
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    dates: set[str] = set()
    for milestone in structured_plan.get("milestones") or []:
        if not _is_record(milestone):
            continue
        for task in milestone.get("tasks") or []:
            if _is_record(task) and isinstance(task.get("dueDate"), str) and re.match(r"^\d{4}-\d{2}-\d{2}", task["dueDate"]):
                dates.add(task["dueDate"][:10])
    return max(1, len(dates))


def _plan_title(structured_plan: dict[str, Any], fallback: str) -> str:
    for key in ("goalTitle", "title", "goal"):
        value = structured_plan.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback.strip()[:80] or "Planix 计划草稿"


def _task_key(milestone_index: int, task_index: int) -> str:
    return f"m{milestone_index}:t{task_index}"


def _refined_task_from_payload(value: Any) -> RefinedTask | None:
    if isinstance(value, RefinedTask):
        return value
    if isinstance(value, dict):
        try:
            return RefinedTask.model_validate(value)
        except Exception:
            return None
    return None


def _compact_summary(
    structured_plan: dict[str, Any],
    *,
    title: str,
    quality_status: str | None = None,
    plan_horizon: dict[str, Any] | None = None,
) -> str:
    milestones = _milestone_count(structured_plan)
    tasks = _task_count(structured_plan)
    days = _duration_days(structured_plan)
    quality_label = {
        "passed": "良好",
        "repaired": "已自动补全",
        "local_fallback": "本地模板兜底",
    }.get(str(quality_status or ""), "已生成")
    horizon_days = plan_horizon.get("durationDays") if isinstance(plan_horizon, dict) else days
    return (
        "已生成计划草稿。\n\n"
        "摘要：\n"
        f"- 计划：{title}\n"
        f"- {milestones} 个阶段\n"
        f"- {tasks} 个任务\n"
        f"- 覆盖 {horizon_days} 天\n"
        f"- 计划质量：{quality_label}\n"
        "- 可写入日历\n\n"
        "你可以继续说：\n"
        "“展开看看完整计划”\n"
        "“重新生成一个更轻松的版本”\n"
        "“把这个计划写进日历”"
    )


def _runtime_tool_summary(name: str) -> str:
    labels = {
        "get_memory": "读取记忆",
        "get_today_plans": "读取今日计划",
        "search_materials": "检索本地资料",
        "enrich_with_model_knowledge": "大模型知识补全",
        "propose_tasks": "生成任务提案",
    }
    return labels.get(name, name)


def _normalize_task_date(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return value
        if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            return value[:10]
    return fallback


def _default_task_time(sequence: int) -> str:
    hour = min(21, 9 + (sequence % 9))
    return f"{hour:02d}:00"


def _task_priority(value: Any) -> str:
    return value if value in {"low", "medium", "high"} else "medium"


def _task_minutes(value: Any) -> int:
    try:
        parsed = int(value)
        if 1 <= parsed <= 1440:
            return parsed
    except (TypeError, ValueError):
        pass
    return 60


def _write_result_text(result: dict[str, Any]) -> str:
    created = int(result.get("created") or 0)
    updated = int(result.get("updated") or 0)
    failed = int(result.get("failed") or 0)
    if failed and (created or updated):
        return f"部分计划写入成功，失败 {failed} 个。"
    if failed:
        return "写入日历失败，请检查后端服务或计划数据。"
    return f"已写入日历：创建 {created} 个，更新 {updated} 个，失败 {failed} 个。"


def _patch_result_text(result: dict[str, Any]) -> str:
    if result.get("status") == "failed":
        return str(result.get("error") or "修改日历计划失败。")
    operation = result.get("operation")
    if operation == "delete":
        return "已删除日历计划。"
    return "已更新日历计划。"


def _note_result_text(result: dict[str, Any]) -> str:
    if result.get("status") == "failed":
        return str(result.get("error") or "保存笔记失败。")
    return "已保存到月笔记。"


def _memory_result_text(result: dict[str, Any]) -> str:
    if result.get("status") == "failed":
        return str(result.get("error") or "记录记忆失败。")
    return "已记录到记忆库。"


def _extract_ordinal(message: str) -> int | None:
    lowered = message.lower()
    number_match = re.search(r"(?:第\s*)?(\d+)(?:\s*(?:个|项|条|号|task))", lowered)
    if number_match:
        value = int(number_match.group(1))
        return value if value > 0 else None
    zh_numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    zh_match = re.search(r"第\s*([一二两三四五六七八九十])\s*(?:个|项|条|号)?", message)
    if zh_match:
        return zh_numbers.get(zh_match.group(1))
    english = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    for word, value in english.items():
        if re.search(rf"\b{word}\b", lowered):
            return value
    return None


def _target_date_after_patch_verb(message: str, base_iso: str) -> str | None:
    for match in re.finditer(r"(改到|改为|改成|调整到|移到|挪到|推到|reschedule\s+to|move\s+to|change\s+to)", message, re.I):
        tail = message[match.end(): match.end() + 40]
        resolved = _first_date_expression(tail, base_iso)
        if resolved:
            return resolved[0].isoformat()
    return None


def _target_time_from_message(message: str) -> str | None:
    match = re.search(r"(\d{1,2})[:：](\d{2})", message)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    match = re.search(r"(上午|早上|下午|晚上)?\s*(\d{1,2})\s*点(?:\s*(半|(\d{1,2})\s*分?))?", message)
    if match:
        hour = int(match.group(2))
        if match.group(1) in {"下午", "晚上"} and hour < 12:
            hour += 12
        minute = 30 if match.group(3) == "半" else int(match.group(4) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def _target_minutes_from_message(message: str) -> int | None:
    minute_match = re.search(r"(\d{1,4})\s*(分钟|分鐘|min|mins|minutes?)", message, re.I)
    if minute_match:
        value = int(minute_match.group(1))
        return min(max(value, 1), 1440)
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(小时|小時|hours?|h)(?:\b|$)", message, re.I)
    if hour_match:
        value = round(float(hour_match.group(1)) * 60)
        return min(max(value, 1), 1440)
    return None


def _target_title_from_message(message: str) -> str | None:
    if not re.search(r"(标题|名称|名字|改名|rename|title)", message, re.I):
        return None
    match = re.search(r"(?:改成|改为|叫|命名为|rename\s+to|title\s+to)\s*[“\"']?(.+?)[”\"']?$", message, re.I)
    if not match:
        return None
    title = re.sub(r"[。.!！]+$", "", match.group(1).strip())
    return title[:160] if title else None


def _patch_changes_from_message(message: str, base_iso: str) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    target_date = _target_date_after_patch_verb(message, base_iso)
    target_time = _target_time_from_message(message)
    target_minutes = _target_minutes_from_message(message)
    target_title = _target_title_from_message(message)
    if target_date:
        changes["date"] = target_date
    if target_time:
        changes["time"] = target_time
    if target_minutes:
        changes["estimatedMinutes"] = target_minutes
    if target_title:
        changes["content"] = target_title
    return changes


def _is_delete_request(message: str) -> bool:
    return bool(re.search(r"(删除|删掉|取消|移除|delete|remove|cancel)", message, re.I))


def _note_text_from_message(message: str) -> str:
    cleaned = re.sub(r"(请|帮我|把|将|这段|这个|内容|保存|存下|记下|作为|成|到|笔记|note|save)", " ", message, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:，,。.")
    return cleaned[:2000]


def _truncate_context_line(value: str, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}..."


def _runtime_input_with_thread_context(message: str, context_summary: str) -> str:
    if not context_summary.strip():
        return message
    return (
        "当前用户请求：\n"
        f"{message}\n\n"
        "当前对话上下文（仅来自当前 thread，用于承接指代、地点、主题和约束；不要使用其它对话的内容）：\n"
        f"{context_summary}"
    )


class CommandAgentService:
    def ensure_thread(self, thread_id: str | None = None, title: str = "") -> str:
        if thread_id:
            with get_conn() as conn:
                row = conn.execute("SELECT id FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
                if row:
                    conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (_now(), thread_id))
                    return thread_id

        new_id = thread_id or str(uuid4())
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_threads(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (new_id, (title or "Planix Command").strip()[:160], _now(), _now()),
            )
        return new_id

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        kind: str = "text",
        payload: dict[str, Any] | None = None,
    ) -> CommandMessageOut:
        message_id = str(uuid4())
        created_at = _now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_messages(id, thread_id, role, content, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    role,
                    content,
                    kind,
                    json.dumps(payload or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (created_at, thread_id))
        return CommandMessageOut(
            id=message_id,
            threadId=thread_id,
            role=role,
            content=content,
            kind=kind,
            payload=payload or {},
            createdAt=created_at,
        )

    def list_threads(self, limit: int = 50) -> list[CommandThreadSummaryOut]:
        safe_limit = min(max(int(limit or 50), 1), 100)
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                  t.id,
                  t.title,
                  t.created_at,
                  t.updated_at,
                  COUNT(m.id) AS message_count,
                  COALESCE(cd.title, '') AS current_draft_title
                FROM command_threads t
                LEFT JOIN command_messages m ON m.thread_id = t.id
                LEFT JOIN command_drafts cd
                  ON cd.thread_id = t.id
                 AND cd.kind = 'calendar_plan'
                 AND cd.status = 'current'
                GROUP BY t.id
                ORDER BY t.updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            CommandThreadSummaryOut(
                id=row["id"],
                title=row["title"],
                messageCount=int(row["message_count"] or 0),
                currentDraftTitle=row["current_draft_title"] or "",
                createdAt=row["created_at"],
                updatedAt=row["updated_at"],
            )
            for row in rows
        ]

    def delete_thread(self, thread_id: str) -> dict[str, int]:
        with get_conn() as conn:
            existing = conn.execute("SELECT id FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Thread not found")
            conn.execute("DELETE FROM command_approvals WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_actions WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_drafts WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_messages WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM command_threads WHERE id = ?", (thread_id,))
        return {"deleted": 1}

    def get_thread(self, thread_id: str):
        from ..schemas import CommandThreadOut

        with get_conn() as conn:
            thread = conn.execute("SELECT * FROM command_threads WHERE id = ?", (thread_id,)).fetchone()
            if not thread:
                raise HTTPException(status_code=404, detail="Thread not found")
            messages = [
                _row_to_message(row)
                for row in conn.execute(
                    "SELECT * FROM command_messages WHERE thread_id = ? ORDER BY created_at ASC",
                    (thread_id,),
                ).fetchall()
            ]
            current_draft = self._current_draft(conn, thread_id)
        return CommandThreadOut(
            id=thread["id"],
            title=thread["title"],
            messages=messages,
            currentDraft=current_draft,
            createdAt=thread["created_at"],
            updatedAt=thread["updated_at"],
        )

    def _add_planning_session_card(
        self,
        thread_id: str,
        kind: str,
        content: str,
        payload: dict[str, Any],
    ) -> None:
        self.add_message(thread_id, "card", content, kind=kind, payload=payload)

    def _seen_planning_trace_ids(self, thread_id: str, session_id: str) -> set[tuple[str, str]]:
        """Return Agent trace records already projected into this Command thread.

        Planning session snapshots contain the complete immutable decision and
        message logs. Command cards are an incremental projection of those
        logs, so their stable trace ids must be emitted only once per
        thread/session pair.
        """

        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT kind, payload_json
                FROM command_messages
                WHERE thread_id = ?
                  AND role = 'card'
                  AND kind IN ('agent_decision', 'agent_message')
                """,
                (thread_id,),
            ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row in rows:
            payload = _json_object(row["payload_json"])
            if str(payload.get("sessionId") or "") != session_id:
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            trace_id = str(data.get("id") or "").strip()
            if trace_id:
                seen.add((str(row["kind"]), trace_id))
        return seen

    def _planning_event(
        self,
        thread_id: str,
        kind: str,
        session_id: str,
        *,
        data: dict[str, Any] | None = None,
        status: str | None = None,
        business_status: str | None = None,
        runtime_status: str | None = None,
        model_failure: dict[str, Any] | None = None,
        pending_input: dict[str, Any] | None = None,
        content: str = "",
    ) -> str:
        payload: dict[str, Any] = {"sessionId": session_id}
        if status is not None:
            payload["status"] = status
        if business_status is not None:
            payload["businessStatus"] = business_status
        if runtime_status is not None:
            payload["runtimeStatus"] = runtime_status
        if model_failure is not None:
            payload["modelFailure"] = model_failure
        if pending_input is not None:
            payload["pendingInput"] = pending_input
        if data is not None:
            payload["data"] = data
        self._add_planning_session_card(thread_id, kind, content or status or session_id, payload)
        event = {"type": kind, **payload}
        return _ndjson(event)

    def _planning_agent_event(self, name: str, status: str, summary: str) -> str:
        return _ndjson({"type": "runtime_event", "name": name, "status": status, "summary": summary})

    def _stream_goal_understanding(
        self,
        thread_id: str,
        outcome: GoalUnderstandingOutcome,
    ) -> Iterator[str]:
        understanding = outcome.result
        if not understanding:
            return
        event = understanding.model_dump(by_alias=True, exclude_none=True)
        event["source"] = outcome.source
        if outcome.error:
            event["error"] = outcome.error
        usage = _usage_payload(outcome.usage)
        if usage and outcome.source == "llm":
            event["modelUsage"] = usage
        content = understanding.next_question or understanding.understood_intent or understanding.intent_state
        self._add_planning_session_card(
            thread_id,
            "goal_understanding",
            content,
            event,
        )
        yield _ndjson({"type": "goal_understanding", **event})

    def _stream_planning_session_snapshot(
        self,
        thread_id: str,
        session: PlanningSessionResponse,
        *,
        include_start: bool = False,
        agents: list[tuple[str, str]] | None = None,
    ) -> Iterator[str]:
        if include_start:
            yield self._planning_event(
                thread_id,
                "planning_session_started",
                session.session_id,
                status=session.status,
                content=f"Planning session {session.status}",
            )
        _ = agents
        seen_trace_ids = self._seen_planning_trace_ids(thread_id, session.session_id)
        for decision in session.decisions:
            trace_key = ("agent_decision", decision.id)
            if trace_key in seen_trace_ids:
                continue
            data = decision.model_dump(by_alias=True, exclude_none=True)
            yield self._planning_event(
                thread_id,
                "agent_decision",
                session.session_id,
                data=data,
                content=decision.user_visible_summary or decision.reason or decision.decision,
            )
            seen_trace_ids.add(trace_key)
        for message in session.messages:
            trace_key = ("agent_message", message.id)
            if trace_key in seen_trace_ids:
                continue
            data = message.model_dump(by_alias=True)
            yield self._planning_event(
                thread_id,
                "agent_message",
                session.session_id,
                data=data,
                content=message.reason or message.message_type,
            )
            seen_trace_ids.add(trace_key)
        yield self._planning_event(
            thread_id,
            "planning_session_status",
            session.session_id,
            status=session.status,
            business_status=session.business_status,
            runtime_status=session.runtime_status,
            model_failure=(
                session.model_failure.model_dump(by_alias=True, exclude_none=True)
                if session.model_failure
                else None
            ),
            pending_input=(
                session.pending_input.model_dump(by_alias=True)
                if session.pending_input
                else None
            ),
            data={
                "planningPhase": session.planning_phase,
                "planningStep": session.planning_step,
                "cognitiveMetadata": (
                    session.cognitive_metadata.model_dump(by_alias=True, exclude_none=True)
                    if session.cognitive_metadata
                    else None
                ),
                "understandingSnapshot": session.understanding_snapshot,
                "constraintSet": session.constraint_set,
                "contextPack": session.context_pack,
                "planBlueprint": session.plan_blueprint,
                "planQualityReport": session.plan_quality_report,
                "scheduleBlueprint": session.schedule_blueprint,
                "scheduleQualityReport": session.schedule_quality_report,
                "calendarProposal": session.calendar_proposal,
                "finalApprovalBundle": session.final_approval_bundle,
            },
            content=session.status,
        )

    def _stream_planning_start(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        goal_understanding: GoalUnderstandingResult | None = None,
    ) -> Iterator[str]:
        service = get_planning_orchestrator()
        request_context = dict(payload.context) if isinstance(payload.context, dict) else {}
        if goal_understanding:
            request_context["goalUnderstanding"] = goal_understanding.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        session = service.create_session(
            CreatePlanningSessionRequest(
                entryPoint="p_mode",
                threadId=thread_id,
                userInput=payload.message,
                context=request_context,
            )
        )
        agents = [("Understanding Agent", "Built the typed understanding and checked whether it is ready for confirmation.")]
        yield from self._stream_planning_session_snapshot(thread_id, session, include_start=True, agents=agents)

    def _planning_session_followup_action(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        intent: CommandIntent | None = None,
    ) -> tuple[str, PlanningSessionResponse] | None:
        session = get_planning_orchestrator().latest_for_thread(thread_id)
        if not session:
            return None
        text = payload.message
        control_intent = detect_planning_control_intent(text)
        if control_intent == "skip_current_stage":
            return "skip_current_stage", session
        if control_intent == "cancel_planning":
            return "cancel", session
        if control_intent == "restart_planning":
            return "restart_control", session
        if _planning_restart_text(text):
            return "restart", session
        if control_intent == "continue_current_stage":
            return "continue_current_stage", session
        if control_intent == "modify_current_stage":
            return "modify_current_stage", session
        if session.status == "needs_goal_clarification":
            return "answer_understanding", session
        if session.status == "MODEL_UNAVAILABLE":
            return (
                "answer_understanding"
                if session.model_failure and session.model_failure.resume_node == "understanding"
                else "continue_current_stage"
            ), session
        if session.status == "waiting_understanding_confirmation":
            if control_intent == "approve_current_stage" or _planning_approval_text(text):
                return "confirm_understanding", session
            return "revise_understanding", session
        if session.status == "final_revision":
            if _planning_approval_text(text):
                return "final_revision_status", session
            return "revise_final", session
        if session.status == "waiting_final_review":
            if _planning_calendar_write_text(text):
                return "approve_final", session
            if _planning_approval_text(text):
                return "final_review_status", session
            return "revise_final", session
        if session.status == "waiting_calendar_write_approval":
            return "revise_final", session
        if session.status == "learning_from_feedback":
            return "feedback", session
        return None

    def _stream_planning_followup(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        action: str,
        session: PlanningSessionResponse,
    ) -> Iterator[str]:
        service = get_planning_orchestrator()
        request = PlanningSessionTextRequest(text=payload.message)
        if action == "skip_current_stage":
            skip = getattr(service, "skip_current_stage", None)
            if not callable(skip):
                raise HTTPException(status_code=409, detail={"message": "this planning runtime cannot skip goal clarification"})
            updated = skip(session.session_id)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "continue_current_stage":
            updated = service.continue_current_stage(session.session_id)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "modify_current_stage":
            updated = service.get_session(session.session_id)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "cancel":
            updated = service.cancel(session.session_id)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "restart_control":
            cancel = getattr(service, "cancel", None)
            if callable(cancel):
                cancel(session.session_id)
            restart_payload = payload.model_copy(update={"message": session.user_input})
            yield from self._stream_planning_start(thread_id, restart_payload)
            return
        if action == "restart":
            cancel = getattr(service, "cancel", None)
            if callable(cancel):
                cancel(session.session_id)
            yield from self._stream_planning_start(thread_id, payload)
            return
        if action == "answer_understanding":
            updated = service.answer_understanding(session.session_id, request)
            agents = [("Understanding Agent", "Merged the answer and rebuilt the typed understanding.")]
            yield from self._stream_planning_session_snapshot(thread_id, updated, agents=agents)
            return
        if action == "confirm_understanding":
            updated = service.confirm_understanding(session.session_id)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "revise_understanding":
            updated = service.revise_understanding(session.session_id, request)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "final_review_status":
            yield self._planning_event(thread_id, "planning_session_status", session.session_id, status=session.status, content=session.status)
            return
        if action == "final_revision_status":
            quality = session.plan_quality_report or {}
            issues = quality.get("issues") if isinstance(quality, dict) else []
            descriptions = [str(item.get("description") or "") for item in issues or [] if isinstance(item, dict)]
            summary = "The current plan has unresolved quality issues. " + ("; ".join(filter(None, descriptions)) or "Please revise it before confirmation.")
            yield self._planning_event(
                thread_id,
                "agent_decision",
                session.session_id,
                data={
                    "agent": "Plan Reviewer",
                    "decision": "block",
                    "reason": summary,
                    "confidence": 1,
                    "inputArtifactIds": [],
                    "outputArtifactIds": [],
                    "userVisibleSummary": summary,
                },
                content=summary,
            )
            yield self._planning_event(thread_id, "planning_session_status", session.session_id, status=session.status, content=session.status)
            return
        if action in {"revise_final", "feedback"}:
            updated = service.revise_final(session.session_id, request)
            yield from self._stream_planning_session_snapshot(thread_id, updated)
            return
        if action == "approve_final":
            yield from self._stream_planning_calendar_write(thread_id, payload, session)

    def _stream_planning_calendar_write(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        session: PlanningSessionResponse,
    ) -> Iterator[str]:
        service = get_planning_orchestrator()
        updated = service.approve_final(session.session_id)
        structured_plan = service.plan_to_structured_plan(updated)
        refs: dict[str, dict[str, Any]] = {}
        for kind in ("final_approval_bundle", "calendar_proposal"):
            artifact = max(
                (item for item in updated.artifacts if item.artifact_type == kind),
                key=lambda item: item.version,
                default=None,
            )
            if artifact is None:
                raise RuntimeError(f"Calendar preview requires a versioned {kind} artifact")
            refs[kind] = {
                "id": artifact.id,
                "sessionId": artifact.session_id,
                "kind": artifact.artifact_type,
                "version": artifact.version,
                "owner": artifact.owner_agent,
                "status": artifact.status,
            }
        plan = updated.plan_blueprint if isinstance(updated.plan_blueprint, dict) else {}
        title = str(plan.get("goalSummary") or _plan_title(structured_plan, updated.user_input))
        summary = title
        self._create_calendar_draft(
            thread_id=thread_id,
            title=title,
            summary=summary,
            source_run_id=updated.session_id,
            payload={
                "structuredPlan": structured_plan,
                "planningSessionId": updated.session_id,
                "finalApprovalRef": refs["final_approval_bundle"],
                "calendarProposalRef": refs["calendar_proposal"],
                "goal": updated.user_input,
                "mode": "formal_planning_session",
                "qualityReport": updated.plan_quality_report,
                "qualityStatus": "passed" if (updated.plan_quality_report or {}).get("passed") else "needs_revision",
                "sourceType": "local_context",
                "summary": summary,
            },
        )
        yield from self._stream_planning_session_snapshot(thread_id, updated, agents=[("Calendar Write Gate", "Final approval recorded; preparing Calendar preview.")])
        yield from self._stream_calendar_write_impl(thread_id, payload)

    def _thread_context_summary(self, thread_id: str, exclude_message_id: str = "", limit: int = 8) -> str:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content
                FROM command_messages
                WHERE thread_id = ?
                  AND role IN ('user', 'assistant')
                  AND kind = 'text'
                  AND content != ''
                  AND id != ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (thread_id, exclude_message_id, limit),
            ).fetchall()
        lines: list[str] = []
        for row in reversed(rows):
            role = "用户" if row["role"] == "user" else "Planix"
            content = _truncate_context_line(row["content"])
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _latest_goal_understanding_context(self, thread_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM command_messages
                WHERE thread_id = ? AND role = 'card' AND kind = 'goal_understanding'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        if not row:
            return None
        payload = _json_object(row["payload_json"])
        allowed = {
            "intentState",
            "understoodIntent",
            "possibleDomains",
            "knownFacts",
            "uncertainties",
            "consistencyWarnings",
            "nextQuestion",
            "clarificationOptions",
            "confidence",
        }
        result = {key: payload[key] for key in allowed if key in payload}
        return result or None

    def _draft_decision_summary(self, draft: CommandDraftOut | None) -> dict[str, Any] | None:
        if not draft:
            return None
        structured_plan = draft.payload.get("structuredPlan")
        return {
            "id": draft.id,
            "title": draft.title,
            "version": draft.version,
            "status": draft.status,
            "taskCount": _task_count(structured_plan) if _valid_structured_plan(structured_plan) else 0,
        }

    def _calendar_decision_summary(self, base_iso: str) -> list[dict[str, Any]]:
        base = _base_date(base_iso)
        start = (base - timedelta(days=1)).isoformat()
        end = (base + timedelta(days=7)).isoformat()
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, date, time, content, done, estimated_minutes, source
                FROM plans
                WHERE date >= ? AND date <= ?
                ORDER BY date ASC, time ASC, created_at ASC
                LIMIT 16
                """,
                (start, end),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "date": row["date"],
                "time": row["time"],
                "title": row["content"],
                "done": bool(row["done"]),
                "estimatedMinutes": int(row["estimated_minutes"] or 0),
                "source": row["source"],
            }
            for row in rows
        ]

    def _notes_decision_summary(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT year, month, content, updated_at
                FROM month_notes
                WHERE content != ''
                ORDER BY year DESC, month DESC
                LIMIT 6
                """
            ).fetchall()
        return [
            {
                "year": row["year"],
                "month": row["month"],
                "content": _truncate_context_line(row["content"], 260),
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _resolve_auto_decision(
        self,
        thread_id: str,
        payload: CommandChatRequest,
    ) -> tuple[
        CommandIntent,
        CommandDecision | None,
        str,
        ModelUsage | None,
        str,
        GoalUnderstandingOutcome | None,
    ]:
        base_iso = _context_date(payload)
        current_draft = self._get_current_draft(thread_id)
        thread_context = self._thread_context_summary(thread_id)
        pre_intent = resolve_command_intent(
            payload.message,
            detect_command_intent(payload.message),
            has_current_draft=current_draft is not None,
        )
        goal_outcome = GoalUnderstandingService().understand(
            payload.message,
            thread_context=thread_context,
            prior_understanding=self._latest_goal_understanding_context(thread_id),
        )
        understanding = goal_outcome.result
        if not understanding:
            return (
                "goal_understanding_unavailable",
                None,
                goal_outcome.source,
                goal_outcome.usage,
                goal_outcome.error,
                goal_outcome,
            )
        if understanding:
            if understanding.intent_state == "ambiguous_goal":
                return "clarify", None, "", None, "", goal_outcome
            if understanding.intent_state == "clear_goal":
                return "planning_request", None, "", None, "", goal_outcome
            if understanding.intent_state == "normal_chat":
                return "normal_chat", None, "", None, "", goal_outcome

        decision_result: CommandDecisionResult = CommandDecisionService().decide(
            payload.message,
            task_type=_decision_routing_task_type(pre_intent),
            thread_context=thread_context,
            current_draft=self._draft_decision_summary(current_draft),
            last_search_results=self._last_plan_search_results(thread_id),
            context_date=base_iso,
            calendar_summary=self._calendar_decision_summary(base_iso),
            notes_summary=self._notes_decision_summary(),
        )
        if decision_result.decision:
            resolved_intent = _decision_to_intent(decision_result.decision)
            if resolved_intent == "planning_request":
                return (
                    "normal_chat",
                    decision_result.decision,
                    "goal_understanding_conflict",
                    decision_result.usage,
                    decision_result.error,
                    goal_outcome,
                )
            return (
                resolved_intent,
                decision_result.decision,
                decision_result.source,
                decision_result.usage,
                decision_result.error,
                goal_outcome,
            )

        intent = resolve_command_intent(
            payload.message,
            detect_command_intent(payload.message),
            has_current_draft=current_draft is not None,
        )
        if intent == "planning_request":
            intent = "normal_chat"
        return (
            intent,
            _fallback_decision(intent, payload.message),
            "local_fallback",
            decision_result.usage,
            decision_result.error,
            goal_outcome,
        )

    def stream_chat(self, payload: CommandChatRequest) -> Iterator[str]:
        if payload.mode == "chat":
            yield from self._stream_pure_chat(payload)
            return
        yield from self._stream_chat_with_model(payload)

    def _stream_pure_chat(self, payload: CommandChatRequest) -> Iterator[str]:
        thread_id = payload.thread_id or f"chat-{uuid4()}"
        history = "\n".join(
            f"{'User' if item['role'] == 'user' else 'Assistant'}: {item['content']}"
            for item in payload.history
        )
        user = f"Conversation history:\n{history or '(none)'}\n\nUser: {payload.message}"
        client = LlmClient()
        result, error = client.complete(
            "command_chat",
            "You are Planix chat mode. Reply directly in the user's language. Do not plan, use tools, retrieve memory, or claim to change data.",
            user,
            max_tokens=4096,
            max_token_cap=4096,
            temperature=0.3,
            timeout_seconds=30,
            task_type="chat",
            record_run=False,
            routing_enabled=False,
        )
        yield _ndjson({"type": "thread", "threadId": thread_id})
        if error:
            detail = f" ({error.detail})" if error.detail else ""
            yield _ndjson({"type": "error", "error": f"{error.message}{detail}"})
            return
        if not result or not result.content.strip():
            yield _ndjson({"type": "error", "error": "The model returned an empty response."})
            return
        content = result.content.strip()
        yield _ndjson({"type": "assistant_delta", "text": content})
        yield _ndjson({"type": "done", "threadId": thread_id})

    def _stream_chat_with_model(self, payload: CommandChatRequest) -> Iterator[str]:
        thread_id = ""
        intent: CommandIntent = "normal_chat"
        decision: CommandDecision | None = None
        decision_source = ""
        decision_error = ""
        model_usage: ModelUsage | None = None
        goal_understanding: GoalUnderstandingOutcome | None = None
        try:
            thread_id = self.ensure_thread(payload.thread_id, title=payload.message)
            if payload.mode == "workbench":
                intent = resolve_command_intent(
                    payload.message,
                    detect_command_intent(payload.message),
                    has_current_draft=self._get_current_draft(thread_id) is not None,
                )
                yield from self._stream_chat_impl(
                    thread_id,
                    payload,
                    intent=intent,
                )
                return
            if payload.mode == "auto":
                if self._planning_session_followup_action(thread_id, payload):
                    yield from self._stream_chat_impl(
                        thread_id,
                        payload,
                        intent="planning_request",
                    )
                    return
                (
                    intent,
                    decision,
                    decision_source,
                    model_usage,
                    decision_error,
                    goal_understanding,
                ) = self._resolve_auto_decision(thread_id, payload)
            else:
                intent = resolve_command_intent(
                    payload.message,
                    detect_command_intent(payload.message),
                    has_current_draft=self._get_current_draft(thread_id) is not None,
                )
            yield from self._stream_chat_impl(
                thread_id,
                payload,
                intent=intent,
                decision=decision,
                decision_source=decision_source,
                decision_error=decision_error,
                model_usage=model_usage,
                goal_understanding=goal_understanding,
            )
        except AssertionError:
            raise
        except Exception as exc:
            yield from self._stream_failure(
                thread_id,
                _stream_failure_message(payload, intent),
                exc,
            )

    def _stream_chat_impl(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        intent: CommandIntent,
        decision: CommandDecision | None = None,
        decision_source: str = "",
        decision_error: str = "",
        model_usage: ModelUsage | None = None,
        goal_understanding: GoalUnderstandingOutcome | None = None,
    ) -> Iterator[str]:
        user_message = self.add_message(thread_id, "user", payload.message)
        yield _ndjson({"type": "thread", "threadId": thread_id})

        if goal_understanding and goal_understanding.result:
            yield from self._stream_goal_understanding(thread_id, goal_understanding)
        elif goal_understanding:
            goal_usage = _usage_payload(goal_understanding.usage)
            if goal_usage:
                diagnostic = {
                    "usage": goal_usage,
                    "feature": "goal_understanding",
                    "source": goal_understanding.source,
                    "error": goal_understanding.error,
                }
                self.add_message(thread_id, "card", "", kind="model_usage", payload=diagnostic)
                yield _ndjson({"type": "model_usage", **diagnostic})

        if payload.mode == "auto" and decision and intent != "planning_request":
            decision_card = _decision_payload(decision, decision_source or "local_fallback", decision_error)
            self.add_message(
                thread_id,
                "card",
                decision.decision_summary or decision.intent,
                kind="command_decision",
                payload=decision_card,
            )
            yield _ndjson({"type": "command_decision", **decision_card})
            usage_event = _usage_payload(model_usage)
            if usage_event:
                self.add_message(thread_id, "card", "", kind="model_usage", payload={"usage": usage_event})
                yield _ndjson({"type": "model_usage", "usage": usage_event})

        if payload.mode == "chat":
            content, usage = self._chat_locked_or_normal_with_usage(thread_id, payload, intent, exclude_message_id=user_message.id)
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            usage_event = _usage_payload(usage)
            if usage_event:
                self.add_message(thread_id, "card", "", kind="model_usage", payload={"usage": usage_event})
                yield _ndjson({"type": "model_usage", "usage": usage_event})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto":
            session_followup = self._planning_session_followup_action(thread_id, payload, intent)
            if session_followup:
                followup_action, session = session_followup
                yield from self._stream_planning_followup(thread_id, payload, followup_action, session)
                yield _ndjson({"type": "done", "threadId": thread_id})
                return

        if payload.mode == "workbench":
            yield from self._stream_runtime_handoff(thread_id, payload, exclude_message_id=user_message.id, auto_detail=True)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "planning_request":
            yield from self._stream_planning_start(
                thread_id,
                payload,
                goal_understanding=(
                    goal_understanding.result
                    if goal_understanding
                    and goal_understanding.result
                    and goal_understanding.result.intent_state == "clear_goal"
                    else None
                ),
            )
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "goal_understanding_unavailable":
            reply = _goal_understanding_unavailable_reply(payload.message, goal_understanding)
            self.add_message(thread_id, "assistant", reply)
            yield _ndjson({"type": "assistant_delta", "text": reply})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "clarify":
            if not goal_understanding:
                yield from self._stream_clarify(thread_id, decision)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "show_current_plan":
            yield from self._stream_plan_detail(thread_id)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent in {"regenerate_draft", "modify_current_draft"}:
            yield from self._stream_regenerate_draft(thread_id, payload, exclude_message_id=user_message.id)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "refine_current_plan":
            yield from self._stream_refine_current_plan(thread_id, payload)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "sync_to_calendar":
            yield from self._stream_calendar_write(thread_id, payload)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "query_plan":
            yield from self._stream_query_plan(thread_id, payload)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent in {"query_memory", "query_notes"}:
            yield from self._stream_query_memory(thread_id, payload, decision=decision)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent == "patch_calendar_plan":
            yield from self._stream_patch_calendar_plan(thread_id, payload, decision=decision)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent in {"save_memory", "save_note"}:
            yield from self._stream_save_memory(thread_id, payload, decision=decision)
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        if payload.mode == "auto" and intent not in {"normal_chat", "planning_request"}:
            content = _intent_reply(intent, payload.message)
        else:
            content, usage = self._llm_chat_with_usage(payload, thread_id=thread_id, exclude_message_id=user_message.id)
        self.add_message(thread_id, "assistant", content)
        yield _ndjson({"type": "assistant_delta", "text": content})
        if "usage" in locals():
            usage_event = _usage_payload(usage)
            if usage_event:
                self.add_message(thread_id, "card", "", kind="model_usage", payload={"usage": usage_event})
                yield _ndjson({"type": "model_usage", "usage": usage_event})
        yield _ndjson({"type": "done", "threadId": thread_id})

    def stream_approve(self, payload: CommandApproveRequest) -> Iterator[str]:
        thread_id = payload.thread_id or ""
        action = None
        try:
            action = self._load_action(payload.action_id)
            if action and not thread_id:
                thread_id = action["thread_id"]
            yield from self._stream_approve_impl(payload, action=action)
        except AssertionError:
            raise
        except Exception as exc:
            yield from self._stream_failure(
                thread_id,
                _approval_failure_message(action),
                exc,
            )

    def _stream_approve_impl(self, payload: CommandApproveRequest, *, action=None) -> Iterator[str]:
        action = action or self._load_action(payload.action_id)
        if not action:
            yield _ndjson({"type": "error", "error": "找不到待审批的操作。"})
            return
        thread_id = payload.thread_id or action["thread_id"]
        yield _ndjson({"type": "thread", "threadId": thread_id})

        decision = payload.decision
        if payload.approved is not None:
            decision = "approve" if payload.approved else "reject"
        self._record_approval(thread_id, payload.action_id, payload.permission, decision)

        if decision == "reject":
            self._update_action(payload.action_id, status="rejected", result={"decision": "reject"})
            if action["target"] in {"notes", "memory"}:
                content = "已取消保存笔记。"
                self.add_message(
                    thread_id,
                    "card",
                    content,
                    kind="execution_result",
                    payload={"status": "rejected", "actionId": payload.action_id},
                )
                yield _ndjson({"type": "execution_result", "status": "rejected", "text": content, "actionId": payload.action_id})
                yield _ndjson({"type": "done", "threadId": thread_id})
                return
            content = "已取消操作，未修改日历。" if action["operation"] in {"update", "delete"} else "已取消写入日历。"
            self.add_message(
                thread_id,
                "card",
                content,
                kind="execution_result",
                payload={"status": "rejected", "actionId": payload.action_id},
            )
            yield _ndjson({"type": "execution_result", "status": "rejected", "text": content, "actionId": payload.action_id})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        action_payload = _json_object(action["payload_json"])
        planning_session_id = str(action_payload.get("planningSessionId") or "")
        if action["target"] == "calendar" and planning_session_id:
            # Bind the human decision to the current Final Approval artifact. The
            # write path rechecks the same policy immediately before mutation.
            orchestrator = get_planning_orchestrator()
            approve_calendar = getattr(orchestrator, "approve_calendar_write", None)
            final_approval_ref = action_payload.get("finalApprovalRef")
            proposal_ref = action_payload.get("calendarProposalRef")
            if not self._planning_artifact_ref_is_current(
                planning_session_id,
                final_approval_ref,
                "final_approval_bundle",
            ) or not self._planning_artifact_ref_is_current(
                planning_session_id,
                proposal_ref,
                "calendar_proposal",
            ):
                raise RuntimeError("planning Calendar approval requires a versioned Harness action")
            if not callable(approve_calendar):
                raise RuntimeError("canonical planning Calendar approval requires Harness policy")
            approve_calendar(planning_session_id, final_approval_ref=final_approval_ref)

        if action["target"] in {"notes", "memory"}:
            result = self._execute_memory_action(payload.action_id, action)
            text = _memory_result_text(result)
            event_type = "note_write_result" if action["target"] == "notes" else "memory_write_result"
            self.add_message(thread_id, "card", text, kind=event_type, payload=result)
            yield _ndjson({"type": event_type, **result})
            yield _ndjson({"type": "execution_result", "status": "success" if result.get("status") == "success" else "failed", "text": text})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return

        result = self._execute_calendar_action(payload.action_id)
        if action["operation"] in {"update", "delete"}:
            text = _patch_result_text(result)
            self.add_message(thread_id, "card", text, kind="plan_patch_result", payload=result)
            yield _ndjson({"type": "plan_patch_result", **result})
            yield _ndjson({"type": "execution_result", "status": "success" if result.get("status") == "success" else "failed", "text": text})
            yield _ndjson({"type": "done", "threadId": thread_id})
            return
        text = _write_result_text(result)
        self.add_message(thread_id, "card", text, kind="calendar_write_result", payload=result)
        yield _ndjson({"type": "calendar_write_result", **result})
        yield _ndjson({"type": "execution_result", "status": "success" if not result.get("failed") else "failed", "text": text})
        yield _ndjson({"type": "done", "threadId": thread_id})

    def _stream_failure(self, thread_id: str, message: str, exc: Exception) -> Iterator[str]:
        if thread_id:
            try:
                self.add_message(thread_id, "assistant", message)
            except Exception:
                pass
        # The caller receives a stable user-facing category only. Raw exception
        # text can contain provider responses, local paths, or credentials and
        # belongs exclusively in server-side sanitized diagnostics.
        _ = exc
        yield _ndjson({"type": "error", "error": message})
        if thread_id:
            yield _ndjson({"type": "done", "threadId": thread_id})

    def _chat_locked_or_normal(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        intent: CommandIntent,
        *,
        exclude_message_id: str = "",
    ) -> str:
        locked = _chat_lock_reply(intent, payload.message)
        if locked:
            return locked
        return self._llm_chat(payload, thread_id=thread_id, exclude_message_id=exclude_message_id)

    def _chat_locked_or_normal_with_usage(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        intent: CommandIntent,
        *,
        exclude_message_id: str = "",
    ) -> tuple[str, ModelUsage | None]:
        locked = _chat_lock_reply(intent, payload.message)
        if locked:
            return locked, None
        return self._llm_chat_with_usage(payload, thread_id=thread_id, exclude_message_id=exclude_message_id)

    def _stream_clarify(self, thread_id: str, decision: CommandDecision | None = None) -> Iterator[str]:
        question = ""
        if decision and decision.clarification_question:
            question = decision.clarification_question
        if not question:
            question = "你想让我查计划、修改计划，还是做一个新规划？"
        payload = {
            "question": question,
            "decision": decision.model_dump(by_alias=True, exclude_none=True) if decision else None,
        }
        self.add_message(thread_id, "card", question, kind="clarify_question", payload=payload)
        yield _ndjson({"type": "clarify_question", **payload})

    def _stream_query_plan(self, thread_id: str, payload: CommandChatRequest) -> Iterator[str]:
        base_iso = _context_date(payload)
        calendar_plans, date_range = _search_calendar_plans(payload.message, base_iso)
        result = {
            "query": payload.message,
            "dateRange": date_range,
            "calendarPlans": calendar_plans,
            "materials": [],
            "goalHistory": [],
            "monthNotes": [],
        }
        result["summary"] = _query_result_text(result)
        self.add_message(thread_id, "card", result["summary"], kind="plan_search_results", payload=result)
        yield _ndjson({"type": "plan_search_results", **result})
        return
        terms = []
        material_query = " ".join(terms) or payload.message
        materials = []
        if terms or _contains_any(payload.message, ("资料", "材料", "文档", "笔记", "material", "note", "doc")):
            materials = [source.model_dump(by_alias=True) for source in RagService().retrieve(material_query, limit=4)]
        goal_history = []
        month_notes = []
        result = {
            "query": payload.message,
            "dateRange": date_range,
            "calendarPlans": calendar_plans,
            "materials": materials,
            "goalHistory": goal_history,
            "monthNotes": month_notes,
        }
        result["summary"] = _query_result_text(result)
        self.add_message(thread_id, "card", result["summary"], kind="plan_search_results", payload=result)
        yield _ndjson({"type": "plan_search_results", **result})

    def _stream_query_memory(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        decision: CommandDecision | None = None,
    ) -> Iterator[str]:
        params = decision.extracted_params if decision else None
        query = (params.query if params and params.query else "").strip()
        kinds = ["note"] if decision and decision.intent == "query_notes" else detect_query_kinds(payload.message)
        result_model = MemoryAgentService().search(
            payload.message,
            query=query or None,
            kinds=kinds,
        )
        result = result_model.model_dump(by_alias=True)
        self.add_message(thread_id, "card", result["summary"], kind="memory_search_results", payload=result)
        yield _ndjson({"type": "memory_search_results", **result})

    def _stream_query_notes(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        decision: CommandDecision | None = None,
    ) -> Iterator[str]:
        params = decision.extracted_params if decision else None
        query = (params.query if params and params.query else payload.message).strip()
        materials = [source.model_dump(by_alias=True) for source in RagService().retrieve(query, limit=6)]
        goal_history = _search_goal_history(query, limit=6)
        month_notes = _search_month_notes(query, limit=8)
        parts = []
        if materials:
            parts.append(f"资料 {len(materials)} 条")
        if goal_history:
            parts.append(f"历史规划 {len(goal_history)} 条")
        if month_notes:
            parts.append(f"月笔记 {len(month_notes)} 条")
        summary = "找到" + "、".join(parts) + "。" if parts else "没有找到匹配的笔记或资料。"
        result = {
            "query": query,
            "summary": summary,
            "materials": materials,
            "goalHistory": goal_history,
            "monthNotes": month_notes,
        }
        self.add_message(thread_id, "card", summary, kind="note_search_results", payload=result)
        yield _ndjson({"type": "note_search_results", **result})

    def _stream_save_memory(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        decision: CommandDecision | None = None,
    ) -> Iterator[str]:
        params = decision.extracted_params if decision else None
        note_text = ""
        title = ""
        explicit_kind = None
        if params:
            note_text = (params.note_text or params.query or "").strip()
            title = (params.title or "").strip()
        if decision and decision.intent == "save_note":
            explicit_kind = "note"
        memory_payload = MemoryAgentService().preview_create(
            payload.message,
            content=note_text or None,
            title=title or None,
            kind=explicit_kind,
        )
        if not memory_payload.content.strip() or memory_payload.content.strip() == payload.message.strip() and re.search(r"^(记录一条记忆|记录一条笔记|记录记忆|记录笔记)$", payload.message.strip()):
            question = "你想让 Planix 记住什么内容？可以直接说“记一下：……”"
            self.add_message(thread_id, "card", question, kind="clarify_question", payload={"question": question})
            yield _ndjson({"type": "clarify_question", "question": question})
            return

        action_id = self._create_memory_action(thread_id, memory_payload)
        preview = {
            "actionId": action_id,
            "operation": "create",
            "risk": "write",
            **memory_payload.model_dump(by_alias=True),
        }
        self.add_message(thread_id, "card", "准备记录到记忆库", kind="memory_write_preview", payload=preview)
        yield _ndjson({"type": "memory_write_preview", **preview})

        if command_action_requires_approval(payload.permission, "write"):
            self._update_action(action_id, status="waiting_approval")
            self._record_approval(thread_id, action_id, payload.permission, "pending")
            summary = "准备记录到记忆库，需要确认。"
            approval = {
                "actionId": action_id,
                "draftId": "",
                "permission": payload.permission,
                "risk": "write",
                "target": "memory",
                "operation": "create",
                "summary": summary,
            }
            self.add_message(thread_id, "card", summary, kind="approval_request", payload=approval)
            yield _ndjson({"type": "approval_required", **approval})
            return

        result = self._execute_memory_action(action_id)
        text = _memory_result_text(result)
        self.add_message(thread_id, "card", text, kind="memory_write_result", payload=result)
        yield _ndjson({"type": "memory_write_result", **result})

    def _stream_save_note(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        decision: CommandDecision | None = None,
    ) -> Iterator[str]:
        params = decision.extracted_params if decision else None
        note_text = ""
        if params:
            note_text = (params.note_text or params.query or params.title or "").strip()
        note_text = note_text or _note_text_from_message(payload.message)
        if not note_text:
            question = "你想保存哪段内容到笔记？"
            self.add_message(thread_id, "card", question, kind="clarify_question", payload={"question": question})
            yield _ndjson({"type": "clarify_question", "question": question})
            return

        base_iso = _context_date(payload)
        note_date = params.date if params and isinstance(params.date, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", params.date) else base_iso
        target = _base_date(note_date)
        current = get_month_note(target.year, target.month)
        before = current.content or ""
        entry = f"{note_date} {note_text.strip()}"
        after = f"{before.rstrip()}\n{entry}".strip() if before.strip() else entry
        operation = "update" if before.strip() else "create"
        action_id = self._create_note_action(
            thread_id,
            operation,
            target.year,
            target.month,
            note_date,
            note_text,
            before,
            after,
        )
        preview = {
            "actionId": action_id,
            "operation": operation,
            "risk": "write",
            "year": target.year,
            "month": target.month,
            "date": note_date,
            "noteText": note_text,
            "before": before,
            "after": after,
        }
        self.add_message(thread_id, "card", "准备保存笔记", kind="note_write_preview", payload=preview)
        yield _ndjson({"type": "note_write_preview", **preview})

        if command_action_requires_approval(payload.permission, "write"):
            self._update_action(action_id, status="waiting_approval")
            self._record_approval(thread_id, action_id, payload.permission, "pending")
            summary = "准备保存到月笔记，需要确认。"
            approval = {
                "actionId": action_id,
                "draftId": "",
                "permission": payload.permission,
                "risk": "write",
                "target": "notes",
                "operation": operation,
                "summary": summary,
            }
            self.add_message(thread_id, "card", summary, kind="approval_request", payload=approval)
            yield _ndjson({"type": "approval_required", **approval})
            return

        result = self._execute_note_action(action_id)
        text = _note_result_text(result)
        self.add_message(thread_id, "card", text, kind="note_write_result", payload=result)
        yield _ndjson({"type": "note_write_result", **result})

    def _last_plan_search_results(self, thread_id: str) -> list[dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM command_messages
                WHERE thread_id = ? AND kind = 'plan_search_results'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        if not row:
            return []
        payload = _json_object(row["payload_json"])
        plans = payload.get("calendarPlans")
        return [plan for plan in plans if _is_record(plan)] if isinstance(plans, list) else []

    def _load_plan_payload(self, plan_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return _plan_card_from_row(row) if row else None

    def _select_patch_candidates(self, thread_id: str, message: str, base_iso: str) -> list[dict[str, Any]]:
        ordinal = _extract_ordinal(message)
        last_results = self._last_plan_search_results(thread_id)
        if ordinal and 1 <= ordinal <= len(last_results):
            plan = self._load_plan_payload(str(last_results[ordinal - 1].get("id") or ""))
            return [plan] if plan else []
        if not ordinal and len(last_results) == 1 and not _first_date_expression(message, base_iso):
            plan = self._load_plan_payload(str(last_results[0].get("id") or ""))
            return [plan] if plan else []

        source_date = _first_date_expression(message, base_iso)
        terms = _query_terms(message)
        where = []
        params: list[Any] = []
        if source_date:
            where.append("date = ?")
            params.append(source_date[0].isoformat())
        sql = "SELECT * FROM plans"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date ASC, time ASC, created_at ASC LIMIT 100"
        with get_conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        candidates = [_plan_card_from_row(row) for row in rows]
        if terms:
            candidates = [plan for plan in candidates if _plan_matches_terms(plan, terms)]
        return candidates[:12]

    def _select_patch_candidates_from_decision(
        self,
        thread_id: str,
        decision: CommandDecision,
    ) -> list[dict[str, Any]]:
        params = decision.extracted_params
        if params.target_index:
            last_results = self._last_plan_search_results(thread_id)
            if 1 <= params.target_index <= len(last_results):
                plan = self._load_plan_payload(str(last_results[params.target_index - 1].get("id") or ""))
                return [plan] if plan else []

        date_range = params.date_range
        where = []
        sql_params: list[Any] = []
        if date_range and date_range.start and date_range.end:
            where.append("date >= ? AND date <= ?")
            sql_params.extend([date_range.start, date_range.end])
        elif params.date:
            where.append("date = ?")
            sql_params.append(params.date)
        else:
            return []
        sql = "SELECT * FROM plans"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date ASC, time ASC, created_at ASC LIMIT 100"
        with get_conn() as conn:
            rows = conn.execute(sql, tuple(sql_params)).fetchall()
        candidates = [_plan_card_from_row(row) for row in rows]
        terms = _query_terms(params.query or params.title or "")
        if terms:
            candidates = [plan for plan in candidates if _plan_matches_terms(plan, terms)]
        return candidates[:12]

    def _patch_changes_from_decision(self, decision: CommandDecision | None) -> dict[str, Any]:
        if not decision:
            return {}
        params = decision.extracted_params
        patch = params.patch_fields
        changes: dict[str, Any] = {}
        if patch:
            if patch.date:
                changes["date"] = patch.date
            if patch.time:
                changes["time"] = patch.time
            if patch.estimated_minutes:
                changes["estimatedMinutes"] = patch.estimated_minutes
            if patch.title:
                changes["content"] = patch.title
        if params.time and "time" not in changes:
            changes["time"] = params.time
        if params.estimated_minutes and "estimatedMinutes" not in changes:
            changes["estimatedMinutes"] = params.estimated_minutes
        return changes

    def _stream_patch_calendar_plan(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        decision: CommandDecision | None = None,
    ) -> Iterator[str]:
        base_iso = _context_date(payload)
        candidates = self._select_patch_candidates_from_decision(thread_id, decision) if decision else []
        if not candidates:
            candidates = self._select_patch_candidates(thread_id, payload.message, base_iso)
        if not candidates:
            if _looks_like_generic_calendar_patch(payload.message):
                question = "你想修改哪一个计划？可以先说“查看今天计划”，再告诉我修改第几个。"
                self.add_message(thread_id, "card", question, kind="clarify_question", payload={"question": question})
                yield _ndjson({"type": "clarify_question", "question": question})
                return
            content = "没有找到要修改的日历计划。你可以先问“今天有什么安排”，再说“把第一个改到后天”。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        if len(candidates) > 1:
            result = {
                "query": payload.message,
                "dateRange": _date_range_from_text(payload.message, base_iso),
                "calendarPlans": candidates,
                "materials": [],
                "goalHistory": [],
                "monthNotes": [],
                "summary": "找到多个可能的计划，请说明要修改第几个。",
            }
            self.add_message(thread_id, "card", result["summary"], kind="plan_search_results", payload=result)
            yield _ndjson({"type": "plan_search_results", **result})
            return

        before = candidates[0]
        operation = "delete" if (decision and decision.action == "delete") or _is_delete_request(payload.message) else "update"
        changes = {} if operation == "delete" else {**_patch_changes_from_message(payload.message, base_iso), **self._patch_changes_from_decision(decision)}
        if operation == "update" and not changes:
            content = "我找到了计划，但没有识别到要改的字段。第一版支持改标题、日期、时间和预计时长。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        after = {**before, **changes}
        action_id = self._create_calendar_patch_action(thread_id, operation, before, after, changes)
        risk = "delete" if operation == "delete" else "write"
        preview = {
            "actionId": action_id,
            "operation": operation,
            "risk": risk,
            "before": before,
            "after": after,
            "changes": changes,
        }
        self.add_message(thread_id, "card", "准备修改日历计划", kind="plan_patch_preview", payload=preview)
        yield _ndjson({"type": "plan_patch_preview", **preview})

        # Calendar updates and deletes are human-approved regardless of the
        # generic command permission level.
        requires_approval = True
        if requires_approval:
            self._update_action(action_id, status="waiting_approval")
            self._record_approval(thread_id, action_id, payload.permission, "pending")
            summary = "准备删除日历计划，需要确认。" if operation == "delete" else "准备修改日历计划，需要确认。"
            approval = {
                "actionId": action_id,
                "draftId": "",
                "permission": payload.permission,
                "risk": risk,
                "target": "calendar",
                "operation": operation,
                "summary": summary,
            }
            self.add_message(thread_id, "card", summary, kind="approval_request", payload=approval)
            yield _ndjson({"type": "approval_required", **approval})
            return

        result = self._execute_calendar_action(action_id)
        text = _patch_result_text(result)
        self.add_message(thread_id, "card", text, kind="plan_patch_result", payload=result)
        yield _ndjson({"type": "plan_patch_result", **result})

    def _current_draft(self, conn, thread_id: str) -> CommandDraftOut | None:
        return _row_to_draft(
            conn.execute(
                """
                SELECT * FROM command_drafts
                WHERE thread_id = ? AND kind = 'calendar_plan' AND status = 'current'
                ORDER BY version DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        )

    def _get_current_draft(self, thread_id: str) -> CommandDraftOut | None:
        with get_conn() as conn:
            return self._current_draft(conn, thread_id)

    def _stream_plan_detail(self, thread_id: str) -> Iterator[str]:
        draft = self._get_current_draft(thread_id)
        if not draft:
            content = "当前线程还没有可展开的计划草稿。你可以先说“帮我规划本周安排”。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        structured_plan = draft.payload.get("structuredPlan")
        if not _valid_structured_plan(structured_plan):
            content = "当前计划草稿结构不完整，无法展开。请重新生成一个计划草稿。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        payload = {
            "draftId": draft.id,
            "version": draft.version,
            "title": draft.title,
            "structuredPlan": structured_plan,
            "planHorizon": draft.payload.get("planHorizon") if isinstance(draft.payload.get("planHorizon"), dict) else None,
            "qualityReport": draft.payload.get("qualityReport") if isinstance(draft.payload.get("qualityReport"), dict) else None,
            "qualityStatus": draft.payload.get("qualityStatus"),
            "sourceType": draft.payload.get("sourceType"),
            "localRelevance": draft.payload.get("localRelevance"),
        }
        self.add_message(thread_id, "card", draft.title, kind="plan_detail", payload=payload)
        yield _ndjson({"type": "plan_detail", **payload})

    def _stream_regenerate_draft(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        exclude_message_id: str = "",
    ) -> Iterator[str]:
        draft = self._get_current_draft(thread_id)
        if not draft:
            content = "当前线程还没有可重新生成的计划草稿。你可以先说“帮我规划本周安排”。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        structured_plan = draft.payload.get("structuredPlan")
        if not _valid_structured_plan(structured_plan):
            content = "当前计划草稿结构不完整，无法继续修改。请先重新生成一个计划草稿。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        instruction = (
            f"请基于当前计划草稿重新生成一个完整计划。\n\n"
            f"用户新要求：{payload.message}\n\n"
            f"当前计划草稿 JSON：\n{json.dumps(structured_plan, ensure_ascii=False)}"
        )
        regenerate_payload = payload.model_copy(update={"message": instruction})
        yield from self._stream_runtime_handoff(
            thread_id,
            regenerate_payload,
            exclude_message_id=exclude_message_id,
            auto_detail=True,
        )

    def _draft_task_records(self, draft: CommandDraftOut) -> list[dict[str, Any]]:
        structured_plan = draft.payload.get("structuredPlan")
        if not _valid_structured_plan(structured_plan):
            return []
        records: list[dict[str, Any]] = []
        for milestone_index, milestone in enumerate(structured_plan.get("milestones") or []):
            if not _is_record(milestone):
                continue
            milestone_title = str(milestone.get("title") or "").strip()
            for task_index, task in enumerate(milestone.get("tasks") or []):
                if not _is_record(task):
                    continue
                title = str(task.get("title") or "").strip()
                if not title:
                    continue
                records.append({
                    "key": _task_key(milestone_index, task_index),
                    "milestoneIndex": milestone_index,
                    "taskIndex": task_index,
                    "milestoneTitle": milestone_title,
                    "title": title,
                    "description": str(task.get("description") or "").strip(),
                    "dueDate": task.get("dueDate"),
                    "estimatedMinutes": task.get("estimatedMinutes"),
                    "priority": task.get("priority"),
                })
        return records

    def _selected_refine_tasks(self, draft: CommandDraftOut, message: str) -> list[dict[str, Any]]:
        records = self._draft_task_records(draft)
        if not records:
            return []
        text = message.strip().lower()
        if re.search(r"(全部|所有|一键|all|every)", text):
            first_milestone = records[0]["milestoneIndex"]
            return [record for record in records if record["milestoneIndex"] == first_milestone][:5]

        number_match = re.search(r"(?:第|task\s*)?(\d+)(?:\s*(?:个|项|条|号|task))?", text)
        if number_match:
            index = int(number_match.group(1)) - 1
            if 0 <= index < len(records):
                return [records[index]]

        matched = []
        for record in records:
            title = str(record["title"]).strip()
            if title and title.lower() in text:
                matched.append(record)
        return (matched[:5] if matched else records[:1])

    def _save_draft_refinements(
        self,
        draft: CommandDraftOut,
        refinements: dict[str, dict[str, Any]],
    ) -> None:
        payload = dict(draft.payload)
        existing = payload.get("refinements")
        if not isinstance(existing, dict):
            existing = {}
        existing.update(refinements)
        payload["refinements"] = existing
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE command_drafts
                SET payload_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), _now(), draft.id),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (_now(), draft.thread_id))

    def _stream_refine_current_plan(self, thread_id: str, payload: CommandChatRequest) -> Iterator[str]:
        draft = self._get_current_draft(thread_id)
        if not draft:
            content = "当前线程还没有可细化的计划草稿。你可以先说“帮我做个规划”。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        tasks = self._selected_refine_tasks(draft, payload.message)
        if not tasks:
            content = "当前计划草稿里没有可细化的任务。请重新生成计划后再试。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        yield _ndjson({"type": "refinement_started", "draftId": draft.id, "total": len(tasks)})
        service = PlanningService()
        output_language = "en" if _looks_english(payload.message) else "zh"
        new_refinements: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        goal = str(draft.payload.get("goal") or draft.title)

        for record in tasks:
            key = str(record["key"])
            try:
                refined = service.refine_task(
                    RefineTaskRequest(
                        goal=goal,
                        taskTitle=str(record["title"]),
                        taskDescription="\n".join(
                            value for value in [
                                f"Milestone: {record.get('milestoneTitle')}",
                                str(record.get("description") or ""),
                            ] if value
                        ),
                        date=_normalize_task_date(record.get("dueDate"), _today()),
                        availableMinutes=_task_minutes(record.get("estimatedMinutes")),
                        planContext=build_refine_plan_context(
                            draft.payload.get("structuredPlan"),
                            milestone_index=int(record["milestoneIndex"]),
                            task_index=int(record["taskIndex"]),
                            sources=draft.payload.get("sources") if isinstance(draft.payload.get("sources"), list) else [],
                            plan_horizon=draft.payload.get("planHorizon") if isinstance(draft.payload.get("planHorizon"), dict) else None,
                            quality_status=str(draft.payload.get("qualityStatus") or ""),
                            daily_learning_minutes=_task_minutes(record.get("estimatedMinutes")),
                        ),
                        retrievedSources=draft.payload.get("sources") if isinstance(draft.payload.get("sources"), list) else [],
                        outputLanguage=output_language,
                        refinementInstruction=payload.message,
                    )
                )
                refined_payload = refined.model_dump(by_alias=True)
                new_refinements[key] = refined_payload
                items.append({
                    "taskKey": key,
                    "taskTitle": record["title"],
                    "milestoneTitle": record.get("milestoneTitle") or "",
                    "milestoneIndex": record["milestoneIndex"],
                    "taskIndex": record["taskIndex"],
                    "refinedTask": refined_payload,
                })
            except Exception as exc:
                errors.append({"taskKey": key, "taskTitle": str(record["title"]), "error": str(exc)})

        if new_refinements:
            self._save_draft_refinements(draft, new_refinements)

        result = {
            "draftId": draft.id,
            "total": len(tasks),
            "succeeded": len(items),
            "failed": len(errors),
            "items": items,
            "errors": errors,
        }
        content = f"已细化 {len(items)} 个任务，失败 {len(errors)} 个。"
        self.add_message(thread_id, "card", content, kind="refined_tasks_result", payload=result)
        yield _ndjson({"type": "refined_tasks_result", **result})

    def _stream_calendar_write(self, thread_id: str, payload: CommandChatRequest) -> Iterator[str]:
        try:
            yield from self._stream_calendar_write_impl(thread_id, payload)
        except AssertionError:
            raise
        except Exception as exc:
            yield from self._stream_failure(thread_id, CALENDAR_WRITE_ERROR_MESSAGE, exc)

    def _stream_calendar_write_impl(self, thread_id: str, payload: CommandChatRequest) -> Iterator[str]:
        draft = self._get_current_draft(thread_id)
        if not draft:
            content = "当前线程还没有可写入日历的计划草稿。你可以先生成一个计划草稿。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return
        items = self._calendar_items_from_draft(draft)
        if not items:
            content = "当前计划草稿里没有可写入日历的任务。请重新生成计划后再试。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        action_id = self._create_calendar_action(thread_id, draft, items)
        preview = {
            "actionId": action_id,
            "draftId": draft.id,
            "title": draft.title,
            "plans": items,
        }
        self.add_message(thread_id, "card", "准备写入日历", kind="calendar_plan_preview", payload=preview)
        yield _ndjson({"type": "calendar_plan_preview", **preview})

        # Calendar mutations always require an explicit human approval. Generic
        # low/medium/high permission may add safeguards, but cannot bypass this
        # final gate.
        self._update_action(action_id, status="waiting_approval")
        self._record_approval(thread_id, action_id, payload.permission, "pending")
        approval = {
            "actionId": action_id,
            "draftId": draft.id,
            "permission": payload.permission,
            "risk": "write",
            "target": "calendar",
            "operation": "create_or_update_plans",
            "summary": f"准备写入 {len(items)} 个日历计划，需要确认。",
        }
        self.add_message(thread_id, "card", approval["summary"], kind="approval_request", payload=approval)
        yield _ndjson({"type": "approval_required", **approval})
        return

    def _stream_runtime_handoff(
        self,
        thread_id: str,
        payload: CommandChatRequest,
        *,
        exclude_message_id: str = "",
        auto_detail: bool = False,
    ) -> Iterator[str]:
        yield _ndjson({"type": "runtime_started", "message": "开始运行 Dashboard Runtime"})
        context = payload.context if isinstance(payload.context, dict) else {}
        preferences = context.get("preferences", "")
        if not isinstance(preferences, (str, dict)):
            preferences = ""
        materials = context.get("materials", "")
        if not isinstance(materials, str):
            materials = ""
        data = context.get("data", {})
        if not isinstance(data, dict):
            data = {}
        thread_context = self._thread_context_summary(thread_id, exclude_message_id=exclude_message_id)
        runtime_input = _runtime_input_with_thread_context(payload.message, thread_context)
        runtime_payload = AgentRunRequest(
            input=runtime_input,
            date=_context_date(payload),
            preferences=preferences,
            materials=materials,
            data=data,
        )

        proposal_output: dict[str, Any] | None = None
        proposal_input: dict[str, Any] = {}
        source_run_id = ""
        runtime_failed = ""
        seen_tools: set[tuple[str, str]] = set()

        try:
            for chunk in RuntimeOrchestrator().run(runtime_payload):
                for line in str(chunk).splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event.get("runId"), str):
                        source_run_id = event["runId"]
                    event_type = event.get("type")
                    if event_type == "node" and isinstance(event.get("title"), str) and event.get("title") in KEY_RUNTIME_TOOLS:
                        name = str(event["title"])
                        key = (name, "running")
                        if key not in seen_tools:
                            seen_tools.add(key)
                            yield _ndjson({
                                "type": "runtime_event",
                                "name": name,
                                "status": "running",
                                "summary": _runtime_tool_summary(name),
                            })
                    if event_type == "tool":
                        tool_call = event.get("toolCall")
                        if isinstance(tool_call, dict):
                            name = str(tool_call.get("name") or "")
                            if name in KEY_RUNTIME_TOOLS:
                                yield _ndjson({
                                    "type": "runtime_event",
                                    "name": name,
                                    "status": "success",
                                    "summary": _runtime_tool_summary(name),
                                })
                            if name == "propose_tasks" and isinstance(tool_call.get("output"), dict):
                                proposal_output = tool_call["output"]
                                if isinstance(tool_call.get("input"), dict):
                                    proposal_input = tool_call["input"]
                    if event_type == "error":
                        runtime_failed = str(event.get("error") or "Runtime failed")
                        yield _ndjson({"type": "runtime_event", "name": "runtime", "status": "error", "summary": runtime_failed})
        except AssertionError:
            raise
        except Exception as exc:
            runtime_failed = f"Runtime handoff failed: {exc}"

        if runtime_failed:
            content = f"计划草稿生成失败：{runtime_failed}。没有创建 draft，也没有写入日历。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        if not proposal_output or not _valid_structured_plan(proposal_output.get("structuredPlan")):
            content = "计划草稿生成失败：Runtime 没有返回合法的 structuredPlan。没有创建 draft，也没有写入日历。"
            self.add_message(thread_id, "assistant", content)
            yield _ndjson({"type": "assistant_delta", "text": content})
            return

        structured_plan = proposal_output["structuredPlan"]
        title = _plan_title(structured_plan, proposal_input.get("goal") or payload.message)
        plan_horizon = proposal_output.get("planHorizon") if isinstance(proposal_output.get("planHorizon"), dict) else None
        quality_report = proposal_output.get("qualityReport") if isinstance(proposal_output.get("qualityReport"), dict) else None
        summary = _compact_summary(
            structured_plan,
            title=title,
            quality_status=str(proposal_output.get("qualityStatus") or ""),
            plan_horizon=plan_horizon,
        )
        draft = self._create_calendar_draft(
            thread_id=thread_id,
            title=title,
            summary=summary,
            source_run_id=source_run_id,
            payload={
                "structuredPlan": structured_plan,
                "runtimeRunId": source_run_id,
                "goal": proposal_input.get("goal") or payload.message,
                "threadContextSummary": thread_context,
                "tasks": proposal_output.get("tasks") if isinstance(proposal_output.get("tasks"), list) else [],
                "sources": proposal_output.get("sources") if isinstance(proposal_output.get("sources"), list) else [],
                "mode": proposal_output.get("mode") or "local_fallback",
                "fallbackReason": proposal_output.get("fallbackReason"),
                "errorType": proposal_output.get("errorType"),
                "baseUrlHost": proposal_output.get("baseUrlHost"),
                "planHorizon": plan_horizon,
                "qualityReport": quality_report,
                "qualityStatus": proposal_output.get("qualityStatus"),
                "sourceType": proposal_output.get("sourceType"),
                "localRelevance": proposal_output.get("localRelevance"),
                "summary": summary,
            },
        )
        MemoryService().create_memory(
            MemoryCreate(
                kind="planning_history",
                title=title,
                content=json.dumps(structured_plan, ensure_ascii=False, indent=2),
                summary=summary,
                source="ai",
                sourceId=draft.id,
                sourceKey=f"command-draft:{draft.id}",
                metadata={
                    "structuredPlan": structured_plan,
                    "mode": proposal_output.get("mode") or "local_fallback",
                    "fallbackReason": proposal_output.get("fallbackReason"),
                    "errorType": proposal_output.get("errorType"),
                    "source": "p_mode",
                    "runtimeRunId": source_run_id,
                },
            )
        )
        self.add_message(thread_id, "card", summary, kind="summary", payload={"draftId": draft.id, "text": summary})
        yield _ndjson({"type": "draft_created", "draftId": draft.id, "kind": draft.kind, "version": draft.version})
        yield _ndjson({"type": "summary", "text": summary, "draftId": draft.id})
        if auto_detail:
            yield from self._stream_plan_detail(thread_id)
        model_usage = proposal_output.get("modelUsage") if isinstance(proposal_output.get("modelUsage"), dict) else None
        if model_usage:
            self.add_message(thread_id, "card", "", kind="model_usage", payload={"usage": model_usage})
            yield _ndjson({"type": "model_usage", "usage": model_usage})

    def _create_calendar_draft(
        self,
        *,
        thread_id: str,
        title: str,
        summary: str,
        payload: dict[str, Any],
        source_run_id: str,
    ) -> CommandDraftOut:
        draft_id = str(uuid4())
        now = _now()
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM command_drafts
                WHERE thread_id = ? AND kind = 'calendar_plan'
                """,
                (thread_id,),
            ).fetchone()
            version = int(row["next_version"] or 1)
            conn.execute(
                """
                UPDATE command_drafts
                SET status = 'superseded', updated_at = ?
                WHERE thread_id = ? AND kind = 'calendar_plan' AND status = 'current'
                """,
                (now, thread_id),
            )
            conn.execute(
                """
                INSERT INTO command_drafts(
                  id, thread_id, kind, version, status, title, summary, payload_json,
                  source_run_id, created_at, updated_at
                )
                VALUES (?, ?, 'calendar_plan', ?, 'current', ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    thread_id,
                    version,
                    title[:200],
                    summary[:4000],
                    json.dumps(payload, ensure_ascii=False),
                    source_run_id,
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
            draft_row = conn.execute("SELECT * FROM command_drafts WHERE id = ?", (draft_id,)).fetchone()
        draft = _row_to_draft(draft_row)
        if draft is None:
            raise RuntimeError("Failed to create command draft")
        return draft

    def _calendar_items_from_draft(self, draft: CommandDraftOut) -> list[dict[str, Any]]:
        structured_plan = draft.payload.get("structuredPlan")
        if not _valid_structured_plan(structured_plan):
            return []
        refinements = draft.payload.get("refinements")
        if not isinstance(refinements, dict):
            refinements = {}
        items: list[dict[str, Any]] = []
        sequence = 0
        for milestone_index, milestone in enumerate(structured_plan.get("milestones") or []):
            if not _is_record(milestone):
                continue
            for task_index, task in enumerate(milestone.get("tasks") or []):
                if not _is_record(task):
                    continue
                title = str(task.get("title") or "").strip()
                if not title:
                    continue
                target_date = _normalize_task_date(task.get("dueDate"), _today())
                task_key = _task_key(milestone_index, task_index)
                task_source_key = str(task.get("sourceKey") or "").strip()
                source_key = task_source_key or f"command-draft:{draft.id}:m{milestone_index}:t{task_index}"
                item = {
                    "title": title,
                    "date": target_date,
                    "time": _default_task_time(sequence),
                    "estimatedMinutes": _task_minutes(task.get("estimatedMinutes")),
                    "priority": _task_priority(task.get("priority")),
                    "sourceKey": source_key,
                    "taskKey": task_key,
                    "milestoneTitle": str(milestone.get("title") or ""),
                    "description": str(task.get("description") or ""),
                }
                refined_task = refinements.get(task_key)
                if isinstance(refined_task, dict):
                    item["refinedTask"] = refined_task
                items.append(item)
                sequence += 1
        return items

    def _create_calendar_action(self, thread_id: str, draft: CommandDraftOut, items: list[dict[str, Any]]) -> str:
        action_id = str(uuid4())
        now = _now()
        payload = {"draftId": draft.id, "plans": items}
        if draft.payload.get("planningSessionId"):
            payload["planningSessionId"] = draft.payload.get("planningSessionId")
            payload["finalApprovalRef"] = draft.payload.get("finalApprovalRef")
            payload["calendarProposalRef"] = draft.payload.get("calendarProposalRef")
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_actions(
                  id, thread_id, draft_id, target, operation, risk, status, reason,
                  payload_json, result_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'calendar', 'create_or_update_plans', 'write', 'proposed', ?, ?, '{}', '', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    draft.id,
                    f"Write {len(items)} draft tasks to Calendar",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        return action_id

    def _create_note_action(
        self,
        thread_id: str,
        operation: str,
        year: int,
        month: int,
        note_date: str,
        note_text: str,
        before: str,
        after: str,
    ) -> str:
        action_id = str(uuid4())
        anchor_draft_id = str(uuid4())
        now = _now()
        payload = {
            "year": year,
            "month": month,
            "date": note_date,
            "noteText": note_text,
            "before": before,
            "after": after,
        }
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_drafts(
                  id, thread_id, kind, version, status, title, summary,
                  payload_json, source_run_id, created_at, updated_at
                )
                VALUES (?, ?, 'calendar_plan', 0, 'dismissed', ?, ?, ?, '', ?, ?)
                """,
                (
                    anchor_draft_id,
                    thread_id,
                    "Note write action",
                    f"save note to {year}-{month:02d}",
                    json.dumps({"kind": "note_write_anchor", "year": year, "month": month}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO command_actions(
                  id, thread_id, draft_id, target, operation, risk, status, reason,
                  payload_json, result_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'notes', ?, 'write', 'proposed', ?, ?, '{}', '', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    anchor_draft_id,
                    operation,
                    f"Save note to {year}-{month:02d}",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        return action_id

    def _create_memory_action(self, thread_id: str, memory: MemoryCreate) -> str:
        action_id = str(uuid4())
        anchor_draft_id = str(uuid4())
        now = _now()
        payload = memory.model_dump(by_alias=True)
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_drafts(
                  id, thread_id, kind, version, status, title, summary,
                  payload_json, source_run_id, created_at, updated_at
                )
                VALUES (?, ?, 'calendar_plan', 0, 'dismissed', ?, ?, ?, '', ?, ?)
                """,
                (
                    anchor_draft_id,
                    thread_id,
                    "Memory write action",
                    f"save {memory.kind} memory",
                    json.dumps({"kind": "memory_write_anchor", "memoryKind": memory.kind}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO command_actions(
                  id, thread_id, draft_id, target, operation, risk, status, reason,
                  payload_json, result_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'memory', 'create', 'write', 'proposed', ?, ?, '{}', '', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    anchor_draft_id,
                    f"Save {memory.kind} memory",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        return action_id

    def _create_calendar_patch_action(
        self,
        thread_id: str,
        operation: str,
        before: dict[str, Any],
        after: dict[str, Any],
        changes: dict[str, Any],
    ) -> str:
        action_id = str(uuid4())
        anchor_draft_id = str(uuid4())
        now = _now()
        risk = "delete" if operation == "delete" else "write"
        payload = {
            "planId": before.get("id"),
            "operation": operation,
            "before": before,
            "after": after,
            "changes": changes,
        }
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_drafts(
                  id, thread_id, kind, version, status, title, summary,
                  payload_json, source_run_id, created_at, updated_at
                )
                VALUES (?, ?, 'calendar_plan', 0, 'dismissed', ?, ?, ?, '', ?, ?)
                """,
                (
                    anchor_draft_id,
                    thread_id,
                    "Calendar patch action",
                    f"{operation} calendar plan",
                    json.dumps({"kind": "calendar_patch_anchor", "planId": before.get("id")}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO command_actions(
                  id, thread_id, draft_id, target, operation, risk, status, reason,
                  payload_json, result_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'calendar', ?, ?, 'proposed', ?, ?, '{}', '', ?, ?)
                """,
                (
                    action_id,
                    thread_id,
                    anchor_draft_id,
                    operation,
                    risk,
                    f"{operation} calendar plan {before.get('id') or ''}",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute("UPDATE command_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        return action_id

    def _load_action(self, action_id: str):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM command_actions WHERE id = ?", (action_id,)).fetchone()

    def _update_action(
        self,
        action_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE command_actions
                SET status = ?,
                    result_json = COALESCE(?, result_json),
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    _now(),
                    action_id,
                ),
            )

    def _record_approval(self, thread_id: str, action_id: str, permission: CommandPermission, decision: str) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO command_approvals(id, thread_id, action_id, permission, decision, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), thread_id, action_id, permission, decision, _now()),
            )

    def _calendar_action_is_approved(self, action_id: str) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT decision
                FROM command_approvals
                WHERE action_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (action_id,),
            ).fetchone()
        return bool(row and row["decision"] == "approve")

    def _planning_artifact_ref_is_current(
        self,
        session_id: str,
        raw_ref: Any,
        expected_kind: str,
    ) -> bool:
        if not _is_record(raw_ref):
            return False
        kind = str(raw_ref.get("kind") or "")
        if kind != expected_kind:
            return False
        try:
            expected_version = int(raw_ref.get("version") or 0)
        except (TypeError, ValueError):
            return False
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, version
                FROM planning_artifacts
                WHERE session_id = ? AND artifact_type = ?
                ORDER BY version DESC, created_at DESC, id DESC
                LIMIT 1
                """,
                (session_id, kind),
            ).fetchone()
        return bool(
            row
            and str(raw_ref.get("sessionId") or "") == session_id
            and str(raw_ref.get("id") or "") == row["id"]
            and expected_version == int(row["version"] or 0)
        )

    def _execute_calendar_patch_action(self, action_id: str, action) -> dict[str, Any]:
        self._update_action(action_id, status="running")
        payload = _json_object(action["payload_json"])
        operation = str(payload.get("operation") or action["operation"])
        plan_id = str(payload.get("planId") or "")
        before = payload.get("before") if _is_record(payload.get("before")) else {}
        after = payload.get("after") if _is_record(payload.get("after")) else {}
        changes = payload.get("changes") if _is_record(payload.get("changes")) else {}
        try:
            if operation == "delete":
                delete_plan(plan_id)
                result = {
                    "actionId": action_id,
                    "operation": "delete",
                    "status": "success",
                    "before": before,
                    "after": None,
                    "changes": {},
                }
            else:
                safe_changes: dict[str, Any] = {}
                for key in ("date", "time", "content", "estimatedMinutes"):
                    if key in changes:
                        safe_changes[key] = changes[key]
                if not safe_changes:
                    raise ValueError("no supported plan changes")
                updated = update_plan(plan_id, PlanUpdate(**safe_changes))
                result = {
                    "actionId": action_id,
                    "operation": "update",
                    "status": "success",
                    "before": before,
                    "after": {
                        "id": updated.id,
                        "date": updated.date,
                        "time": updated.time,
                        "title": updated.content,
                        "done": updated.done,
                        "completion": updated.result,
                        "priority": updated.priority,
                        "estimatedMinutes": updated.estimated_minutes,
                        "source": updated.source,
                        "sourceKey": updated.source_key,
                        "createdAt": updated.created_at,
                        "updatedAt": updated.updated_at,
                    },
                    "changes": safe_changes,
                }
            self._update_action(action_id, status="success", result=result)
            return result
        except Exception as exc:
            result = {
                "actionId": action_id,
                "operation": operation,
                "status": "failed",
                "before": before,
                "after": after,
                "changes": changes,
                "error": str(exc),
            }
            self._update_action(action_id, status="failed", result=result, error=str(exc))
            return result

    def _execute_memory_action(self, action_id: str, action=None) -> dict[str, Any]:
        action = action or self._load_action(action_id)
        if not action:
            return {"actionId": action_id, "status": "failed", "error": "action not found"}
        self._update_action(action_id, status="running")
        payload = _json_object(action["payload_json"])
        try:
            if payload.get("kind"):
                memory_payload = MemoryCreate.model_validate(payload)
            else:
                note_text = str(payload.get("noteText") or payload.get("after") or "").strip()
                memory_payload = MemoryCreate(
                    kind="note",
                    title=note_text[:60] or "个人记录",
                    content=note_text,
                    summary=note_text[:220],
                    source="user",
                    metadata={
                        "compat": "notes",
                        "year": payload.get("year"),
                        "month": payload.get("month"),
                        "date": payload.get("date"),
                    },
                )
            saved = MemoryService().create_memory(memory_payload)
            result = {
                "actionId": action_id,
                "operation": action["operation"],
                "status": "success",
                "memory": saved.model_dump(by_alias=True),
                "kind": saved.kind,
                "title": saved.title,
                "content": saved.content,
                "summary": saved.summary,
                "tags": saved.tags,
                "noteText": saved.content,
                "year": payload.get("year"),
                "month": payload.get("month"),
                "date": payload.get("date"),
                "updatedAt": saved.updated_at,
            }
            self._update_action(action_id, status="success", result=result)
            return result
        except Exception as exc:
            result = {
                "actionId": action_id,
                "operation": action["operation"] if action else "create",
                "status": "failed",
                "kind": payload.get("kind") or "note",
                "title": payload.get("title") or "",
                "content": payload.get("content") or payload.get("noteText") or "",
                "error": str(exc),
            }
            self._update_action(action_id, status="failed", result=result, error=str(exc))
            return result

    def _execute_note_action(self, action_id: str, action=None) -> dict[str, Any]:
        action = action or self._load_action(action_id)
        if not action:
            return {"actionId": action_id, "status": "failed", "error": "action not found"}
        self._update_action(action_id, status="running")
        payload = _json_object(action["payload_json"])
        try:
            year = int(payload.get("year"))
            month = int(payload.get("month"))
            after = str(payload.get("after") or "")
            saved = upsert_month_note(MonthNotePut(year=year, month=month, content=after))
            result = {
                "actionId": action_id,
                "operation": action["operation"],
                "status": "success",
                "year": saved.year,
                "month": saved.month,
                "date": payload.get("date"),
                "noteText": payload.get("noteText"),
                "before": payload.get("before") or "",
                "after": saved.content,
                "updatedAt": saved.updated_at,
            }
            self._update_action(action_id, status="success", result=result)
            return result
        except Exception as exc:
            result = {
                "actionId": action_id,
                "operation": action["operation"] if action else "update",
                "status": "failed",
                "year": payload.get("year"),
                "month": payload.get("month"),
                "before": payload.get("before") or "",
                "after": payload.get("after") or "",
                "error": str(exc),
            }
            self._update_action(action_id, status="failed", result=result, error=str(exc))
            return result

    def _execute_calendar_action(self, action_id: str) -> dict[str, Any]:
        action = self._load_action(action_id)
        if not action:
            return {"created": 0, "updated": 0, "failed": 1, "affectedDates": [], "errors": ["action not found"], "plans": []}
        if action["target"] == "calendar" and not self._calendar_action_is_approved(action_id):
            self._update_action(action_id, status="waiting_approval")
            return {
                "actionId": action_id,
                "created": 0,
                "updated": 0,
                "failed": 1,
                "affectedDates": [],
                "errors": ["calendar action requires explicit approval"],
                "plans": [],
            }
        payload = _json_object(action["payload_json"])
        planning_session_id = str(payload.get("planningSessionId") or "")
        if planning_session_id:
            # Fail closed at execution time so a repaired plan cannot reuse a
            # stale preview or stale human approval.
            orchestrator = get_planning_orchestrator()
            assert_calendar = getattr(orchestrator, "assert_calendar_write_allowed", None)
            final_approval_ref = payload.get("finalApprovalRef")
            proposal_ref = payload.get("calendarProposalRef")
            if not self._planning_artifact_ref_is_current(
                planning_session_id,
                final_approval_ref,
                "final_approval_bundle",
            ) or not self._planning_artifact_ref_is_current(
                planning_session_id,
                proposal_ref,
                "calendar_proposal",
            ):
                raise RuntimeError("planning Calendar write requires a versioned Harness action")
            if not callable(assert_calendar):
                raise RuntimeError("canonical planning Calendar write requires Harness policy")
            assert_calendar(planning_session_id, final_approval_ref=final_approval_ref)
        if action["operation"] in {"update", "delete"}:
            return self._execute_calendar_patch_action(action_id, action)
        self._update_action(action_id, status="running")
        items = payload.get("plans") if isinstance(payload.get("plans"), list) else []
        created = 0
        updated = 0
        failed = 0
        affected_dates: set[str] = set()
        errors: list[str] = []
        written_plans: list[dict[str, Any]] = []
        for item in items:
            if not _is_record(item):
                continue
            try:
                state, plan = self._upsert_calendar_plan(item)
                if state == "created":
                    created += 1
                else:
                    updated += 1
                affected_dates.add(str(item.get("date") or ""))
                written_plans.append({
                    "id": plan.id,
                    "date": plan.date,
                    "time": plan.time,
                    "title": plan.content,
                    "sourceKey": plan.source_key,
                    "state": state,
                })
            except Exception as exc:
                failed += 1
                errors.append(str(exc))
        result = {
            "actionId": action_id,
            "created": created,
            "updated": updated,
            "failed": failed,
            "affectedDates": sorted(date for date in affected_dates if date),
            "errors": errors,
            "plans": written_plans,
        }
        if failed == 0 and payload.get("planningSessionId"):
            orchestrator = get_planning_orchestrator()
            final_approval_ref = payload.get("finalApprovalRef")
            orchestrator.mark_calendar_written(
                str(payload.get("planningSessionId")),
                final_approval_ref=final_approval_ref,
            )
        self._update_action(action_id, status="success" if failed == 0 else "failed", result=result, error="; ".join(errors))
        return result

    def _upsert_calendar_plan(self, item: dict[str, Any]):
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError("plan title is empty")
        date = _normalize_task_date(item.get("date"), _today())
        time = str(item.get("time") or "09:00")
        source_key = str(item.get("sourceKey") or "").strip()
        description = str(item.get("description") or "").strip()
        priority = _task_priority(item.get("priority"))
        estimated_minutes = _task_minutes(item.get("estimatedMinutes"))
        row_data: dict[str, Any] | None = None
        with get_conn() as conn:
            row = None
            if source_key:
                row = conn.execute("SELECT * FROM plans WHERE source_key = ? LIMIT 1", (source_key,)).fetchone()
            if not row and not source_key:
                row = conn.execute(
                    """
                    SELECT * FROM plans
                    WHERE date = ? AND content = ? AND source = 'ai'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (date, title),
                ).fetchone()
            if row:
                if row["source"] != "ai":
                    row = None
            if row:
                row_data = dict(row)
        if row_data:
            updates: dict[str, Any] = {}
            if source_key and row_data["source_key"] != source_key:
                updates["sourceKey"] = source_key
            if description and not str(row_data["result"] or "").strip():
                updates["result"] = description
            if row_data["priority"] != priority:
                updates["priority"] = priority
            if int(row_data["estimated_minutes"] or 0) != estimated_minutes:
                updates["estimatedMinutes"] = estimated_minutes
            plan = update_plan(row_data["id"], PlanUpdate(**updates))
            refined_task = _refined_task_from_payload(item.get("refinedTask"))
            if refined_task:
                plan = save_plan_refined_task(plan.id, PlanRefinedTaskUpdate(refinedTask=refined_task))
            return "updated", plan
        refined_task = _refined_task_from_payload(item.get("refinedTask"))
        plan = create_plan(
            PlanCreate(
                date=date,
                time=time,
                content=title,
                done=False,
                result=description,
                priority=priority,
                estimatedMinutes=estimated_minutes,
                source="ai",
                sourceKey=source_key,
                refinedTask=refined_task,
            )
        )
        return "created", plan

    def _llm_chat(self, payload: CommandChatRequest, *, thread_id: str = "", exclude_message_id: str = "") -> str:
        content, _usage = self._llm_chat_with_usage(payload, thread_id=thread_id, exclude_message_id=exclude_message_id)
        return content

    def _llm_chat_with_usage(
        self,
        payload: CommandChatRequest,
        *,
        thread_id: str = "",
        exclude_message_id: str = "",
    ) -> tuple[str, ModelUsage | None]:
        thread_context = self._thread_context_summary(thread_id, exclude_message_id=exclude_message_id) if thread_id else ""
        system = (
            "You are Planix P Mode in Phase 4.4. Reply in the user's language. "
            "Normal chat is allowed. Calendar writes are handled only by explicit draft commands and PermissionGate. "
            "Do not claim to write Notes, Materials, Goals, Settings, or any data outside Calendar plans."
        )
        user = (
            "Current thread context, if any:\n"
            f"{thread_context or '(none)'}\n\n"
            "User message:\n"
            f"{payload.message}\n\n"
            "Answer conversationally and concisely. If the user asks for unsupported execution, explain the current P Mode boundary."
        )
        client = LlmClient()
        result, error = client.complete(
            "command_chat",
            system,
            user,
            max_tokens=700,
            temperature=0.3,
            timeout_seconds=30,
            task_type="chat",
        )
        if result and result.content.strip():
            return result.content.strip(), usage_from_llm_result(result, "chat")
        if error and error.local_fallback_allowed is False:
            return "模型调用失败且本地兜底已关闭，请检查模型路由或开启本地兜底。", local_fallback_usage(client, "chat", error)
        return _fallback_reply(payload.message), local_fallback_usage(client, "chat", error)
