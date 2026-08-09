from concurrent.futures import ThreadPoolExecutor

import pytest

from app.cognitive_planning.artifact_audit import PlanningArtifactAuditStore
from app.cognitive_planning.persistence import PlanningPersistence
from app import db as database
from app.db import get_conn
from app.harness.persistence import HarnessCheckpointConflict, HarnessStateRepository


def _session() -> str:
    return PlanningPersistence().create(thread_id="thread-concurrency", user_input="goal")


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


def test_parallel_artifact_versions_are_unique_and_monotonic() -> None:
    session_id = _session()

    def write(index: int) -> int:
        artifact = PlanningArtifactAuditStore().record_artifact(
            session_id,
            owner_agent="Understanding Agent",
            artifact_type="understanding_snapshot",
            content={"index": index},
        )
        return artifact.version

    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(write, range(20)))
    assert sorted(versions) == list(range(1, 21))


def test_harness_checkpoint_compare_and_swap_allows_one_writer() -> None:
    session_id = _session()
    repository = HarnessStateRepository()
    state = repository.create_or_load(session_id)

    def checkpoint():
        return repository.checkpoint(
            state,
            event_type="harness_decision",
            expected_version=state.checkpoint_version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = []
        for future in [pool.submit(checkpoint), pool.submit(checkpoint)]:
            try:
                outcomes.append(future.result())
            except HarnessCheckpointConflict as exc:
                outcomes.append(exc)
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, HarnessCheckpointConflict) for item in outcomes) == 1
    assert len(repository.events(session_id)) == 1


def test_planning_session_compare_and_swap_rejects_stale_version() -> None:
    session_id = _session()
    persistence = PlanningPersistence()
    row = persistence.get_row(session_id)
    persistence.update(session_id, status="planning", expected_version=row["version"])
    with pytest.raises(ValueError, match="version changed"):
        persistence.update(session_id, status="cancelled", expected_version=row["version"])


def test_transactions_do_not_expose_uncommitted_rows() -> None:
    session_id = _session()
    with get_conn() as conn:
        assert conn.execute("SELECT id FROM planning_sessions WHERE id = %s", (session_id,)).fetchone()
