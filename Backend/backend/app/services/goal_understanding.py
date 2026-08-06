from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from pydantic import ValidationError

from ..schemas import GoalUnderstandingResult, GoalUnderstandingUncertainty, ModelUsage
from .command_decision import usage_from_llm_result
from .llm import LlmClient


GOAL_CONFIDENCE_THRESHOLD = 0.65

GOAL_UNDERSTANDING_SYSTEM = """
You are Planix's Goal Understanding layer. You run before command routing and must determine whether the user
has expressed a clear personal goal, an ambiguous personal goal, normal conversation, or an operational command.
You do not answer the user and you never write or claim to store data. Return only one JSON object matching the
requested schema, without markdown or hidden reasoning.

A destination or location alone never implies travel. For example, "我要去北京" or "我要去乌鲁木齐" may concern
work, an interview, study, relocation, visiting someone, travel, or another purpose. Treat such input as an
ambiguous_goal, preserve the literal location in knownFacts, express possible meanings only as hypotheses, and ask
one high-value question about the user's purpose. Never select a travel template merely because a place is named.

Understand meaning with the model, not keyword-to-domain mappings. Do not use fixed domain forms, domain templates,
or fixed question banks. possibleDomains are hypotheses grounded in this conversation, not classifications forced
by a noun. knownFacts must contain only information explicitly stated by the user or present in preExtractedFacts.
uncertainties must name information whose answer changes strategy, feasibility, schedule, resources, safety, or
success criteria.

A direct desired change such as "我要学 Python" or "I want to learn Python" is clear enough to enter Goal
Intelligence even when purpose, current level, schedule, or duration is not yet stated. Those details belong to the
next planning stage unless the meaning of the desired change itself is genuinely ambiguous. Never describe a model
or provider failure as if the user's wording were the problem.

Check semantic consistency before accepting a stated purpose. If a supplied purpose, deliverable, or success signal
does not fit the apparent activity or contradicts other facts, do not normalize it, silently reinterpret it, or
blindly accept/store it. Put a concise user-visible explanation in consistencyWarnings, return ambiguous_goal, and
ask a question that lets the user resolve the mismatch. A nonempty consistencyWarnings list always blocks a clear
goal. Low confidence about a personal goal also means ambiguous_goal.

Use intentState="clear_goal" only when the desired change is reliable enough to start Goal Intelligence. Use
intentState="ambiguous_goal" when a personal goal is present but its purpose or another planning-critical meaning is
uncertain. Use intentState="normal_chat" for discussion or questions that are not requests to pursue a personal
goal. Use intentState="command" for operational requests such as querying, saving, modifying, deleting, navigating,
approving, or executing existing Planix data. There is no unknown state. Use the user's language for understoodIntent,
consistencyWarnings, uncertainty descriptions, and nextQuestion.

For ambiguous_goal, state what is already understood and why the remaining distinction changes planning. When the
question has a small honest answer space, return two to four concise, mutually exclusive clarificationOptions in the
user's language. Do not include "Other"; the interface adds it. Leave clarificationOptions empty for open-ended
questions and for clear_goal, normal_chat, or command.

When priorGoalUnderstanding is present, treat it as the exact prior question, hypotheses, and known facts. Resolve
short answers such as "第二个" against those prior options instead of guessing from the latest message alone.
""".strip()


