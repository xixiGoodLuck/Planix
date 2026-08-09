import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT / "backend"))
sys.path.insert(0, str(BACKEND_ROOT.parent))

database_url = os.getenv("DATABASE_URL", "").strip()
database_name = urlsplit(database_url).path.removeprefix("/").casefold()
if os.getenv("PLANIX_TEST_DATABASE") != "1" or "test" not in database_name:
    raise RuntimeError(
        "Backend tests require PLANIX_TEST_DATABASE=1 and a PostgreSQL DATABASE_URL whose database name contains 'test'."
    )

os.environ["PLANIX_SECRET_STORE"] = "memory"
os.environ["USE_REAL_LLM"] = "0"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("AI_API_KEY", None)

from app.db import REQUIRED_TABLES, close_db_pool, get_conn, open_db_pool  # noqa: E402
from app.main import app  # noqa: E402
from app.services.secret_store import get_secret_store, provider_secret_key  # noqa: E402


def _truncate() -> None:
    tables = ", ".join(sorted(REQUIRED_TABLES - {"calendar_state"}))
    with get_conn() as conn:
        conn.execute(f"TRUNCATE TABLE {tables} CASCADE")
        conn.execute("TRUNCATE TABLE calendar_state CASCADE")
        conn.execute("INSERT INTO calendar_state(id, revision) VALUES ('local', 0)")


@pytest.fixture(scope="session", autouse=True)
def migrated_postgresql_schema():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    open_db_pool()
    _truncate()
    yield
    open_db_pool()
    _truncate()
    close_db_pool()


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    open_db_pool()
    _truncate()
    monkeypatch.setenv("USE_REAL_LLM", "0")
    monkeypatch.setenv("PLANIX_SECRET_STORE", "memory")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    store = get_secret_store()
    providers = ("deepseek", "zhipu_glm", "kimi", "openai", "custom", "local")
    for provider in providers:
        store.delete(provider_secret_key(provider))
    yield
    open_db_pool()
    _truncate()
    for provider in providers:
        store.delete(provider_secret_key(provider))


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
