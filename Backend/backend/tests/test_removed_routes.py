import pytest

from app.db import get_conn


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/runtime/run"),
        ("post", "/api/agent/plan"),
        ("post", "/api/agent/review"),
        ("post", "/api/agent/evaluate"),
        ("post", "/api/planning/goal-plan"),
        ("post", "/api/planning/refine-task"),
        ("post", "/api/planning/daily-review"),
        ("post", "/api/planning/replan/apply"),
        ("post", "/api/materials/ai-draft"),
        ("post", "/api/rag/query"),
        ("post", "/api/rag/documents"),
        ("post", "/api/rag/documents/upload"),
        ("get", "/api/rag/documents"),
        ("delete", "/api/rag/documents/retired"),
        ("post", "/api/rag/ingest"),
        ("get", "/api/memory"),
        ("post", "/api/memory"),
        ("get", "/api/memory/search"),
        ("patch", "/api/memory/retired"),
        ("delete", "/api/memory/retired"),
    ],
)
def test_retired_routes_are_not_mounted(client, method, path):
    assert client.request(method.upper(), path, json={}).status_code == 404


def test_openapi_and_fresh_database_exclude_retired_rag_and_generic_memory(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/api/rag")]
    assert not [path for path in paths if path.startswith("/api/memory")]

    with get_conn() as conn:
        tables = {
            row["table_name"]
            for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()
        }
    assert tables.isdisjoint({"documents", "document_chunks", "document_chunks_fts", "memories", "memories_fts"})
    assert {"user_model_memories", "user_planning_hypotheses"} <= tables
