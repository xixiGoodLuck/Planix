from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


FORMAL_ENGINE = "planning-engine-2"


def _metadata(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def retired_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='planning_sessions'"
    ).fetchone():
        return []
    rows = conn.execute(
        "SELECT id, status, cognitive_metadata_json FROM planning_sessions WHERE status <> 'ARCHIVED'"
    ).fetchall()
    return [
        row
        for row in rows
        if str(_metadata(row["cognitive_metadata_json"]).get("engineVersion") or "")
        != FORMAL_ENGINE
    ]


def backup_database(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{source.stem}-before-planning-retirement-{stamp}{source.suffix}"
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as backup_conn:
        source_conn.backup(backup_conn)
    return destination


def archive_database(database: Path, *, apply: bool, backup_dir: Path | None = None) -> dict:
    database = database.resolve()
    with sqlite3.connect(database) as conn:
        rows = retired_sessions(conn)
        result = {
            "database": str(database),
            "found": len(rows),
            "archived": 0,
            "alreadyArchived": conn.execute(
                "SELECT COUNT(*) FROM planning_sessions WHERE status = 'ARCHIVED'"
            ).fetchone()[0]
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='planning_sessions'"
            ).fetchone()
            else 0,
            "backup": None,
            "dryRun": not apply,
        }
        if not apply or not rows:
            return result

    backup = backup_database(database, backup_dir or database.parent / "backups")
    archived_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = retired_sessions(conn)
        for row in rows:
            metadata = _metadata(row["cognitive_metadata_json"])
            metadata.update(
                {
                    "migratedFrom": metadata.get("engineVersion") or "unknown-retired-runtime",
                    "archivePhase": "BLOCKED",
                    "archiveReason": "The retired session lacks enough verified artifacts for deterministic migration.",
                    "archivedAt": archived_at,
                }
            )
            conn.execute(
                """
                UPDATE planning_sessions
                SET status = 'ARCHIVED', business_status = 'blocked', runtime_status = 'idle',
                    cognitive_metadata_json = ?, updated_at = ?
                WHERE id = ? AND status <> 'ARCHIVED'
                """,
                (json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), archived_at, row["id"]),
            )
        conn.commit()
    result.update(archived=len(rows), backup=str(backup))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive sessions owned by retired planning runtimes.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply the archival update; default is dry-run.")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(archive_database(args.database, apply=args.apply, backup_dir=args.backup_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
