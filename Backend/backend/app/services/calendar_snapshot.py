from __future__ import annotations

from datetime import datetime, timedelta, timezone as fixed_timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn


def normalize_timezone(value: str | None) -> str:
    candidate = (value or "Asia/Shanghai").strip() or "Asia/Shanghai"
    if candidate in {"UTC", "Etc/UTC", "Asia/Shanghai"}:
        return "UTC" if candidate == "Etc/UTC" else candidate
    try:
        ZoneInfo(candidate)
        return candidate
    except ZoneInfoNotFoundError:
        return "UTC"


def timezone_info(value: str | None) -> tzinfo:
    normalized = normalize_timezone(value)
    if normalized == "Asia/Shanghai":
        return fixed_timezone(timedelta(hours=8))
    if normalized == "UTC":
        return fixed_timezone.utc
    return ZoneInfo(normalized)


def current_calendar_revision(conn=None) -> int:
    if conn is not None:
        row = conn.execute("SELECT revision FROM calendar_state WHERE id = 'local'").fetchone()
        return int(row["revision"] if row else 0)
    with get_conn() as connection:
        return current_calendar_revision(connection)


def bump_calendar_revision(conn) -> int:
    conn.execute("UPDATE calendar_state SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 'local'")
    return current_calendar_revision(conn)


def calendar_snapshot(timezone: str | None = None) -> dict[str, object]:
    normalized = normalize_timezone(timezone)
    zone = timezone_info(normalized)
    with get_conn() as conn:
        revision = current_calendar_revision(conn)
        rows = conn.execute("SELECT id, date, time, estimated_minutes FROM plans ORDER BY date, time, id").fetchall()
    busy: list[dict[str, str]] = []
    for row in rows:
        try:
            start = datetime.fromisoformat(f"{row['date']}T{row['time']}:00").replace(tzinfo=zone)
        except ValueError:
            continue
        end = start + timedelta(minutes=max(1, int(row["estimated_minutes"] or 30)))
        busy.append({"planId": row["id"], "start": start.isoformat(), "end": end.isoformat()})
    return {
        "calendarSnapshotRef": f"calendar:{revision}",
        "calendarSnapshotVersion": revision,
        "calendarBusy": busy,
        "timezone": normalized,
    }


__all__ = ["bump_calendar_revision", "calendar_snapshot", "current_calendar_revision", "normalize_timezone", "timezone_info"]
