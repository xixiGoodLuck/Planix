from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import AiMaterialDraftOut, AiMaterialDraftRequest
from .llm import LlmClient
from .planner import _json_object


URL_PATTERN = re.compile(r"(https?://|www\.)", re.I)
REALTIME_CLAIM_PATTERN = re.compile(r"(实时搜索|联网搜索|网页搜索|联网获取|web search|searched the web|real-time web)", re.I)


def create_material_draft(payload: AiMaterialDraftRequest) -> AiMaterialDraftOut:
    query = payload.query.strip()
    output_language = payload.output_language if payload.output_language in {"zh", "en"} else "zh"
    llm_result, _ = LlmClient().complete(
        "material_ai_draft",
        _material_draft_system_prompt(output_language),
        json.dumps({"query": query, "outputLanguage": output_language}, ensure_ascii=False),
        max_tokens=2200,
        max_token_cap=4000,
        temperature=0.25,
        response_format_json=True,
        task_type="model_knowledge",
    )
    if llm_result:
        parsed = _json_object(llm_result.content)
        draft = _draft_from_parsed(parsed, source_type="model_knowledge")
        if draft and _draft_is_safe(draft):
            return draft
    return _local_material_draft(query, output_language)


def enrich_with_model_knowledge(
    *,
    goal: str,
    output_language: str,
    memory_context_summary: str,
    trigger_reason: str,
) -> dict[str, object]:
    language = "zh" if output_language != "en" else "en"
    llm_result, _ = LlmClient().complete(
        "runtime_model_knowledge",
        _runtime_enrichment_system_prompt(language),
        json.dumps(
            {
                "goal": goal,
                "outputLanguage": language,
                "memoryContextSummary": memory_context_summary,
                "triggerReason": trigger_reason,
            },
            ensure_ascii=False,
        ),
        max_tokens=1200,
        max_token_cap=3000,
        temperature=0.25,
        response_format_json=True,
        task_type="model_knowledge",
    )
    if llm_result:
        parsed = _json_object(llm_result.content)
        material = _enrichment_from_parsed(parsed, trigger_reason=trigger_reason)
        if material and _knowledge_material_is_safe(material):
            return material
    return _local_model_knowledge(goal, language, trigger_reason)


def _material_draft_system_prompt(output_language: str) -> str:
    language_rule = (
        "Write all user-facing fields in Simplified Chinese."
        if output_language == "zh"
        else "Write all user-facing fields in English."
    )
    return (
        "You generate a knowledge-base draft for Planix. Return strict JSON only with keys "
        "title, content, summary, caveat. Do not return markdown code fences. "
        "Use general model knowledge only. Do not generate URLs. Do not claim real-time web search. "
        "Do not invent organizations, courses, prices, policies, latest data, or named external sources. "
        "If the topic involves sports, health, safety, or physical training, include practical safety reminders. "
        "Make the content natural, clear, and suitable to paste into a material form. "
        f"{language_rule}"
    )


def _runtime_enrichment_system_prompt(output_language: str) -> str:
    language_rule = (
        "Write all user-facing fields in Simplified Chinese."
        if output_language == "zh"
        else "Write all user-facing fields in English."
    )
    return (
        "You provide model knowledge enrichment for a planning runtime. Return strict JSON only with keys "
        "title, summary, relevance, suggestions, caveat. suggestions must be an array of concise strings. "
        "Use general model knowledge only. Do not generate URLs. Do not claim real-time web search. "
        "Do not invent organizations, courses, prices, policies, latest data, or named external sources. "
        "For sports, health, safety, or physical training, include safety reminders. "
        f"{language_rule}"
    )


def _draft_from_parsed(parsed: dict[str, Any] | None, *, source_type: str) -> AiMaterialDraftOut | None:
    if not parsed:
        return None
    title = _clean_field(parsed.get("title"))
    content = _clean_field(parsed.get("content"))
    summary = _clean_field(parsed.get("summary"))
    caveat = _clean_field(parsed.get("caveat"))
    if not title or not content:
        return None
    return AiMaterialDraftOut(
        title=title,
        content=content,
        summary=summary or _summarize(content),
        sourceType=source_type,
        caveat=caveat or None,
    )


def _enrichment_from_parsed(parsed: dict[str, Any] | None, *, trigger_reason: str) -> dict[str, object] | None:
    if not parsed:
        return None
    title = _clean_field(parsed.get("title"))
    summary = _clean_field(parsed.get("summary"))
    suggestions = parsed.get("suggestions")
    normalized_suggestions = [_clean_field(item) for item in suggestions] if isinstance(suggestions, list) else []
    normalized_suggestions = [item for item in normalized_suggestions if item][:5]
    if not title or not summary:
        return None
    return {
        "sourceType": "model_knowledge",
        "triggerReason": trigger_reason,
        "title": title,
        "summary": summary,
        "relevance": _float_value(parsed.get("relevance"), 0.75),
        "suggestions": normalized_suggestions,
        "caveat": _clean_field(parsed.get("caveat")) or _default_caveat("zh"),
    }


