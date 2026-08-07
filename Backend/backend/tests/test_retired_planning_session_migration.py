from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.migrations.archive_retired_planning_sessions import archive_database


def _database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE planning_sessions (
                id TEXT PRIMARY KEY, status TEXT, business_status TEXT,
                runtime_status TEXT, cognitive_metadata_json TEXT, updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO planning_sessions VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("retired", "MODEL_UNAVAILABLE", "goal_understood", "running", '{"engineVersion":"cognitive-os-v1","raw":{"kept":true}}', "old"),
                ("formal", "planning", "goal_understood", "running", '{"engineVersion":"planning-engine-2"}', "new"),
            ],
        )


def test_archive_is_dry_run_backed_up_and_idempotent(tmp_path):
    database = tmp_path / "planix.db"
    backups = tmp_path / "backups"
    _database(database)

    dry_run = archive_database(database, apply=False, backup_dir=backups)
    assert dry_run["found"] == 1
    assert dry_run["archived"] == 0

    applied = archive_database(database, apply=True, backup_dir=backups)
    assert applied["archived"] == 1
    assert Path(applied["backup"]).exists()

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT status, business_status, runtime_status, cognitive_metadata_json FROM planning_sessions WHERE id='retired'"
        ).fetchone()
    metadata = json.loads(row[3])
    assert row[:3] == ("ARCHIVED", "blocked", "idle")
    assert metadata["migratedFrom"] == "cognitive-os-v1"
    assert metadata["raw"] == {"kept": True}

    repeated = archive_database(database, apply=True, backup_dir=backups)
    assert repeated["found"] == 0
    assert repeated["archived"] == 0
    assert repeated["alreadyArchived"] == 1
