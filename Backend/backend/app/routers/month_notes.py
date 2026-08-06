from fastapi import APIRouter

from ..schemas import MonthNoteOut, MonthNotePut
from ..services.month_notes import get_month_note, upsert_month_note

router = APIRouter(prefix="/api/month-notes", tags=["month-notes"])


@router.get("", response_model=MonthNoteOut)
def read_month_note(year: int, month: int) -> MonthNoteOut:
    return get_month_note(year, month)


@router.put("", response_model=MonthNoteOut)
def save_month_note(payload: MonthNotePut) -> MonthNoteOut:
    return upsert_month_note(payload)
