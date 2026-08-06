from __future__ import annotations

import re
from typing import Iterable

from ..schemas import MemoryCreate, MemoryKind, MemorySearchResult
from .memory_store import MEMORY_KIND_ORDER, MemoryService


MemoryKindList = list[MemoryKind]


def _contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def detect_query_kinds(message: str) -> MemoryKindList:
    text = message.strip()
    if _contains_any(text, ("查笔记", "我的笔记", "个人记录", "我记录过", "记一下的内容", "notes", "note")):
        return ["note"]
    if _contains_any(text, ("查资料", "学习资料", "文档", "参考资料", "面试资料", "项目资料", "资料库", "materials", "material", "document")):
        return ["material"]
    if _contains_any(text, ("历史规划", "以前的规划", "上次生成的计划", "之前的计划", "规划档案", "planning history")):
        return ["planning_history"]
    if _contains_any(text, ("我的偏好", "我之前说过", "适合什么时候", "我的限制", "偏好", "preference")):
        return ["preference"]
    if _contains_any(text, ("复盘", "总结", "执行情况", "为什么没完成", "完成反馈", "review")):
        return ["review"]
    return list(MEMORY_KIND_ORDER)  # type: ignore[return-value]


def infer_memory_kind(message: str, *, explicit: str | None = None) -> MemoryKind:
    if explicit in MEMORY_KIND_ORDER:
        return explicit  # type: ignore[return-value]
    text = message.strip()
    if _contains_any(text, ("记住", "偏好", "适合", "不喜欢", "只能", "希望以后")):
        return "preference"
    if _contains_any(text, ("资料", "文档", "参考资料")):
        return "material"
    if _contains_any(text, ("复盘", "总结", "没完成原因")):
        return "review"
    if _contains_any(text, ("记一下", "记录一下", "保存一下", "记录", "保存")):
        return "note"
    return "note"


def memory_text_from_message(message: str) -> str:
    cleaned = message.strip()
    patterns = [
        r"^(?:请)?(?:帮我)?(?:记一下|记录一下|保存一下|记住|记录|保存)(?:这条|一条)?(?:记忆|笔记|资料|偏好)?[:：,，\s]*(.+)$",
        r"^(?:record|remember|save)\s+(?:this\s+)?(?:memory|note)?[:：,，\s]*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned, re.I)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return cleaned


def title_from_content(content: str, kind: str) -> str:
    line = next((item.strip() for item in content.splitlines() if item.strip()), "")
    if line:
        return line[:60]
    labels = {
        "note": "个人记录",
        "material": "知识资料",
        "planning_history": "规划档案",
        "preference": "偏好约束",
        "review": "复盘反馈",
    }
    return labels.get(kind, "记忆")


class MemoryAgentService:
    def __init__(self, memory: MemoryService | None = None):
        self.memory = memory or MemoryService()

    def search(self, message: str, *, query: str | None = None, kinds: list[str] | None = None) -> MemorySearchResult:
        resolved_kinds = kinds or detect_query_kinds(message)
        resolved_query = (query or self._query_text(message)).strip()
        return self.memory.search_memories_grouped(resolved_query, kinds=resolved_kinds, limit=24)

    def preview_create(
        self,
        message: str,
        *,
        content: str | None = None,
        kind: str | None = None,
        title: str | None = None,
    ) -> MemoryCreate:
        note_text = (content or "").strip() or memory_text_from_message(message)
        resolved_kind = infer_memory_kind(message, explicit=kind)
        return MemoryCreate(
            kind=resolved_kind,
            title=title or title_from_content(note_text, resolved_kind),
            content=note_text,
            summary=note_text[:220],
            tags=[],
            source="user",
            metadata={"createdFrom": "p_mode"},
        )

    def _query_text(self, message: str) -> str:
        cleaned = message.strip()
        cleaned = re.sub(r"^(查一下|查找|查询|搜索|看看|找)(我的)?(记忆|笔记|资料|历史规划|偏好|复盘)?[:：,，\s]*", "", cleaned)
        cleaned = re.sub(r"(相关的)?(所有内容|全部内容|内容)$", "", cleaned).strip()
        return cleaned or message.strip()
