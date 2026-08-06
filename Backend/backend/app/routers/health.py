import os
from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()

APP_VERSION = os.getenv("PLANIX_API_VERSION", "3.11-demo-reliability")
STARTUP_TIME = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
FEATURES = {
    "planQualityGate": True,
    "contextAwareRefinement": True,
    "calendarDraftContextRecovery": True,
    "demoMetrics": True,
}


@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "name": "planix-api",
        "app": "planix-api",
        "pid": os.getpid(),
        "version": APP_VERSION,
        "startupTime": STARTUP_TIME,
        "features": FEATURES,
    }
