from __future__ import annotations

from ...memory_store import MemoryService
from ..contracts import MemoryDocument, UserGoalModel
from .memory_retriever import safe_provenance_metadata


class PlanningHistoryRetriever:
    """Retrieves prior plans as evidence without treating them as reusable plan content."""

    def __init__(self, memory: MemoryService | None = None):
        self.memory = memory or MemoryService()

    def retrieve(self, goal: UserGoalModel, *, limit: int = 8) -> list[MemoryDocument]:
        query = " ".join(
            value
            for value in (goal.goal_statement, goal.desired_change, goal.domain, goal.subdomain or "")
            if value
        )
        items = self.memory.search_memories(query, kinds=["planning_history"], limit=limit)
        return [
            MemoryDocument(
                id=item.id,
                kind=item.kind,
                title=item.title,
                summary=item.summary,
                content="",
                tags=item.tags,
                contextRole="historical_context",
                source=item.source,
                sourceId=item.source_id,
                sourceKey=item.source_key,
                metadata=safe_provenance_metadata(item.metadata),
            )
            for item in items
        ]
