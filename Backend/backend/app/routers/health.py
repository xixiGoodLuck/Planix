import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from ..db import database_health
from ..version import APP_VERSION

router = APIRouter()

STARTUP_TIME = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
FEATURES = {
    "learningRuntime": True,
    "evidenceValidation": True,
    "artifactRecovery": True,
}


@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, object]:
    db = database_health()
    if not db["available"]:
        raise HTTPException(status_code=503, detail="PostgreSQL unavailable")
    return {
        "status": "ok",
        "name": "planix-api",
        "app": "planix-api",
        "pid": os.getpid(),
        "version": APP_VERSION,
        "startupTime": STARTUP_TIME,
        "features": FEATURES,
        "database": "postgresql",
    }
