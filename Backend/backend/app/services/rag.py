import json
from collections import Counter
from hashlib import sha256
from re import findall

from ..errors import bad_request
from ..schemas import AiPayload, MemoryCreate, MemoryItemOut, RagDocumentCreate, RagDocumentOut, RagIngestPayload, RagQueryOut, RagSource
from .llm import LlmClient
from .memory_store import MemoryService
from .planner import _json_object


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


def _keywords(text: str, limit: int = 6) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "you",
        "your",
        "are",
        "is",
        "to",
        "of",
        "in",
        "a",
        "an",
    }
    counts = Counter(token for token in tokenize(text) if token not in stopwords and len(token) > 1)
    return [word for word, _ in counts.most_common(limit)]


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

    def query(self, payload: AiPayload) -> RagQueryOut:
        query_text = " ".join(part for part in [payload.goal, payload.materials] if part.strip())
        sources = self.retrieve(query_text, limit=4)
        source_payload = [source.model_dump(by_alias=True) for source in sources]
        llm_result, _ = LlmClient().complete(
            "rag_query",
            (
                "You answer study planning questions with the provided sources. "
                "Return strict JSON only with keys: answer and keywords. "
                "keywords is an array of short strings. If sources are empty, say what material is missing."
            ),
            json.dumps(
                {
                    "goal": payload.goal,
                    "questionOrMaterials": payload.materials,
                    "sources": source_payload,
                },
                ensure_ascii=False,
            ),
            task_type="memory_query",
        )
        if llm_result:
            parsed = _json_object(llm_result.content)
            if parsed:
                answer = str(parsed.get("answer") or "").strip()
                keywords = parsed.get("keywords")
                if not isinstance(keywords, list):
                    keywords = _keywords(" ".join(source.chunk for source in sources))
                if answer:
                    return RagQueryOut(
                        mode="llm",
                        answer=answer,
                        sources=sources,
                        keywords=[str(item) for item in keywords[:8]],
                        provider=llm_result.provider,
                        model=llm_result.model,
                    )

        return RagQueryOut(
            mode="mock",
            answer=self._mock_answer(sources),
            sources=sources,
            keywords=_keywords(" ".join(source.chunk for source in sources)),
        )

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

    def _mock_answer(self, sources: list[RagSource]) -> str:
        if not sources:
            return "资料库还没有命中内容。请先保存 JD、课程笔记、面经或项目资料，再用问题触发检索。"
        titles = "、".join(dict.fromkeys(source.title for source in sources))
        return (
            f"根据资料库命中的《{titles}》片段，建议优先把高频技能、项目产出和时间约束转成可执行任务，"
            "并在计划中标注对应证据来源，方便后续复盘和面试讲解。"
        )