_OBVIOUS_CHINESE_LOCATIONS = frozenset(
    {
        "北京",
        "上海",
        "天津",
        "重庆",
        "广州",
        "深圳",
        "杭州",
        "南京",
        "武汉",
        "成都",
        "西安",
        "苏州",
        "青岛",
        "厦门",
        "长沙",
        "郑州",
        "昆明",
        "大连",
        "沈阳",
        "哈尔滨",
        "三亚",
        "乌鲁木齐",
        "香港",
        "澳门",
        "台北",
    }
)
_OBVIOUS_ENGLISH_LOCATIONS = frozenset(
    {
        "beijing",
        "shanghai",
        "tianjin",
        "chongqing",
        "guangzhou",
        "shenzhen",
        "hangzhou",
        "nanjing",
        "wuhan",
        "chengdu",
        "xi'an",
        "xian",
        "urumqi",
        "hong kong",
        "macau",
        "taipei",
    }
)
_LOCATION_SUFFIXES = ("特别行政区", "自治区", "自治州", "省", "市", "县", "区", "州", "盟", "国")
_CHINESE_GO_TO_LOCATION_ONLY = re.compile(
    r"^\s*(?:(?:我)?(?:要|想|打算|准备|计划)\s*|我\s*)?"
    r"(?:(?:今天|明天|后天|本周|下周)\s*)?(?:去|前往|到)\s*"
    r"(?P<location>[\u4e00-\u9fff]{2,12})\s*[。.!！?？]?\s*$"
)
_ENGLISH_GO_TO_LOCATION_ONLY = re.compile(
    r"^\s*(?:i\s+(?:want|plan|intend|need)\s+to\s+)?go\s+to\s+"
    r"(?P<location>[A-Za-z][A-Za-z .'-]{1,48}?)\s*[.!?]?\s*$",
    re.I,
)
_EXPLICIT_LOCATION = re.compile(
    r"(?:目的地|地点|位置)\s*(?:是|为|在|[:：])?\s*"
    r"(?P<location>[\u4e00-\u9fff]{2,12})(?=$|[\s，。！？,.!?；;])"
)
_INLINE_GO_TO_LOCATION = re.compile(
    r"(?:去|前往|到)\s*(?P<location>[\u4e00-\u9fff]{2,12}?)(?=$|[\s，。！？,.!?；;])"
)
_DATE_EXPRESSION = re.compile(
    r"(?:20\d{2}(?:年\d{1,2}月(?:\d{1,2}[日号]?)?|[-/]\d{1,2}(?:[-/]\d{1,2})?)"
    r"|(?<![\d年])\d{1,2}月(?:\d{1,2}[日号])?"
    r"|今天|明天|后天|本周|下周|本月|下月"
    r"|today|tomorrow|the day after tomorrow|this week|next week|this month|next month)",
    re.I,
)
_DURATION_EXPRESSION = re.compile(
    r"(?<!\d)\d+(?:\.\d+)?\s*"
    r"(?:分钟|小时|天|周|星期|个月|月|年|minutes?|hours?|days?|weeks?|months?|years?)",
    re.I,
)
_TIME_COMMITMENT_EXPRESSION = re.compile(
    r"(?:每天|每日|每周|每星期|daily|weekly)\s*\d+(?:\.\d+)?\s*"
    r"(?:分钟|小时|minutes?|hours?)",
    re.I,
)
_CLOCK_TIME_EXPRESSION = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
_SKILL_EXPRESSION = re.compile(
    r"(?:我要|我想|想要)?\s*(?:学|学习|学会|掌握|练习)\s*"
    r"(?P<skill>[A-Za-z][A-Za-z0-9+#._-]{0,31}|[\u4e00-\u9fff]{1,10}?)"
    r"(?=$|[\s，。！？,.!?；;]|零基础|有基础|每天|每日|每周|在\d|用\d|花\d)",
    re.I,
)
_EXPLICIT_SKILL_EXPRESSION = re.compile(
    r"(?:技能(?:是|为|包括|有)?|会|熟悉|擅长)\s*[:：]?\s*"
    r"(?P<skill>[A-Za-z][A-Za-z0-9+#._-]{0,31}|[\u4e00-\u9fff]{1,12})"
    r"(?=$|[\s，。！？,.!?；;])",
    re.I,
)
_CONSTRAINT_MARKER = re.compile(
    r"(?:必须|只能|不能|不要|不可以|至少|至多|最多|预算|截止|务必|"
    r"must|only|cannot|can't|do not|at least|at most|budget|deadline|before|within)",
    re.I,
)


@dataclass(frozen=True)
class GoalUnderstandingOutcome:
    result: GoalUnderstandingResult | None
    usage: ModelUsage | None
    source: str
    error: str = ""


