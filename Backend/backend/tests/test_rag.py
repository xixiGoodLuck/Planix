from app.services.rag import RagService


def test_rag_document_crud_and_deterministic_retrieval(client):
    created = client.post("/api/rag/documents", json={"title": "AI internship JD", "content": "FastAPI React RAG Agent workflow", "sourceType": "paste"})
    assert created.status_code == 200
    document = created.json()
    sources = RagService().retrieve("RAG FastAPI")
    assert sources and sources[0].document_id == document["id"]
    assert client.delete(f"/api/rag/documents/{document['id']}").status_code == 204
    assert RagService().retrieve("RAG FastAPI") == []


def test_rag_ingest_still_writes_documents(client):
    response = client.post("/api/rag/ingest", json={"title": "Course note", "content": "BM25 deterministic retrieval"})
    assert response.status_code == 200
    assert client.get("/api/rag/documents").json()[0]["title"] == "Course note"


def test_upload_txt_and_md_documents(client):
    txt = client.post("/api/rag/documents/upload", data={"title": "Uploaded JD", "sourceType": "upload"}, files={"file": ("jd.txt", b"FastAPI React", "text/plain")})
    md = client.post("/api/rag/documents/upload", files={"file": ("note.md", b"# RAG\nBM25 retrieval", "text/markdown")})
    assert txt.status_code == md.status_code == 200
    assert {item["title"] for item in client.get("/api/rag/documents").json()} == {"Uploaded JD", "note"}


def test_upload_rejects_unsupported_empty_and_large_files(client):
    assert client.post("/api/rag/documents/upload", files={"file": ("resume.pdf", b"pdf", "application/pdf")}).status_code == 400
    assert client.post("/api/rag/documents/upload", files={"file": ("empty.txt", b"", "text/plain")}).status_code == 400
    assert client.post("/api/rag/documents/upload", files={"file": ("large.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")}).status_code == 400
