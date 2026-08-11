import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import close_db_pool, open_db_pool
from .learning.runtime.bootstrap import get_learning_runtime_bootstrap
from .routers import command, context_settings, health, learning, month_notes, planning, plans, settings

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    open_db_pool()
    logger.info("Planix PostgreSQL connection pool is ready")
    bootstrap = get_learning_runtime_bootstrap()
    try:
        report = bootstrap.startup()
        learning.configure_learning_runtime_factory(
            bootstrap.create_runtime,
            health_provider=bootstrap.health,
        )
        log = logger.info if report.status == "ready" else logger.warning
        log(
            "Planix Learning Runtime startup status=%s unavailable=%s",
            report.status,
            [
                item.component
                for item in report.checks
                if item.status == "unavailable"
            ],
        )
        yield
    finally:
        learning.shutdown_learning_runtime_manager()
        bootstrap.shutdown()
        close_db_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="Planix API", version=APP_VERSION, lifespan=lifespan)

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
    app.include_router(settings.router)
    app.include_router(context_settings.router)
    app.include_router(learning.router)

    logger.info("Planix API configured version=%s database=postgresql", APP_VERSION)
    return app


app = create_app()
