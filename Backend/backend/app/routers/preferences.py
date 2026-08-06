from fastapi import APIRouter, Query, Response, status

from ..schemas import MemoryCreate, MemoryItemOut, MemoryKind, MemoryPayload, MemorySearchResult, MemoryUpdate
from ..services.memory import MemoryStore
from ..services.memory_store import MemoryService

router = APIRouter(prefix="/api/memory", tags=["memory"])

memory = MemoryStore()
memories = MemoryService()


@router.get("", response_model=list[MemoryItemOut])
def list_memories(kind: list[MemoryKind] | None = Query(default=None), limit: int = 100) -> list[MemoryItemOut]:
    return memories.list_memories(kinds=kind, limit=limit)


@router.post("", response_model=MemoryItemOut)
def create_memory(payload: MemoryCreate) -> MemoryItemOut:
    return memories.create_memory(payload)


@router.get("/search", response_model=MemorySearchResult)
def search_memories(
    q: str = "",
    kind: list[MemoryKind] | None = Query(default=None),
    limit: int = 20,
) -> MemorySearchResult:
    return memories.search_memories_grouped(q, kinds=kind, limit=limit)


@router.post("/preferences")
def save_preferences(payload: MemoryPayload) -> dict[str, str]:
    return memory.save(payload)


@router.get("/preferences")
def get_preferences(user_id: str = "local-user") -> dict[str, str]:
    return memory.get(user_id)


@router.get("/{memory_id}", response_model=MemoryItemOut)
def get_memory(memory_id: str) -> MemoryItemOut:
    return memories.get_memory(memory_id)


@router.patch("/{memory_id}", response_model=MemoryItemOut)
def update_memory(memory_id: str, payload: MemoryUpdate) -> MemoryItemOut:
    return memories.update_memory(memory_id, payload)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str) -> Response:
    memories.delete_memory(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
