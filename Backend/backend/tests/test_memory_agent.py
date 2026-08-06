import json

from app.db import get_conn
from app.schemas import MemoryCreate
from app.services.memory_agent import MemoryAgentService, detect_query_kinds, infer_memory_kind
from app.services.memory_store import MemoryService


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_memory_agent_kind_detection_and_grouped_search(client):
    service = MemoryService()
    service.create_memory(MemoryCreate(kind="note", title="Python note", content="Python pathlib preference note"))
    service.create_memory(MemoryCreate(kind="material", title="Python material", content="FastAPI material for Python project"))
    service.create_memory(MemoryCreate(kind="planning_history", title="Python archive", content="Previous Python plan"))

    assert detect_query_kinds("查笔记 Python") == ["note"]
    assert detect_query_kinds("查资料 Python") == ["material"]
    assert detect_query_kinds("历史规划 Python") == ["planning_history"]
    assert infer_memory_kind("记住：我晚上 8 点适合学习") == "preference"
    assert infer_memory_kind("保存一下这段参考资料") == "material"

    notes = MemoryAgentService(service).search("查笔记 Python")
    assert [group.kind for group in notes.groups] == ["note"]
    assert all(item.kind == "note" for item in notes.results)

    all_memory = MemoryAgentService(service).search("查记忆 Python")
    assert {group.kind for group in all_memory.groups} >= {"note", "material", "planning_history"}


def test_p_mode_query_memory_and_query_plan_are_separate(client):
    MemoryService().create_memory(MemoryCreate(kind="material", title="Python material", content="FastAPI material"))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO plans(id, date, time, content, done)
            VALUES ('plan-memory-separate', '2026-07-08', '09:00', 'Calendar Python task', 0)
            """
        )

    plan_response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": "查看我的计划", "context": {"date": "2026-07-08"}},
    )
    assert plan_response.status_code == 200
    plan_event = next(event for event in _events(plan_response) if event["type"] == "plan_search_results")
    assert plan_event["calendarPlans"]
    assert plan_event["materials"] == []
    assert plan_event["goalHistory"] == []
    assert plan_event["monthNotes"] == []

    memory_response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "message": "查资料 Python", "context": {"date": "2026-07-08"}},
    )
    assert memory_response.status_code == 200
    memory_event = next(event for event in _events(memory_response) if event["type"] == "memory_search_results")
    assert memory_event["groups"][0]["kind"] == "material"


def test_p_mode_record_memory_approve_and_reject(client):
    preview_response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "message": "记住：我晚上 8 点适合学习"},
    )
    events = _events(preview_response)
    preview = next(event for event in events if event["type"] == "memory_write_preview")
    assert preview["kind"] == "preference"
    approval = next(event for event in events if event["type"] == "approval_required")

    rejected = client.post(
        "/api/command/approve",
        json={"actionId": approval["actionId"], "permission": "low", "decision": "reject"},
    )
    assert rejected.status_code == 200
    assert MemoryService().search_memories("晚上", kinds=["preference"]) == []

    preview_response = client.post(
        "/api/command/chat",
        json={"mode": "auto", "permission": "low", "message": "记住：我晚上 8 点适合学习"},
    )
    approval = next(event for event in _events(preview_response) if event["type"] == "approval_required")
    approved = client.post(
        "/api/command/approve",
        json={"actionId": approval["actionId"], "permission": "low", "decision": "approve"},
    )
    assert approved.status_code == 200
    result = next(event for event in _events(approved) if event["type"] == "memory_write_result")
    assert result["status"] == "success"
    assert MemoryService().search_memories("晚上", kinds=["preference"])
