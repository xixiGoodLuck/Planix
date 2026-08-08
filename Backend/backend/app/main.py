import logging
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import get_db_path
from .routers import command, health, month_notes, planning, plans, preferences, rag, settings

APP_VERSION = "1.1.4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("planix.api")

_SENSITIVE_VALIDATION_FIELDS = {
    "apikey",
    "api_key",
    "authorization",
    "clientsecret",
    "client_secret",
    "password",
    "secret",
}


def _redact_validation_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).casefold() in _SENSITIVE_VALIDATION_FIELDS else _redact_validation_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_validation_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_validation_value(item) for item in value)
    return value


def _redact_request_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted: list[dict[str, Any]] = []
    for error in errors:
        cleaned = _redact_validation_value(error)
        location = cleaned.get("loc") if isinstance(cleaned, dict) else None
        if isinstance(location, (list, tuple)) and any(
            str(part).casefold() in _SENSITIVE_VALIDATION_FIELDS for part in location
        ):
            cleaned["input"] = "[REDACTED]"
        redacted.append(cleaned)
    return redacted


def create_app() -> FastAPI:
    app = FastAPI(title="Planix API", version=APP_VERSION)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(_request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(_redact_request_validation_errors(exc.errors()))},
        )

    allowed_origins = [
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ]

    # Scope CORS to local Vite dev/preview and Tauri/WebView2 origins. Desktop
    # production requests normally go through the Rust IPC proxy, but keeping
    # these origins listed makes local diagnostics work without using "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1):\d+$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(command.router)
    app.include_router(plans.router)
    app.include_router(month_notes.router)
    app.include_router(planning.router)
    app.include_router(rag.router)
    app.include_router(preferences.router)
    app.include_router(settings.router)

    logger.info("Planix API started version=%s db_path=%s", APP_VERSION, get_db_path())
    return app


app = create_app()