def _model_unavailable_usage(client: LlmClient, error: object | None) -> ModelUsage:
    attempts = getattr(error, "attempts", None) or []
    return ModelUsage(
        provider=client.settings.provider,
        model=client.settings.model,
        mode="model_unavailable",
        taskType="goal_understanding",
        fallbackUsed=getattr(error, "fallback_used", None),
        localFallbackAllowed=getattr(error, "local_fallback_allowed", None),
        attempts=attempts,
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _obvious_location(value: str) -> str:
    cleaned = value.strip().strip("，。！？,.!?；; ")
    if not cleaned:
        return ""
    if cleaned in _OBVIOUS_CHINESE_LOCATIONS or cleaned.casefold() in _OBVIOUS_ENGLISH_LOCATIONS:
        return cleaned
    if any(cleaned.endswith(suffix) and len(cleaned) > len(suffix) for suffix in _LOCATION_SUFFIXES):
        return cleaned
    return ""


def _go_to_location_literal(text: str) -> str:
    chinese = _CHINESE_GO_TO_LOCATION_ONLY.fullmatch(text)
    if chinese:
        return _obvious_location(chinese.group("location"))
    english = _ENGLISH_GO_TO_LOCATION_ONLY.fullmatch(text)
    if english:
        return _obvious_location(english.group("location"))
    return ""


def extract_obvious_goal_facts(text: str) -> dict[str, Any]:
    """Extract literal spans only; never assign a domain or interpret their meaning."""

    value = (text or "").strip()
    if not value:
        return {}

    facts: dict[str, Any] = {}
    locations: list[str] = []
    go_to_location = _go_to_location_literal(value)
    if go_to_location:
        locations.append(go_to_location)
    for match in _EXPLICIT_LOCATION.finditer(value):
        location = _obvious_location(match.group("location"))
        if location:
            locations.append(location)
    for match in _INLINE_GO_TO_LOCATION.finditer(value):
        location = _obvious_location(match.group("location"))
        if location:
            locations.append(location)
    locations = _dedupe_strings(locations)
    if locations:
        facts["location"] = locations[0]
        if len(locations) > 1:
            facts["locations"] = locations

    date_matches = list(_DATE_EXPRESSION.finditer(value))
    dates = _dedupe_strings([match.group(0) for match in date_matches])
    if dates:
        facts["dateExpressions"] = dates

    durations = _dedupe_strings(
        [
            match.group(0)
            for match in _DURATION_EXPRESSION.finditer(value)
            if not any(match.start() < date.end() and date.start() < match.end() for date in date_matches)
        ]
    )
    if durations:
        facts["durationExpressions"] = durations

    times = _dedupe_strings(
        [match.group(0) for match in _TIME_COMMITMENT_EXPRESSION.finditer(value)]
        + [match.group(0) for match in _CLOCK_TIME_EXPRESSION.finditer(value)]
    )
    if times:
        facts["timeExpressions"] = times

    skills = _dedupe_strings(
        [match.group("skill") for match in _SKILL_EXPRESSION.finditer(value)]
        + [match.group("skill") for match in _EXPLICIT_SKILL_EXPRESSION.finditer(value)]
    )
    if skills:
        facts["skills"] = skills

    clauses = [part.strip() for part in re.split(r"[\n，。！？,.!?；;]+", value) if part.strip()]
    constraints = _dedupe_strings([clause for clause in clauses if _CONSTRAINT_MARKER.search(clause)])
    if constraints:
        facts["constraints"] = constraints
    return facts


def _strip_json_fence(value: str) -> str:
    cleaned = (value or "").strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S | re.I)
    return match.group(1).strip() if match else cleaned


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_json_fence(value))
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _useful_question(value: str | None) -> bool:
    cleaned = str(value or "").strip()
    if len(cleaned) < 6:
        return False
    return cleaned.casefold() not in {
        "unknown",
        "n/a",
        "none",
        "please clarify",
        "please provide more details",
        "请补充",
        "请说明更多",
    }


