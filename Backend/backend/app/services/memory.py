from ..schemas import MemoryCreate, MemoryPayload
from .memory_store import MemoryService


class MemoryStore:
    def __init__(self):
        self.memories = MemoryService()

    def save(self, payload: MemoryPayload) -> dict[str, str]:
        self.memories.create_memory(
            MemoryCreate(
                kind="preference",
                title="用户偏好",
                content=payload.preferences,
                summary=payload.preferences[:220],
                source="user",
                sourceId=payload.user_id,
                sourceKey=f"preferences:{payload.user_id}",
                metadata={"compat": "memory/preferences"},
            )
        )
        return {"userId": payload.user_id, "preferences": payload.preferences}

    def get(self, user_id: str = "local-user") -> dict[str, str]:
        source_key = f"preferences:{user_id}"
        item = self.memories.get_by_source_key("preference", source_key)
        if item:
            return {"userId": user_id, "preferences": item.content}
        return {"userId": user_id, "preferences": ""}
