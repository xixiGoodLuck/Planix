from app.db import get_conn
from app.services import model_knowledge as model_knowledge_module
from app.services.llm import LlmResult


def _documents_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS total FROM documents").fetchone()["total"]


def test_ai_material_draft_returns_local_template_without_writing_documents(client):
    before = _documents_count()

    response = client.post(
        "/api/materials/ai-draft",
        json={"query": "游泳入门安全注意事项", "outputLanguage": "zh"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"]
    assert body["content"]
    assert body["summary"]
    assert body["sourceType"] == "local_knowledge_template"
    assert "http://" not in body["content"]
    assert "https://" not in body["content"]
    assert _documents_count() == before


def test_ai_material_draft_validates_empty_and_long_query(client):
    empty = client.post("/api/materials/ai-draft", json={"query": "   "})
    long_query = client.post("/api/materials/ai-draft", json={"query": "x" * 201})

    assert empty.status_code == 422
    assert long_query.status_code == 422


def test_ai_material_draft_uses_llm_json_when_safe(client, monkeypatch):
    class DraftClient:
        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content='{"title":"游泳安全笔记","content":"先熟悉水性，再练习漂浮和换气，并在安全水域练习。","summary":"游泳入门安全草稿","caveat":"注意安全"}',
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(model_knowledge_module, "LlmClient", lambda: DraftClient())

    response = client.post(
        "/api/materials/ai-draft",
        json={"query": "游泳入门安全注意事项", "outputLanguage": "zh"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sourceType"] == "model_knowledge"
    assert body["title"] == "游泳安全笔记"
    assert "联网" not in body["content"]


def test_ai_material_draft_rejects_llm_url_and_falls_back(client, monkeypatch):
    class UrlDraftClient:
        def complete(self, *args, **kwargs):
            return (
                LlmResult(
                    content='{"title":"资料","content":"查看 https://example.com 获取最新内容","summary":"包含链接","caveat":""}',
                    provider="deepseek",
                    model="deepseek-v4-flash",
                ),
                None,
            )

    monkeypatch.setattr(model_knowledge_module, "LlmClient", lambda: UrlDraftClient())

    response = client.post(
        "/api/materials/ai-draft",
        json={"query": "游泳入门安全注意事项", "outputLanguage": "zh"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sourceType"] == "local_knowledge_template"
    assert "https://" not in body["content"]
