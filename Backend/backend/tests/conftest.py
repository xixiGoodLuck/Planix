import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

os.environ["PLANIX_SECRET_STORE"] = "memory"
os.environ["USE_REAL_LLM"] = "0"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("AI_API_KEY", None)

from app.main import app  # noqa: E402
from app.services.secret_store import get_secret_store, provider_secret_key  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'planix-test.db'}")
    monkeypatch.setenv("USE_REAL_LLM", "0")
    monkeypatch.setenv("PLANIX_SECRET_STORE", "memory")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    store = get_secret_store()
    providers = ("deepseek", "zhipu_glm", "kimi", "openai", "custom", "local")
    for provider in providers:
        store.delete(provider_secret_key(provider))
    yield
    for provider in providers:
        store.delete(provider_secret_key(provider))


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
