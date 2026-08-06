from __future__ import annotations

from typing import Protocol

from ..contracts import ResearchPolicy
from .static_resource_candidates import candidate_pool


class WebResearchProvider(Protocol):
    def search(self, query: str, policy: ResearchPolicy) -> list[dict]:
        ...


class DisabledWebResearchProvider:
    def search(self, query: str, policy: ResearchPolicy) -> list[dict]:
        return []


class ResourceResearch:
    def __init__(self, web_provider: WebResearchProvider | None = None):
        self.web_provider = web_provider or DisabledWebResearchProvider()

    def candidates(self, query: str, policy: ResearchPolicy) -> list[dict]:
        allowed = set(policy.source_types_allowed)
        candidates = [
            item
            for item in candidate_pool(query)
            if not allowed or str(item.get("type") or "") in allowed
        ]
        if policy.allow_web_research and not policy.require_user_approval:
            candidates.extend(
                item
                for item in self.web_provider.search(query, policy)
                if not allowed or str(item.get("type") or "") in allowed
            )
        return candidates
