from __future__ import annotations

import re
from urllib.parse import urlsplit


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
BILIBILI_ID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}", re.IGNORECASE)
CONTINUE_PHRASES = (
    "继续",
    "直接开始",
    "就这些",
    "按现在的信息继续",
    "按当前信息继续",
    "不用补充",
    "其他先不填",
    "先生成看看",
    "continue with current",
    "start now",
)


def contains_url(value: str) -> bool:
    return bool(URL_PATTERN.search(value))


def redact_urls(value: str) -> str:
    return URL_PATTERN.sub("[USER_SUPPLIED_URL]", value)


def extract_bilibili_urls(value: str) -> list[str]:
    canonical: list[str] = []
    for match in URL_PATTERN.findall(value):
        candidate = match.rstrip(".,;:!?，。；：！？)]}）】")
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if host != "bilibili.com" and not host.endswith(".bilibili.com"):
            continue
        identifier = BILIBILI_ID_PATTERN.search(parsed.path)
        if identifier is None:
            continue
        normalized_id = "BV" + identifier.group(0)[2:]
        normalized = f"https://www.bilibili.com/video/{normalized_id}"
        if normalized not in canonical:
            canonical.append(normalized)
    return canonical


def canonicalize_bilibili_resource_url(value: str) -> str:
    candidate = value.strip()
    if not candidate or not URL_PATTERN.fullmatch(candidate):
        raise ValueError("resource URL must be an absolute Bilibili video URL")
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() not in {"http", "https"} or (
        host != "bilibili.com" and not host.endswith(".bilibili.com")
    ):
        raise ValueError("resource URL must use the Bilibili domain")
    identifier = BILIBILI_ID_PATTERN.search(parsed.path)
    if identifier is None:
        raise ValueError("resource URL must contain a valid Bilibili BV identifier")
    normalized_id = "BV" + identifier.group(0)[2:]
    return f"https://www.bilibili.com/video/{normalized_id}"


def canonicalize_bilibili_resource_urls(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(canonicalize_bilibili_resource_url(value) for value in values)
    )


def is_continue_intent(value: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?]+", "", value).casefold()
    return any(re.sub(r"\s+", "", phrase).casefold() in normalized for phrase in CONTINUE_PHRASES)


def has_target_result_signal(value: str) -> bool:
    return bool(
        re.search(
            r"最终|最后|目标是|希望(?:能|做到)|想(?:要)?(?:做|完成|理解|掌握|实现)|"
            r"完成.{0,24}(?:项目|API|应用|系统)|\b(?:build|create|understand|implement|ship)\b",
            value,
            re.IGNORECASE,
        )
    )


def extract_target_result(value: str) -> str | None:
    chinese = re.search(
        r"(?P<verb>理解|掌握|完成|实现|做出|做到|构建|开发)\s*"
        r"(?P<target>[^，。；\n]{2,160})",
        value,
        re.IGNORECASE,
    )
    if chinese is not None and re.search(
        r"想|希望|最终|最后|目标",
        value[max(0, chinese.start() - 8) : chinese.start()],
    ):
        target = chinese.group("target").strip()
        separator = " " if target and target[0].isascii() else ""
        return f"{chinese.group('verb')}{separator}{target}"
    english = re.search(
        r"(?:want|hope|aim|goal is)\s+to\s+(?P<target>[^,.;\n]{2,160})",
        value,
        re.IGNORECASE,
    )
    return english.group("target").strip() if english is not None else None


def has_current_level_signal(value: str) -> bool:
    return bool(
        re.search(
            r"我(?:会|学过|掌握|熟悉|了解|不会)|基础|入门|初学|零基础|经验|"
            r"\b(?:beginner|novice|intermediate|advanced|know|familiar|experience)\b",
            value,
            re.IGNORECASE,
        )
    )


def text_supported_by_message(value: str, message: str) -> bool:
    candidate = value.strip().casefold()
    source = message.casefold()
    if not candidate:
        return False
    compact_candidate = re.sub(r"\s+", "", candidate)
    compact_source = re.sub(r"\s+", "", source)
    if compact_candidate in compact_source or compact_source in compact_candidate:
        return True
    tokens = [
        token
        for token in re.findall(r"[a-z0-9_+#.-]{2,}|[\u4e00-\u9fff]{2,}", candidate)
        if token not in {"学习", "掌握", "了解", "基础", "目标", "内容"}
    ]
    return bool(tokens) and all(token in source for token in tokens)


def parse_content_minutes(value: str) -> int | None:
    match = re.search(
        r"(?P<amount>\d+(?:\.\d+)?|一|两|二|半)\s*"
        r"(?P<unit>分钟|分(?:钟)?|小时|个小时|hours?|hrs?|minutes?|mins?)",
        value,
        re.IGNORECASE,
    )
    if match is None:
        return None
    raw_amount = match.group("amount")
    amount = {"一": 1.0, "二": 2.0, "两": 2.0, "半": 0.5}.get(raw_amount)
    if amount is None:
        amount = float(raw_amount)
    unit = match.group("unit").casefold()
    minutes = amount * (60 if "小时" in unit or unit.startswith(("hour", "hr")) else 1)
    return max(1, int(minutes))


def language_preferences(value: str) -> tuple[list[str], list[str]]:
    lowered = value.casefold()
    chinese = bool(re.search(r"中文|汉语|chinese", lowered))
    english = bool(re.search(r"英文|英语|english", lowered))
    only = bool(re.search(r"只(?:看|要|用)|仅|only", lowered))
    if chinese and not english:
        return (["zh-CN"], [] if only else ["en"])
    if english and not chinese:
        return (["en"], [] if only else ["zh-CN"])
    return ([], [])


def platform_preferences(value: str) -> list[str]:
    if re.search(r"B\s*站|哔哩哔哩|bilibili", value, re.IGNORECASE):
        return ["bilibili"]
    return []


def style_preferences(value: str) -> list[str]:
    mappings = (
        (r"项目|project", "project_based"),
        (r"实操|动手|hands?[- ]on", "hands_on"),
        (r"概念|原理|concept", "conceptual"),
        (r"讲座|lecture", "lecture"),
        (r"短视频|short[- ]form", "short_form"),
    )
    return [style for pattern, style in mappings if re.search(pattern, value, re.IGNORECASE)]


__all__ = [
    "canonicalize_bilibili_resource_url",
    "canonicalize_bilibili_resource_urls",
    "contains_url",
    "extract_bilibili_urls",
    "extract_target_result",
    "has_current_level_signal",
    "has_target_result_signal",
    "is_continue_intent",
    "language_preferences",
    "parse_content_minutes",
    "platform_preferences",
    "redact_urls",
    "style_preferences",
    "text_supported_by_message",
]
