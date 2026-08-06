from app.db import get_conn


def _refined_task() -> dict:
    return {
        "title": "Refined task",
        "objective": "Turn the plan into a concrete execution session.",
        "estimatedMinutes": 45,
        "steps": ["Clarify the output.", "Do the smallest useful step.", "Record the result."],
        "checklist": ["Output exists.", "Next action is clear."],
        "acceptanceCriteria": ["The plan has a visible deliverable."],
        "deliverable": "A short execution note.",
        "risks": [],
        "fallbackTips": ["Reduce the scope if blocked."],
        "mode": "local_fallback",
    }


def test_plan_crud(client):
    created = client.post(
        "/api/plans",
        json={
            "date": "2026-06-30",
            "time": "09:00",
            "content": "Build SQLite plan API",
            "priority": "high",
            "estimatedMinutes": 90,
        },
    )
    assert created.status_code == 200
    plan = created.json()
    assert plan["content"] == "Build SQLite plan API"
    assert plan["done"] is False
    assert plan["priority"] == "high"

    listed = client.get("/api/plans", params={"date": "2026-06-30"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [plan["id"]]

    updated = client.patch(
        f"/api/plans/{plan['id']}",
        json={"done": True, "completion": "CRUD verified"},
    )
    assert updated.status_code == 200
    assert updated.json()["done"] is True
    assert updated.json()["result"] == "CRUD verified"

    deleted = client.delete(f"/api/plans/{plan['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/plans", params={"date": "2026-06-30"}).json() == []


def test_plan_refined_task_persists_and_deletes_without_touching_completion(client):
    created = client.post(
        "/api/plans",
        json={
            "date": "2026-07-04",
            "time": "10:00",
            "content": "Practice swimming breathing",
            "completion": "Keep this completion text",
            "source": "ai",
            "sourceKey": "goal-1-0-0",
            "refinedTask": _refined_task(),
        },
    )
    assert created.status_code == 200
    plan = created.json()
    assert plan["sourceKey"] == "goal-1-0-0"
    assert plan["refinedTask"]["title"] == "Refined task"
    assert plan["refinedTaskUpdatedAt"]
    assert plan["result"] == "Keep this completion text"

    updated_task = {**_refined_task(), "title": "Updated refinement"}
    patched = client.patch(
        f"/api/plans/{plan['id']}/refined-task",
        json={"refinedTask": updated_task},
    )
    assert patched.status_code == 200
    patched_plan = patched.json()
    assert patched_plan["refinedTask"]["title"] == "Updated refinement"
    assert patched_plan["result"] == "Keep this completion text"

    listed = client.get("/api/plans", params={"date": "2026-07-04"})
    assert listed.status_code == 200
    assert listed.json()[0]["refinedTask"]["title"] == "Updated refinement"

    deleted = client.delete(f"/api/plans/{plan['id']}/refined-task")
    assert deleted.status_code == 200
    deleted_plan = deleted.json()
    assert deleted_plan["refinedTask"] is None
    assert deleted_plan["refinedTaskUpdatedAt"] is None
    assert deleted_plan["result"] == "Keep this completion text"


def test_list_month_plans_returns_only_requested_month(client):
    for plan_date, title in [
        ("2026-06-30", "Previous month"),
        ("2026-07-01", "First July plan"),
        ("2026-07-18", "Second July plan"),
        ("2026-08-01", "Next month"),
    ]:
        created = client.post(
            "/api/plans",
            json={"date": plan_date, "time": "09:00", "content": title},
        )
        assert created.status_code == 200

    listed = client.get("/api/plans/month", params={"year": 2026, "month": 7})
    assert listed.status_code == 200
    body = listed.json()
    assert [item["content"] for item in body] == ["First July plan", "Second July plan"]
    assert {item["date"] for item in body} == {"2026-07-01", "2026-07-18"}

    empty = client.get("/api/plans/month", params={"year": 2026, "month": 9})
    assert empty.status_code == 200
    assert empty.json() == []


def test_bad_refined_task_json_does_not_break_plan_listing(client):
    created = client.post(
        "/api/plans",
        json={
            "date": "2026-07-05",
            "time": "11:00",
            "content": "Plan with broken refinement",
        },
    )
    assert created.status_code == 200
    plan_id = created.json()["id"]

    with get_conn() as conn:
        conn.execute(
            "UPDATE plans SET refined_task_json = ?, refined_task_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("{not valid json", plan_id),
        )

    listed = client.get("/api/plans", params={"date": "2026-07-05"})
    assert listed.status_code == 200
    assert listed.json()[0]["refinedTask"] is None


def test_delete_all_plans_preserves_notes_documents_and_ai_settings(client):
    for index, plan_date in enumerate(["2026-07-06", "2026-07-07"], start=1):
        created = client.post(
            "/api/plans",
            json={
                "date": plan_date,
                "time": "09:00",
                "content": f"Plan {index}",
                "refinedTask": _refined_task(),
            },
        )
        assert created.status_code == 200

    note = client.put(
        "/api/month-notes",
        json={"year": 2026, "month": 7, "content": "Keep this calendar note"},
    )
    assert note.status_code == 200

    document = client.post(
        "/api/rag/documents",
        json={"title": "Keep document", "content": "Document content should remain."},
    )
    assert document.status_code == 200

    settings = client.put(
        "/api/ai/settings",
        json={
            "provider": "mock",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "temperature": 0.3,
            "timeoutSeconds": 40,
        },
    )
    assert settings.status_code == 200

    deleted = client.delete("/api/plans/all")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 2
    assert client.delete("/api/plans/all").json()["deleted"] == 0
    assert client.get("/api/plans", params={"date": "2026-07-06"}).json() == []
    assert client.get("/api/plans", params={"date": "2026-07-07"}).json() == []
    assert client.get("/api/month-notes", params={"year": 2026, "month": 7}).json()["content"] == "Keep this calendar note"
    documents = client.get("/api/rag/documents").json()
    assert any(item["title"] == "Keep document" for item in documents)
    assert client.get("/api/ai/settings").json()["provider"] == "mock"


def test_month_note_upsert(client):
    empty = client.get("/api/month-notes", params={"year": 2026, "month": 6})
    assert empty.status_code == 200
    assert empty.json()["content"] == ""

    saved = client.put(
        "/api/month-notes",
        json={"year": 2026, "month": 6, "content": "June focus: backend data layer"},
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == "June focus: backend data layer"

    loaded = client.get("/api/month-notes", params={"year": 2026, "month": 6})
    assert loaded.json()["content"] == "June focus: backend data layer"
