from hashlib import sha256
from re import findall

from ..errors import bad_request
from ..schemas import MemoryCreate, MemoryItemOut, RagDocumentCreate, RagDocumentOut, RagIngestPayload, RagSource
from .memory_store import MemoryService


def chunk_text(text: str, size: int = 420, overlap: int = 48) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks = []
    step = max(size - overlap, 1)
    for start in range(0, len(cleaned), step):
        chunk = cleaned[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(cleaned):
            break
    return chunks


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text)]


def _fts_query(text: str) -> str:
    tokens = tokenize(text)
    if not tokens:
        return ""
    escaped = [token.replace('"', '""') for token in tokens[:12]]
    return " OR ".join(f'"{token}"' for token in escaped)


class RagService:
    def __init__(self):
        self.memories = MemoryService()

    def create_document(self, payload: RagDocumentCreate) -> RagDocumentOut:
        title = payload.title.strip() or "Untitled material"
        content = payload.content.strip()
        if not content:
            raise bad_request("content cannot be empty")

        chunks = chunk_text(content)
        if not chunks:
            raise bad_request("content cannot be empty")

        summary = content[:220]
        content_hash = sha256(content.encode("utf-8")).hexdigest()
        memory = self.memories.create_memory(
            MemoryCreate(
                kind="material",
                title=title,
                content=content,
                summary=summary,
                source="user",
                sourceId=payload.source_type,
                sourceKey="",
                metadata={
                    "compat": "rag/documents",
                    "sourceType": payload.source_type,
                    "contentHash": content_hash,
                    "chunks": len(chunks),
                },
            )
        )
        return self._document_out(memory)

    def list_documents(self) -> list[RagDocumentOut]:
        return [self._document_out(item) for item in self.memories.list_memories(kinds=["material"], limit=200)]

    def delete_document(self, document_id: str) -> None:
        self.memories.delete_memory(document_id)

    def ingest(self, payload: RagIngestPayload) -> dict[str, int | str]:
        document = self.create_document(
            RagDocumentCreate(title=payload.title, content=payload.content, sourceType="ingest")
        )
        return {"title": document.title, "chunks": document.chunks}

    def retrieve(self, query_text: str, limit: int = 4) -> list[RagSource]:
        if not _fts_query(query_text):
            return []
        items = self.memories.search_memories(query_text, kinds=["material"], limit=max(limit * 3, limit))
        sources = self._sources_from_memories(items, query_text, limit)
        if not sources:
            return self._fallback_retrieve(query_text, limit)
        return sources

    def _fallback_retrieve(self, query_text: str, limit: int) -> list[RagSource]:
        query_terms = set(tokenize(query_text))
        if not query_terms:
            return []

        scored = []
        for item in self.memories.list_memories(kinds=["material"], limit=200):
            chunks = chunk_text(item.content)
            for index, chunk in enumerate(chunks):
                terms = set(tokenize(chunk))
                score = len(query_terms & terms)
                if score:
                    scored.append((score, item, index, chunk))

        sources = []
        for score, item, index, chunk in sorted(scored, key=lambda value: value[0], reverse=True)[:limit]:
            sources.append(
                RagSource(
                    documentId=item.id,
                    title=item.title,
                    chunk=chunk,
                    score=float(score),
                    chunkIndex=index,
                )
            )
        return sources

    def _sources_from_memories(self, items: list[MemoryItemOut], query_text: str, limit: int) -> list[RagSource]:
        query_terms = set(tokenize(query_text))
        sources = []
        for item in items:
            chunks = chunk_text(item.content) or [item.content]
            scored = []
            for index, chunk in enumerate(chunks):
                terms = set(tokenize(chunk))
                score = len(query_terms & terms)
                scored.append((score, index, chunk))
            score, index, chunk = max(scored, key=lambda value: value[0]) if scored else (0, 0, item.summary or item.content)
            sources.append(
                RagSource(
                    documentId=item.id,
                    title=item.title,
                    chunk=chunk,
                    score=float(score or 1),
                    chunkIndex=index,
                )
            )
        return sources[:limit]

    def _document_out(self, item: MemoryItemOut) -> RagDocumentOut:
        source_type = str(item.metadata.get("sourceType") or item.source_id or "paste")
        chunks = int(item.metadata.get("chunks") or len(chunk_text(item.content)) or 1)
        return RagDocumentOut(
            id=item.id,
            title=item.title,
            sourceType=source_type,
            summary=item.summary,
            chunks=chunks,
            createdAt=item.created_at,
        )
