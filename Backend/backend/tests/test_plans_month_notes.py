def test_plan_crud(client):
    created = client.post("/api/plans", json={"date": "2026-06-30", "time": "09:00", "content": "Build SQLite plan API", "priority": "high", "estimatedMinutes": 90})
    assert created.status_code == 200
    plan = created.json()
    assert client.get("/api/plans", params={"date": "2026-06-30"}).json()[0]["id"] == plan["id"]
    updated = client.patch(f"/api/plans/{plan['id']}", json={"done": True, "completion": "CRUD verified"})
    assert updated.json()["done"] is True
    assert updated.json()["result"] == "CRUD verified"
    assert client.delete(f"/api/plans/{plan['id']}").status_code == 204


def test_list_month_plans_returns_only_requested_month(client):
    for date, title in [("2026-06-30", "Previous"), ("2026-07-01", "First"), ("2026-07-18", "Second")]:
        assert client.post("/api/plans", json={"date": date, "time": "09:00", "content": title}).status_code == 200
    body = client.get("/api/plans/month", params={"year": 2026, "month": 7}).json()
    assert [item["content"] for item in body] == ["First", "Second"]


def test_delete_all_plans_preserves_independent_data(client):
    assert client.post("/api/plans", json={"date": "2026-07-06", "time": "09:00", "content": "Plan"}).status_code == 200
    assert client.put("/api/month-notes", json={"year": 2026, "month": 7, "content": "Keep note"}).status_code == 200
    assert client.post("/api/rag/documents", json={"title": "Keep document", "content": "Keep content"}).status_code == 200
    assert client.delete("/api/plans/all").json()["deleted"] == 1
    assert client.get("/api/month-notes", params={"year": 2026, "month": 7}).json()["content"] == "Keep note"
    assert client.get("/api/rag/documents").json()[0]["title"] == "Keep document"


def test_month_note_upsert(client):
    assert client.get("/api/month-notes", params={"year": 2026, "month": 6}).json()["content"] == ""
    saved = client.put("/api/month-notes", json={"year": 2026, "month": 6, "content": "June focus"})
    assert saved.status_code == 200
    assert client.get("/api/month-notes", params={"year": 2026, "month": 6}).json()["content"] == "June focus"
