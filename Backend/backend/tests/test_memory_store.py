from app.schemas import MemoryCreate, MemoryUpdate
from app.services.memory_store import MemoryService


def test_memory_store_create_search_group_update_delete(client):
    service = MemoryService()
    note = service.create_memory(
        MemoryCreate(kind="note", title="Python note", content="Learn pathlib and pytest", tags=["python"])
    )
    material = service.create_memory(
        MemoryCreate(kind="material", title="Interview JD", content="FastAPI React RAG agent workflow")
    )
    history = service.create_memory(
        MemoryCreate(
            kind="planning_history",
            title="Python plan",
            content="30 day Python planning archive",
            source="ai",
            sourceKey="draft:python",
        )
    )
    updated_history = service.create_memory(
        MemoryCreate(
            kind="planning_history",
            title="Python plan v2",
            content="Updated 30 day Python planning archive",
            source="ai",
            sourceKey="draft:python",
        )
    )

    assert updated_history.id == history.id
    assert service.get_memory(note.id).title == "Python note"
    assert [item.kind for item in service.search_memories("pytest", kinds=["note"])] == ["note"]
    assert [item.kind for item in service.search_memories("FastAPI", kinds=["material"])] == ["material"]

    grouped = service.search_memories_grouped("Python")
    assert {group.kind for group in grouped.groups} >= {"note", "planning_history"}
    assert all(item.kind != "planning_history" for item in service.search_memories("Python", kinds=["note"]))

    updated = service.update_memory(note.id, MemoryUpdate(summary="Updated summary", tags=["python", "test"]))
    assert updated.summary == "Updated summary"
    assert updated.tags == ["python", "test"]

    service.delete_memory(material.id)
    assert service.search_memories("FastAPI", kinds=["material"]) == []


def test_memory_api_crud_and_search(client):
    created = client.post(
        "/api/memory",
        json={"kind": "preference", "title": "Learning window", "content": "I study better after 8 PM"},
    )
    assert created.status_code == 200
    memory = created.json()
    assert memory["kind"] == "preference"

    searched = client.get("/api/memory/search?q=8%20PM&kind=preference")
    assert searched.status_code == 200
    assert searched.json()["groups"][0]["kind"] == "preference"

    patched = client.patch(f"/api/memory/{memory['id']}", json={"summary": "Evening preference"})
    assert patched.status_code == 200
    assert patched.json()["summary"] == "Evening preference"

    deleted = client.delete(f"/api/memory/{memory['id']}")
    assert deleted.status_code == 204
