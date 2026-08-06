from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from ..db import get_conn
from ..errors import bad_request, not_found
from ..schemas import MemoryCreate, MemoryItemOut, MemoryKind, MemoryResultGroup, MemorySearchResult, MemoryUpdate


MEMORY_KIND_LABELS: dict[str, str] = {
    "note": "个人记录",
    "material": "知识资料",
    "planning_history": "规划档案",
    "preference": "偏好约束",
    "review": "复盘反馈",
}
MEMORY_KIND_ORDER = ["note", "material", "planning_history", "preference", "review"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean_title(title: str, content: str, kind: str) -> str:
    title = title.strip()
    if title:
        return title[:200]
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first_line:
        return first_line[:80]
    return MEMORY_KIND_LABELS.get(kind, "记忆")


def _clean_summary(summary: str, content: str) -> str:
    summary = summary.strip()
    if summary:
        return summary[:500]
    return " ".join(content.split())[:220]


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text)]


def _fts_query(text: str) -> str:
    tokens = tokenize(text)
    if not tokens:
        return ""
    escaped = [token.replace('"', '""') for token in tokens[:12]]
    return " OR ".join(f'"{token}"' for token in escaped)


def _kind_filter(kinds: Iterable[str] | None) -> list[str]:
    if not kinds:
        return []
    result: list[str] = []
    for kind in kinds:
        if kind in MEMORY_KIND_LABELS and kind not in result:
            result.append(kind)
    return result


