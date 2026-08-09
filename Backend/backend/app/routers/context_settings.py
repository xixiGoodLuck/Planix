import logging

from fastapi import APIRouter, HTTPException

from ..schemas import ContextResetOut, ContextResetRequest, ContextStatsOut
from ..services.context_reset import ContextResetService

logger = logging.getLogger("planix.api.context_settings")
router = APIRouter(prefix="/api/settings/context", tags=["context-settings"])


@router.get("", response_model=ContextStatsOut)
def read_context_stats() -> ContextStatsOut:
    return ContextStatsOut(**ContextResetService().stats())


@router.delete("", response_model=ContextResetOut)
def reset_context(payload: ContextResetRequest) -> ContextResetOut:
    try:
        return ContextResetOut(**ContextResetService().reset(clear_memory=payload.clear_memory))
    except Exception as exc:
        logger.error("AI context reset failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear AI context") from exc
