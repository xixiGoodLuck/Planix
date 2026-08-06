from ..db import get_conn
from ..schemas import MonthNoteOut, MonthNotePut


def _to_month_note(row, year: int, month: int) -> MonthNoteOut:
    if row:
        return MonthNoteOut(year=year, month=month, content=row["content"], updatedAt=row["updated_at"])
    return MonthNoteOut(year=year, month=month, content="", updatedAt="")


def get_month_note(year: int, month: int) -> MonthNoteOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content, updated_at FROM month_notes WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
    return _to_month_note(row, year, month)


def upsert_month_note(payload: MonthNotePut) -> MonthNoteOut:
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO month_notes(year, month, content, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(year, month)
            DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
            RETURNING content, updated_at
            """,
            (payload.year, payload.month, payload.content),
        ).fetchone()
    return _to_month_note(row, payload.year, payload.month)
