from datetime import date as date_type
from datetime import datetime
from calendar import monthrange
from uuid import uuid4

from ..db import get_conn
from ..errors import bad_request, not_found
from ..schemas import PlanCreate, PlanOut, PlanUpdate
from .calendar_snapshot import bump_calendar_revision


def _normalize_date(value: str) -> str:
    try:
        return date_type.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise bad_request("date must use YYYY-MM-DD format") from exc


def _normalize_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise bad_request("time must use HH:MM format") from exc


def _normalize_content(content: str | None, title: str | None) -> str:
    value = (content or title or "").strip()
    if not value:
        raise bad_request("plan content cannot be empty")
    return value


def _normalize_result(result: str | None, completion: str | None) -> str:
    return result if result is not None else completion or ""


def _to_plan(row) -> PlanOut:
    return PlanOut(
        id=row["id"],
        date=row["date"],
        time=row["time"],
        content=row["content"],
        done=bool(row["done"]),
        result=row["result"],
        priority=row["priority"],
        estimatedMinutes=row["estimated_minutes"],
        source=row["source"],
        sourceKey=row["source_key"] if "source_key" in row.keys() else "",
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def list_plans(plan_date: str) -> list[PlanOut]:
    normalized_date = _normalize_date(plan_date)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM plans
            WHERE date = ?
            ORDER BY time ASC, created_at ASC
            """,
            (normalized_date,),
        ).fetchall()
    return [_to_plan(row) for row in rows]


def list_month_plans(year: int, month: int) -> list[PlanOut]:
    if month < 1 or month > 12:
        raise bad_request("month must be between 1 and 12")
    try:
        start = date_type(year, month, 1)
    except ValueError as exc:
        raise bad_request("year and month must form a valid date") from exc
    last_day = monthrange(year, month)[1]
    end = date_type(year, month, last_day)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM plans
            WHERE date >= ? AND date <= ?
            ORDER BY date ASC, time ASC, created_at ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_to_plan(row) for row in rows]


def create_plan(payload: PlanCreate) -> PlanOut:
    normalized_date = _normalize_date(payload.date)
    normalized_time = _normalize_time(payload.time)
    content = _normalize_content(payload.content, payload.title)
    result = _normalize_result(payload.result, payload.completion)
    plan_id = str(uuid4())
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO plans(
              id, date, time, content, done, result, priority, estimated_minutes, source,
              source_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                plan_id,
                normalized_date,
                normalized_time,
                content,
                int(payload.done),
                result,
                payload.priority,
                payload.estimated_minutes,
                payload.source,
                payload.source_key.strip(),
            ),
        ).fetchone()
        bump_calendar_revision(conn)
    return _to_plan(row)


def update_plan(plan_id: str, payload: PlanUpdate) -> PlanOut:
    updates: dict[str, object] = {}
    if payload.date is not None:
        updates["date"] = _normalize_date(payload.date)
    if payload.time is not None:
        updates["time"] = _normalize_time(payload.time)
    if payload.content is not None or payload.title is not None:
        updates["content"] = _normalize_content(payload.content, payload.title)
    if payload.done is not None:
        updates["done"] = int(payload.done)
    if payload.result is not None or payload.completion is not None:
        updates["result"] = _normalize_result(payload.result, payload.completion)
    if payload.priority is not None:
        updates["priority"] = payload.priority
    if payload.estimated_minutes is not None:
        updates["estimated_minutes"] = payload.estimated_minutes
    if payload.source is not None:
        updates["source"] = payload.source
    if payload.source_key is not None:
        updates["source_key"] = payload.source_key.strip()

    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not exists:
            raise not_found("plan does not exist")
        if updates:
            assignments = ", ".join(f"{field} = ?" for field in updates)
            conn.execute(
                f"""
                UPDATE plans
                SET {assignments}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (*updates.values(), plan_id),
            )
            bump_calendar_revision(conn)
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return _to_plan(row)


def delete_plan(plan_id: str) -> None:
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        if cursor.rowcount == 0:
            raise not_found("plan does not exist")
        bump_calendar_revision(conn)


def delete_all_plans() -> int:
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM plans")
        if cursor.rowcount:
            bump_calendar_revision(conn)
        return int(cursor.rowcount or 0)


def upsert_calendar_plans(items: list[dict], *, expected_revision: int) -> tuple[list[tuple[str, PlanOut]], int]:
    results: list[tuple[str, PlanOut]] = []
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()
        if int(current["revision"] if current else 0) != expected_revision:
            raise ValueError("Calendar revision is stale")
        for item in items:
            title = str(item.get("title") or "").strip()
            source_key = str(item.get("sourceKey") or "").strip()
            if not title or not source_key:
                raise ValueError("Calendar event requires title and sourceKey")
            target_date = _normalize_date(str(item.get("date") or ""))
            target_time = _normalize_time(str(item.get("time") or "09:00"))
            estimated = max(1, int(item.get("estimatedMinutes") or 30))
            existing = conn.execute("SELECT id FROM plans WHERE source_key = ?", (source_key,)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE plans SET date = ?, time = ?, content = ?, result = ?, priority = 'medium',
                       estimated_minutes = ?, source = 'ai', updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (target_date, target_time, title, str(item.get("description") or ""), estimated, existing["id"]),
                )
                state, plan_id = "updated", existing["id"]
            else:
                plan_id = str(uuid4())
                conn.execute(
                    """INSERT INTO plans(id, date, time, content, result, priority, estimated_minutes, source, source_key)
                       VALUES (?, ?, ?, ?, ?, 'medium', ?, 'ai', ?)""",
                    (plan_id, target_date, target_time, title, str(item.get("description") or ""), estimated, source_key),
                )
                state = "created"
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
            results.append((state, _to_plan(row)))
        revision = bump_calendar_revision(conn) if results else expected_revision
    return results, revision
