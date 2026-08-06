from __future__ import annotations

from ...memory_store import MemoryService
from ..contracts import MemoryDocument, UserGoalModel


PROVENANCE_METADATA_KEYS = {
    "planningSessionId",
    "domain",
    "domainScope",
    "sourceStage",
    "artifactType",
    "artifactId",
}


def safe_provenance_metadata(metadata: dict) -> dict:
    result: dict = {}
    for key in PROVENANCE_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:240]
        elif isinstance(value, list):
            cleaned = [str(item).strip()[:120] for item in value[:20] if str(item).strip()]
            if cleaned:
                result[key] = cleaned
    return result


class CognitiveMemoryRetriever:
    def __init__(self, memory: MemoryService | None = None):
        self.memory = memory or MemoryService()

    def retrieve(self, goal: UserGoalModel, *, limit: int = 20) -> list[MemoryDocument]:
        query = " ".join(
            value
            for value in (goal.goal_statement, goal.desired_change, goal.domain, goal.subdomain or "")
            if value
        )
        items = self.memory.search_memories(
            query,
            kinds=["preference", "review", "material", "note"],
            limit=limit,
        )
        return [
            MemoryDocument(
                id=item.id,
                kind=item.kind,
                title=item.title,
                summary=item.summary,
                content="" if item.kind == "review" else item.content[:2000],
                tags=item.tags,
                contextRole="historical_context" if item.kind == "review" else "supporting_context",
                source=item.source,
                sourceId=item.source_id,
                sourceKey=item.source_key,
                metadata=safe_provenance_metadata(item.metadata),
            )
            for item in items
        ]