def _local_material_draft(query: str, output_language: str) -> AiMaterialDraftOut:
    if output_language == "en":
        title = f"{query} notes"
        safety = (
            "\n\nSafety note: if the topic involves sport, health, or physical practice, "
            "start in a safe environment and ask a qualified person for guidance when needed."
        )
        content = (
            f"# {title}\n\n"
            f"## Core idea\n{query} can be organized as a practical learning material: clarify the goal, "
            "identify the basic concepts, then turn them into small actions and review questions.\n\n"
            "## Suggested structure\n"
            "- Start with the most basic terminology and safety boundaries.\n"
            "- Break practice into short sessions with one focus each time.\n"
            "- Record what worked, what felt difficult, and what needs a follow-up check.\n\n"
            "## Review questions\n"
            "- What is the smallest next step?\n"
            "- What risk or prerequisite should be checked first?\n"
            "- What evidence shows progress?"
            f"{safety}"
        )
        summary = f"A practical draft for organizing {query} into learnable notes."
        caveat = "Generated from a local knowledge template."
    else:
        title = f"{query}资料草稿"
        content = (
            f"# {title}\n\n"
            f"## 核心认识\n{query} 可以先整理为一份可执行资料：明确目标、拆出基础概念，再转成练习步骤和复盘问题。\n\n"
            "## 建议结构\n"
            "- 先确认基本概念、适用边界和安全注意事项。\n"
            "- 每次练习只聚焦一个动作或一个知识点，避免一次塞入太多内容。\n"
            "- 记录完成情况、卡点和下一步要验证的问题。\n\n"
            "## 复盘问题\n"
            "- 下一步最小行动是什么？\n"
            "- 需要先确认哪些前提或风险？\n"
            "- 哪个结果能证明自己已经进步？\n\n"
            "安全提醒：如果主题涉及运动、健康或实际训练，请在安全环境中练习，必要时寻求专业人士指导。"
        )
        summary = f"围绕“{query}”整理的一份可执行资料草稿。"
        caveat = "本地知识模板生成。"
    return AiMaterialDraftOut(
        title=title,
        content=content,
        summary=summary,
        sourceType="local_knowledge_template",
        caveat=caveat,
    )


def _local_model_knowledge(goal: str, output_language: str, trigger_reason: str) -> dict[str, object]:
    if output_language == "en":
        title = "Model knowledge enrichment"
        summary = (
            "Use general knowledge to add background, common practice paths, risk checks, "
            "and review prompts before generating the structured plan."
        )
        suggestions = [
            "Start from fundamentals and safety boundaries.",
            "Use small practice loops with one clear output each time.",
            "Review progress and adjust the next session.",
        ]
        caveat = "This is model knowledge, not live web research."
    else:
        title = "大模型知识补全"
        summary = f"围绕“{goal}”补充通用背景、常见路径、风险提醒和练习建议，用于辅助结构化规划。"
        suggestions = [
            "先确认基础概念和安全边界。",
            "把练习拆成短周期，每次只验证一个重点。",
            "通过复盘记录卡点，再调整下一次安排。",
        ]
        caveat = "这是模型通用知识补全，不是实时资料。"
    return {
        "sourceType": "local_knowledge_template",
        "triggerReason": trigger_reason,
        "title": title,
        "summary": summary,
        "relevance": 0.68,
        "suggestions": suggestions,
        "caveat": caveat,
    }


def _draft_is_safe(draft: AiMaterialDraftOut) -> bool:
    text = " ".join([draft.title, draft.content, draft.summary, draft.caveat or ""])
    return not URL_PATTERN.search(text) and not REALTIME_CLAIM_PATTERN.search(text)


def _knowledge_material_is_safe(material: dict[str, object]) -> bool:
    text = json.dumps(material, ensure_ascii=False)
    return not URL_PATTERN.search(text) and not REALTIME_CLAIM_PATTERN.search(text)


def _clean_field(value: object) -> str:
    return str(value or "").strip()


def _summarize(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip()[:160]


def _float_value(value: object, fallback: float) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(parsed, 1.0))


def _default_caveat(output_language: str) -> str:
    if output_language == "en":
        return "This is model knowledge, not live web research."
    return "这是模型通用知识补全，不是实时资料。"
