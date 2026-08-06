def test_rag_document_crud_and_bm25_query(client):
    created = client.post(
        "/api/rag/documents",
        json={
            "title": "Beijing AI application internship JD",
            "content": (
                "The role requires FastAPI, React, RAG, Agent workflow, Prompt Engineering, "
                "SQLite, and tool calling experience for AI application development."
            ),
            "sourceType": "paste",
        },
    )
    assert created.status_code == 200
    document = created.json()
    assert document["title"] == "Beijing AI application internship JD"
    assert document["chunks"] >= 1

    listed = client.get("/api/rag/documents")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [document["id"]]

    queried = client.post(
        "/api/rag/query",
        json={
            "goal": "prepare for AI application internship",
            "materials": "What should I improve first for RAG Agent FastAPI?",
            "date": "2026-07-01",
        },
    )
    assert queried.status_code == 200
    body = queried.json()
    assert body["mode"] == "mock"
    assert body["sources"]
    assert body["sources"][0]["documentId"] == document["id"]
    assert "FastAPI" in body["sources"][0]["chunk"]

    deleted = client.delete(f"/api/rag/documents/{document['id']}")
    assert deleted.status_code == 204

    queried_after_delete = client.post(
        "/api/rag/query",
        json={
            "goal": "prepare for AI application internship",
            "materials": "RAG Agent FastAPI",
            "date": "2026-07-01",
        },
    )
    assert queried_after_delete.status_code == 200
    assert queried_after_delete.json()["sources"] == []


def test_legacy_rag_ingest_still_writes_documents(client):
    response = client.post(
        "/api/rag/ingest",
        json={
            "title": "Course note",
            "content": "RAG retrieval should return source chunks with BM25 ranking.",
        },
    )
    assert response.status_code == 200
    assert response.json()["chunks"] >= 1

    listed = client.get("/api/rag/documents")
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "Course note"


def test_upload_txt_document_lists_document(client):
    response = client.post(
        "/api/rag/documents/upload",
        data={"title": "Uploaded JD", "sourceType": "upload"},
        files={"file": ("jd.txt", b"FastAPI React RAG Agent portfolio upload test.", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Uploaded JD"
    assert body["sourceType"] == "upload"

    listed = client.get("/api/rag/documents").json()
    assert listed[0]["id"] == body["id"]


def test_upload_md_document_is_queryable(client):
    response = client.post(
        "/api/rag/documents/upload",
        files={
            "file": (
                "beijing-ai.md",
                b"# JD\nNeed RAG retrieval, BM25 citations, FastAPI backend, and planner evaluation.",
                "text/markdown",
            )
        },
    )
    assert response.status_code == 200
    document = response.json()
    assert document["title"] == "beijing-ai"

    queried = client.post(
        "/api/rag/query",
        json={
            "goal": "AI internship",
            "materials": "BM25 citations planner evaluation",
            "date": "2026-07-01",
        },
    )
    assert queried.status_code == 200
    sources = queried.json()["sources"]
    assert sources
    assert sources[0]["documentId"] == document["id"]


def test_upload_rejects_unsupported_empty_and_large_files(client):
    unsupported = client.post(
        "/api/rag/documents/upload",
        files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert unsupported.status_code == 400

    empty = client.post(
        "/api/rag/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 400

    large = client.post(
        "/api/rag/documents/upload",
        files={"file": ("large.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )
    assert large.status_code == 400
