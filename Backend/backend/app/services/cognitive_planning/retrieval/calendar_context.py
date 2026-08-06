from __future__ import annotations

from datetime import date, timedelta

from ....db import get_conn
from ..contracts import CalendarConstraint


class CalendarContextRetriever:
    def retrieve(self, start_date: str | None = None, *, days: int = 30) -> list[CalendarConstraint]:
        try:
            start = date.fromisoformat(start_date or date.today().isoformat())
        except ValueError:
            start = date.today()
        end = start + timedelta(days=max(1, min(days, 120)))
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, date, time, content, estimated_minutes
                FROM plans
                WHERE date >= ? AND date <= ?
                ORDER BY date, time
                LIMIT 120
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [
            CalendarConstraint(
                date=row["date"],
                sourceId=row["id"],
                statement=f"{row['time']} {row['content']} ({int(row['estimated_minutes'] or 0)} min)",
            )
            for row in rows
        ]
