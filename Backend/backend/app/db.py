import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, time
from typing import Any, Iterator
from urllib.parse import urlsplit

from psycopg import Connection
from psycopg.rows import RowFactory
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


ALEMBIC_REVISION = "20260811_02"
REQUIRED_TABLES = {
    "plans",
    "month_notes",
    "planning_sessions",
    "planning_artifacts",
    "agent_decisions",
    "agent_messages",
    "harness_states",
    "harness_events",
    "ai_settings",
    "ai_provider_configs",
    "ai_model_routing_rules",
    "calendar_state",
    "user_preferences",
    "user_planning_hypotheses",
    "user_model_memories",
    "ai_runs",
    "command_threads",
    "command_messages",
    "command_drafts",
    "command_actions",
    "command_approvals",
    "learning_runs",
    "learning_artifacts",
    "learning_checkpoints",
    "learning_resume_events",
    "learning_video_resources",
    "learning_transcript_sources",
    "learning_transcript_segments",
}


class DatabaseConfigurationError(RuntimeError):
    pass


class DatabaseUnavailableError(RuntimeError):
    pass


class DatabaseSchemaError(RuntimeError):
    pass


_pool: ConnectionPool | None = None
_pool_lock = threading.RLock()


def get_database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise DatabaseConfigurationError("DATABASE_URL is required; Planix requires PostgreSQL configuration.")
    if value.startswith("postgresql+psycopg://"):
        value = "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if urlsplit(value).scheme != "postgresql":
        raise DatabaseConfigurationError("DATABASE_URL must use the postgresql:// scheme.")
    return value


def _pool_setting(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise DatabaseConfigurationError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise DatabaseConfigurationError(f"{name} must be at least {minimum}.")
    return value


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    return value


def planix_dict_row(cursor) -> RowFactory:
    if cursor.description is None:
        return lambda values: values
    names = [column.name for column in cursor.description]

    def make_row(values):
        return {name: _serialize_value(value) for name, value in zip(names, values, strict=True)}

    return make_row


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)


def open_db_pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        min_size = _pool_setting("PLANIX_DB_POOL_MIN", 1)
        max_size = _pool_setting("PLANIX_DB_POOL_MAX", 5)
        timeout = _pool_setting("PLANIX_DB_POOL_TIMEOUT", 10)
        if max_size < min_size:
            raise DatabaseConfigurationError("PLANIX_DB_POOL_MAX must be greater than or equal to PLANIX_DB_POOL_MIN.")
        pool = ConnectionPool(
            conninfo=get_database_url(),
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs={"row_factory": planix_dict_row},
            open=False,
            name="planix",
        )
        try:
            pool.open(wait=True, timeout=timeout)
        except Exception as exc:
            pool.close()
            raise DatabaseUnavailableError("PostgreSQL unavailable.") from exc
        _pool = pool
        try:
            verify_schema()
        except Exception:
            close_db_pool()
            raise
        return pool


def close_db_pool() -> None:
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
        if pool is not None:
            pool.close()


def get_db_pool() -> ConnectionPool:
    if _pool is None:
        raise DatabaseUnavailableError("PostgreSQL connection pool is not open.")
    return _pool


@contextmanager
def get_conn() -> Iterator[Connection]:
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def database_health() -> dict[str, object]:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"available": True, "database": "postgresql"}
    except Exception:
        return {"available": False, "database": "postgresql"}


def verify_schema() -> None:
    pool = get_db_pool()
    try:
        with pool.connection() as conn:
            revision_row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            table_rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
    except Exception as exc:
        raise DatabaseSchemaError("PostgreSQL schema is missing; run Alembic upgrade head.") from exc
    revision = revision_row["version_num"] if revision_row else ""
    tables = {row["table_name"] for row in table_rows}
    missing = REQUIRED_TABLES - tables
    if revision != ALEMBIC_REVISION or missing:
        raise DatabaseSchemaError("PostgreSQL schema is outdated; run Alembic upgrade head.")


def row_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
