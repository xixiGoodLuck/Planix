from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pydantic import Field

from ..contracts import KnowledgeNode, LearningContract
from ..generators import LearningSemanticModel
from .providers import VideoSearchQuery


SEARCH_QUERY_SYSTEM = """
Generate concise video-search queries for one KnowledgeNode. Queries may describe concepts, operations, and tutorial
intent only. Never return or invent a URL, domain, video title, BV/AV identifier, duration, timestamp, uploader, or
resource identity. Return JSON only and do not reveal hidden reasoning.
""".strip()


class SearchQueryDraft(LearningContract):
    queries: list[str] = Field(min_length=1, max_length=4)


@dataclass(frozen=True)
class SearchQueryGenerationResult:
    search_query: VideoSearchQuery
    model_usage: dict[str, Any]


class SearchQueryGenerationError(ValueError):
    pass


class SearchQueryGenerator:
    _FORBIDDEN = re.compile(
        r"https?://|www\.|bilibili\.com|\bBV[0-9A-Za-z]{10}\b|\bav\d+\b|\b\d{1,3}:\d{2}\b",
        re.IGNORECASE,
    )

    def __init__(self, model: LearningSemanticModel | None = None):
        self.model = model

    def generate(
        self,
        knowledge: KnowledgeNode,
        *,
        language: str = "zh-CN",
        maximum_results: int = 5,
    ) -> SearchQueryGenerationResult:
        if self.model is None:
            queries = [
                f"{knowledge.name} 中文教程",
                f"{knowledge.name} 入门 实战 教程",
            ]
            model_usage: dict[str, Any] = {}
        else:
            response = self.model.complete(
                stage="learning_video_search_query",
                feature="learning_video_search_query_generation",
                system=SEARCH_QUERY_SYSTEM,
                payload={
                    "name": knowledge.name,
                    "explanation": knowledge.explanation,
                    "whyRequired": knowledge.why_required,
                    "masteryIndicators": knowledge.mastery_indicators,
                    "language": language,
                },
                response_type=SearchQueryDraft,
                max_tokens=600,
            )
            queries = response.value.queries
            model_usage = response.model_usage

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in queries:
            query = " ".join(raw.split()).strip()
            if not query or len(query) > 120:
                raise SearchQueryGenerationError(
                    "search query must contain 1..120 visible characters"
                )
            if self._FORBIDDEN.search(query):
                raise SearchQueryGenerationError(
                    "search query must not contain a resource URL, video id, or duration"
                )
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(query)
        if not normalized:
            raise SearchQueryGenerationError("no safe search query was generated")
        return SearchQueryGenerationResult(
            search_query=VideoSearchQuery(
                knowledgeTerms=normalized,
                language=language,
                maximumResults=maximum_results,
            ),
            model_usage=model_usage,
        )


__all__ = [
    "SEARCH_QUERY_SYSTEM",
    "SearchQueryDraft",
    "SearchQueryGenerationError",
    "SearchQueryGenerationResult",
    "SearchQueryGenerator",
]
