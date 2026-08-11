import pytest

from app import db as database


def test_database_url_is_required_and_postgresql_only(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(database.DatabaseConfigurationError, match="required"):
        database.get_database_url()
    monkeypatch.setenv("DATABASE_URL", "file:embedded-database")
    with pytest.raises(database.DatabaseConfigurationError, match="postgresql"):
        database.get_database_url()


def test_unreachable_postgresql_fails_pool_startup(monkeypatch) -> None:
    original = database.get_database_url()
    database.close_db_pool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://planix:planix@127.0.0.1:1/planix_test")
    monkeypatch.setenv("PLANIX_DB_POOL_TIMEOUT", "1")
    with pytest.raises(database.DatabaseUnavailableError, match="unavailable"):
        database.open_db_pool()
    monkeypatch.setenv("DATABASE_URL", original)
    monkeypatch.setenv("PLANIX_DB_POOL_TIMEOUT", "10")
    database.open_db_pool()