class MemoryService:
    def create_memory(self, payload: MemoryCreate) -> MemoryItemOut:
        content = payload.content.strip()
        if not content:
            raise bad_request("content cannot be empty")
        kind = payload.kind
        title = _clean_title(payload.title, content, kind)
        summary = _clean_summary(payload.summary, content)
        tags = payload.tags
        now = _now()

        with get_conn() as conn:
            existing_id = ""
            if payload.source_key:
                row = conn.execute(
                    "SELECT id FROM memories WHERE kind = ? AND source_key = ? LIMIT 1",
                    (kind, payload.source_key),
                ).fetchone()
                existing_id = row["id"] if row else ""

            memory_id = existing_id or str(uuid4())
            if existing_id:
                conn.execute(
                    """
                    UPDATE memories
                    SET title = ?, content = ?, summary = ?, tags_json = ?, source = ?,
                        source_id = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        content,
                        summary,
                        _dump(tags),
                        payload.source,
                        payload.source_id,
                        _dump(payload.metadata),
                        now,
                        memory_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO memories(
                      id, kind, title, content, summary, tags_json, source,
                      source_id, source_key, metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        kind,
                        title,
                        content,
                        summary,
                        _dump(tags),
                        payload.source,
                        payload.source_id,
                        payload.source_key,
                        _dump(payload.metadata),
                        now,
                        now,
                    ),
                )
            self._sync_fts(conn, memory_id)
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_item(row)

    def update_memory(self, memory_id: str, payload: MemoryUpdate) -> MemoryItemOut:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                raise not_found("memory not found")
            current = self._row_to_item(row)
            kind = payload.kind or current.kind
            content = payload.content if payload.content is not None else current.content
            if not content.strip():
                raise bad_request("content cannot be empty")
            title = _clean_title(payload.title if payload.title is not None else current.title, content, kind)
            summary = _clean_summary(payload.summary if payload.summary is not None else current.summary, content)
            tags = payload.tags if payload.tags is not None else current.tags
            source = payload.source or current.source
            source_id = payload.source_id if payload.source_id is not None else current.source_id
            source_key = payload.source_key if payload.source_key is not None else current.source_key
            metadata = payload.metadata if payload.metadata is not None else current.metadata
            now = _now()
            conn.execute(
                """
                UPDATE memories
                SET kind = ?, title = ?, content = ?, summary = ?, tags_json = ?,
                    source = ?, source_id = ?, source_key = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    kind,
                    title,
                    content.strip(),
                    summary,
                    _dump(tags),
                    source,
                    source_id,
                    source_key,
                    _dump(metadata),
                    now,
                    memory_id,
                ),
            )
            self._sync_fts(conn, memory_id)
            updated = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_item(updated)

    def delete_memory(self, memory_id: str) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def get_memory(self, memory_id: str) -> MemoryItemOut:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            raise not_found("memory not found")
        return self._row_to_item(row)

    def get_by_source_key(self, kind: str, source_key: str) -> MemoryItemOut | None:
        if kind not in MEMORY_KIND_LABELS or not source_key:
            return None
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE kind = ? AND source_key = ? LIMIT 1",
                (kind, source_key),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list_memories(self, *, kinds: Iterable[str] | None = None, limit: int = 100) -> list[MemoryItemOut]:
        kinds_filter = _kind_filter(kinds)
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        if kinds_filter:
            sql += f" WHERE kind IN ({','.join('?' for _ in kinds_filter)})"
            params.extend(kinds_filter)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def search_memories(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 20,
    ) -> list[MemoryItemOut]:
        kinds_filter = _kind_filter(kinds)
        cleaned = query.strip()
        if not cleaned:
            return self.list_memories(kinds=kinds_filter, limit=limit)

        rows = self._search_fts(cleaned, kinds_filter, limit)
        if not rows:
            rows = self._search_like(cleaned, kinds_filter, limit)

        seen: set[tuple[str, str]] = set()
        items: list[MemoryItemOut] = []
        for row in rows:
            key = (row["kind"], row["id"])
            if key in seen:
                continue
            seen.add(key)
            items.append(self._row_to_item(row))
        return items

    def search_memories_grouped(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 20,
    ) -> MemorySearchResult:
        results = self.search_memories(query, kinds=kinds, limit=limit)
        grouped: dict[str, list[MemoryItemOut]] = {kind: [] for kind in MEMORY_KIND_ORDER}
        for item in results:
            grouped.setdefault(item.kind, []).append(item)
        groups = [
            MemoryResultGroup(kind=kind, title=MEMORY_KIND_LABELS[kind], items=items)
            for kind in MEMORY_KIND_ORDER
            if (items := grouped.get(kind))
        ]
        parts = [f"{group.title} {len(group.items)} 条" for group in groups]
        summary = "找到" + "、".join(parts) + "。" if parts else "没有找到匹配的记忆。"
        return MemorySearchResult(query=query, summary=summary, groups=groups, results=results)

    def _sync_fts(self, conn: sqlite3.Connection, memory_id: str) -> None:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
        if not row:
            return
        conn.execute(
            """
            INSERT INTO memories_fts(memory_id, kind, title, content, summary, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["kind"],
                row["title"],
                row["content"],
                row["summary"],
                " ".join(_json_list(row["tags_json"])),
            ),
        )

    def _search_fts(self, query_text: str, kinds: list[str], limit: int) -> list[sqlite3.Row]:
        query = _fts_query(query_text)
        if not query:
            return []
        params: list[Any] = [query]
        where = "memories_fts MATCH ?"
        if kinds:
            where += f" AND memories.kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        params.append(max(1, min(limit, 100)))
        try:
            with get_conn() as conn:
                return conn.execute(
                    f"""
                    SELECT memories.*
                    FROM memories_fts
                    JOIN memories ON memories.id = memories_fts.memory_id
                    WHERE {where}
                    ORDER BY bm25(memories_fts)
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        except sqlite3.OperationalError:
            return []

    def _search_like(self, query_text: str, kinds: list[str], limit: int) -> list[sqlite3.Row]:
        terms = tokenize(query_text) or [query_text]
        clauses = []
        params: list[Any] = []
        for term in terms[:8]:
            pattern = f"%{term}%"
            clauses.append("(title LIKE ? OR content LIKE ? OR summary LIKE ? OR tags_json LIKE ?)")
            params.extend([pattern, pattern, pattern, pattern])
        where = " OR ".join(clauses) if clauses else "1=1"
        if kinds:
            where = f"({where}) AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        params.append(max(1, min(limit, 100)))
        with get_conn() as conn:
            return conn.execute(
                f"""
                SELECT *
                FROM memories
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

    def _row_to_item(self, row: sqlite3.Row | None) -> MemoryItemOut:
        if not row:
            raise not_found("memory not found")
        return MemoryItemOut(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            content=row["content"],
            summary=row["summary"],
            tags=_json_list(row["tags_json"]),
            source=row["source"],
            sourceId=row["source_id"],
            sourceKey=row["source_key"],
            metadata=_json_object(row["metadata_json"]),
            createdAt=row["created_at"],
            updatedAt=row["updated_at"],
        )
