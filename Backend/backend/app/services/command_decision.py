from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from pydantic import ValidationError

from ..schemas import CommandDecision, ModelUsage
from .llm import LlmClient, LlmResult


DECISION_CONFIDENCE_THRESHOLD = 0.45


@dataclass(frozen=True)
class CommandDecisionResult:
    decision: CommandDecision | None
    usage: ModelUsage | None
    source: str
    error: str = ""


def usage_from_llm_result(result: LlmResult, task_type: str) -> ModelUsage:
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
        fallbackUsed=result.fallback_used,
        localFallbackAllowed=result.local_fallback_allowed,
        attempts=result.attempts or [],
    )


def local_fallback_usage(client: LlmClient, task_type: str, error: object | None = None) -> ModelUsage:
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


def _strip_json_fence(value: str) -> str:
    cleaned = value.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S | re.I)
    return match.group(1).strip() if match else cleaned


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_json_fence(value))
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class CommandDecisionService:
    def __init__(self, client: LlmClient | None = None):
        self.client = client or LlmClient()

    def decide(
        self,
        message: str,
        *,
        task_type: str = "command_decision",
        thread_context: str = "",
        current_draft: dict[str, Any] | None = None,
        last_search_results: list[dict[str, Any]] | None = None,
        context_date: str = "",
        calendar_summary: list[dict[str, Any]] | None = None,
        notes_summary: list[dict[str, Any]] | None = None,
    ) -> CommandDecisionResult:
        system = (
            "You are Planix's conversational command intent router. "
            "Your job is not to answer the user. Decide what Planix should do next. "
            "Return only one valid JSON object. Do not include markdown or explanations. "
            "Never write to the database and never claim that data was written. "
            "If the user's request is unclear, return intent=\"clarify\" with clarificationQuestion. "
            "Use Simplified Chinese for decisionSummary and clarificationQuestion when the user writes Chinese. "
            "Allowed intents: create_plan, save_plan_to_calendar, query_plan, query_memory, patch_calendar_plan, "
            "refine_plan, refine_task, query_notes, save_memory, save_note, modify_current_draft, chat, clarify. "
            "Writes must set needsConfirmation=true. Read-only queries can set needsConfirmation=false. "
            "Use query_plan only for Calendar plans. Use query_memory for notes, materials, preferences, reviews, and planning history. "
            "Use save_memory for remembered notes, materials, preferences, and reviews. For saving notes or memories, put the exact content in extractedParams.noteText when available. "
            "For calendar patches, use extractedParams.targetIndex for references like first/second, and patchFields "
            "only for title, date, time, estimatedMinutes. Do not include done, result, completion, source, sourceKey, "
            "refinedTask, createdAt, or updatedAt."
        )
        user = json.dumps(
            {
                "message": message,
                "currentDate": context_date,
                "threadContext": thread_context,
                "currentDraft": current_draft or None,
                "lastPlanSearchResults": last_search_results or [],
                "calendarSummary": calendar_summary or [],
                "notesSummary": notes_summary or [],
                "requiredShape": {
                    "intent": "create_plan|save_plan_to_calendar|query_plan|query_memory|patch_calendar_plan|refine_plan|refine_task|query_notes|save_memory|save_note|modify_current_draft|chat|clarify",
                    "confidence": 0.0,
                    "targetType": "current_draft|calendar_plan|calendar_date|note|material|unknown",
                    "action": "create|save|query|update|delete|refine|reschedule|summarize|answer",
                    "extractedParams": {
                        "title": "",
                        "date": "YYYY-MM-DD",
                        "dateRange": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
                        "time": "HH:MM",
                        "estimatedMinutes": 30,
                        "targetIndex": 1,
                        "query": "",
                        "noteText": "",
                        "refinementInstruction": "",
                        "patchFields": {
                            "title": "",
                            "date": "YYYY-MM-DD",
                            "time": "HH:MM",
                            "estimatedMinutes": 30,
                        },
                    },
                    "needsConfirmation": False,
                    "needsClarification": False,
                    "clarificationQuestion": "",
                    "decisionSummary": "",
                },
            },
            ensure_ascii=False,
        )
        result, error = self.client.complete(
            task_type,
            system,
            user,
            max_tokens=700,
            temperature=0,
            timeout_seconds=20,
            response_format_json=True,
            task_type=task_type,
        )
        if not result:
            if error and error.local_fallback_allowed is False:
                return CommandDecisionResult(
                    decision=CommandDecision(
                        intent="clarify",
                        confidence=1,
                        targetType="unknown",
                        action="answer",
                        needsClarification=True,
                        clarificationQuestion="模型调用失败且本地兜底已关闭，请检查模型路由或开启本地兜底。",
                        decisionSummary="模型路由失败",
                    ),
                    usage=local_fallback_usage(self.client, task_type, error),
                    source="llm_error",
                    error=error.message,
                )
            return CommandDecisionResult(
                decision=None,
                usage=local_fallback_usage(self.client, task_type, error),
                source="local_fallback",
                error=error.message if error else "llm unavailable",
            )

        usage = usage_from_llm_result(result, task_type)
        parsed = _parse_json_object(result.content)
        if not parsed:
            return CommandDecisionResult(
                decision=None,
                usage=usage,
                source="local_fallback",
                error="invalid decision json",
            )
        try:
            decision = CommandDecision.model_validate(parsed)
        except ValidationError as exc:
            return CommandDecisionResult(
                decision=None,
                usage=usage,
                source="local_fallback",
                error=str(exc),
            )
        if decision.confidence < DECISION_CONFIDENCE_THRESHOLD and decision.intent != "clarify":
            return CommandDecisionResult(
                decision=None,
                usage=usage,
                source="local_fallback",
                error="low confidence decision",
            )
        return CommandDecisionResult(decision=decision, usage=usage, source="llm")