def _normalize_result(result: GoalUnderstandingResult) -> GoalUnderstandingResult:
    possible_domains = _dedupe_strings(result.possible_domains)
    consistency_warnings = _dedupe_strings(result.consistency_warnings)
    clarification_options = _dedupe_strings(result.clarification_options)[:4]
    uncertainties = [
        GoalUnderstandingUncertainty(field=item.field.strip(), impact=item.impact.strip())
        for item in result.uncertainties
        if item.field.strip() and item.impact.strip()
    ]
    known_facts = {
        str(key).strip(): value
        for key, value in result.known_facts.items()
        if str(key).strip() and value not in (None, "", [], {})
    }
    intent_state = result.intent_state
    if consistency_warnings or (
        intent_state in {"clear_goal", "ambiguous_goal"} and result.confidence < GOAL_CONFIDENCE_THRESHOLD
    ):
        intent_state = "ambiguous_goal"

    next_question = str(result.next_question or "").strip() or None
    if intent_state == "ambiguous_goal" and not _useful_question(next_question):
        raise ValueError("ambiguous goal understanding requires a model-authored next question")
    if intent_state != "ambiguous_goal":
        clarification_options = []
    if consistency_warnings and not uncertainties:
        raise ValueError("consistency warnings require a decision-relevant uncertainty")

    if consistency_warnings:
        rejected_fields = {
            re.sub(r"[\W_]+", "", item.field.casefold())
            for item in uncertainties
            if item.field.strip()
        }
        rejected_fields.discard("")
        rejected_values = [
            str(value).strip()
            for key, value in known_facts.items()
            if any(
                re.sub(r"[\W_]+", "", key.casefold()) == field
                or re.sub(r"[\W_]+", "", key.casefold()).endswith(field)
                for field in rejected_fields
            )
            and isinstance(value, (str, int, float))
            and str(value).strip()
        ]
        if any(value.casefold() in result.understood_intent.casefold() for value in rejected_values):
            raise ValueError("understood intent includes a consistency-rejected fact")
        known_facts = {
            key: value
            for key, value in known_facts.items()
            if not any(
                re.sub(r"[\W_]+", "", key.casefold()) == field
                or re.sub(r"[\W_]+", "", key.casefold()).endswith(field)
                for field in rejected_fields
            )
        }

    return GoalUnderstandingResult(
        intentState=intent_state,
        understoodIntent=result.understood_intent.strip(),
        possibleDomains=possible_domains,
        knownFacts=known_facts,
        uncertainties=uncertainties,
        consistencyWarnings=consistency_warnings,
        nextQuestion=next_question,
        clarificationOptions=clarification_options,
        confidence=result.confidence,
    )


class GoalUnderstandingService:
    def __init__(self, client: LlmClient | None = None):
        self.client = client or LlmClient()

    def understand(
        self,
        message: str,
        *,
        thread_context: str = "",
        prior_understanding: dict[str, Any] | None = None,
    ) -> GoalUnderstandingOutcome:
        pre_extracted_facts = extract_obvious_goal_facts(message)
        user = json.dumps(
            {
                "message": message,
                "threadContext": thread_context,
                "priorGoalUnderstanding": prior_understanding or None,
                "preExtractedFacts": pre_extracted_facts,
                "requiredOutputSchema": GoalUnderstandingResult.model_json_schema(by_alias=True),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        model_result, error = self.client.complete(
            "goal_understanding",
            GOAL_UNDERSTANDING_SYSTEM,
            user,
            max_tokens=1000,
            max_token_cap=1200,
            temperature=0,
            timeout_seconds=20,
            response_format_json=True,
            task_type="goal_understanding",
        )
        if not model_result:
            error_message = error.message if error else "goal understanding model unavailable"
            return GoalUnderstandingOutcome(
                result=None,
                usage=_model_unavailable_usage(self.client, error),
                source="model_unavailable",
                error=error_message,
            )

        usage = usage_from_llm_result(model_result, "goal_understanding")
        parsed = _parse_json_object(model_result.content)
        if not parsed:
            return self._invalid_output(usage, "invalid goal understanding json")
        try:
            understanding = GoalUnderstandingResult.model_validate(parsed)
            understanding = _normalize_result(understanding)
        except (ValidationError, ValueError) as exc:
            return self._invalid_output(usage, str(exc))
        return GoalUnderstandingOutcome(result=understanding, usage=usage, source="llm")

    @staticmethod
    def _invalid_output(usage: ModelUsage, error: str) -> GoalUnderstandingOutcome:
        return GoalUnderstandingOutcome(
            result=None,
            usage=usage,
            source="invalid_model_output",
            error=error,
        )


__all__ = [
    "GOAL_CONFIDENCE_THRESHOLD",
    "GOAL_UNDERSTANDING_SYSTEM",
    "GoalUnderstandingOutcome",
    "GoalUnderstandingService",
    "extract_obvious_goal_facts",
]
