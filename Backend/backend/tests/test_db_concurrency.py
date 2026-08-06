import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import db as database


def _use_database(monkeypatch, path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")


def test_file_connections_use_wal_busy_timeout_and_foreign_keys(tmp_path, monkeypatch) -> None:
    _use_database(monkeypatch, tmp_path / "connection-settings.db")

    for _ in range(2):
        conn = database.get_conn()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()


def test_direct_memory_initialization_remains_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        database.init_db(conn)
        database.init_db(conn)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'planning_sessions'"
        ).fetchone()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        conn.close()


def test_schema_initialization_is_once_per_live_file_and_detects_path_reuse(
    tmp_path,
    monkeypatch,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    original_init_db = database.init_db
    init_count = 0
    count_lock = threading.Lock()
    start = threading.Barrier(2)

    def counted_init_db(conn: sqlite3.Connection) -> None:
        nonlocal init_count
        with count_lock:
            init_count += 1
        original_init_db(conn)

    monkeypatch.setattr(database, "init_db", counted_init_db)
    _use_database(monkeypatch, first_path)

    def open_first_database() -> None:
        start.wait(timeout=5)
        conn = database.get_conn()
        try:
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'planning_sessions'"
            ).fetchone()
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_first_database) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    assert init_count == 1

    _use_database(monkeypatch, second_path)
    conn = database.get_conn()
    conn.close()
    assert init_count == 2

    _use_database(monkeypatch, first_path)
    conn = database.get_conn()
    conn.close()
    assert init_count == 2

    first_path.unlink()
    conn = database.get_conn()
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'planning_sessions'"
        ).fetchone()
    finally:
        conn.close()
    assert init_count == 3


def test_two_threads_create_isolated_sessions_and_write_without_lock_errors(
    tmp_path,
    monkeypatch,
) -> None:
    _use_database(monkeypatch, tmp_path / "concurrent-sessions.db")
    open_barrier = threading.Barrier(2)
    write_barrier = threading.Barrier(2)
    committed_barrier = threading.Barrier(2)

    def create_session(worker: str) -> tuple[str, str, list[str]]:
        open_barrier.wait(timeout=5)
        conn = database.get_conn()
        thread_id = f"thread-{worker}"
        session_id = f"session-{worker}"
        messages = [f"{worker}-message-{index}" for index in range(12)]
        try:
            write_barrier.wait(timeout=10)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO command_threads(id, title) VALUES (?, ?)",
                (thread_id, f"Thread {worker}"),
            )
            conn.execute(
                """
                INSERT INTO planning_sessions(id, thread_id, status, user_input)
                VALUES (?, ?, 'understanding_goal', ?)
                """,
                (session_id, thread_id, f"goal-{worker}"),
            )
            for index, content in enumerate(messages):
                conn.execute(
                    """
                    INSERT INTO command_messages(id, thread_id, role, content)
                    VALUES (?, ?, 'user', ?)
                    """,
                    (f"message-{worker}-{index}", thread_id, content),
                )
            # Hold the first writer briefly so the competing writer must honor the
            # configured busy timeout instead of failing immediately.
            time.sleep(0.05)
            conn.commit()
            committed_barrier.wait(timeout=10)

            own_session = conn.execute(
                "SELECT id FROM planning_sessions WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            own_messages = [
                row["content"]
                for row in conn.execute(
                    "SELECT content FROM command_messages WHERE thread_id = ? ORDER BY id",
                    (thread_id,),
                ).fetchall()
            ]
            assert own_session["id"] == session_id
            assert set(own_messages) == set(messages)
            assert all(content.startswith(f"{worker}-") for content in own_messages)
            return thread_id, session_id, own_messages
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(create_session, worker)
            for worker in ("alpha", "beta")
        ]
        created = [future.result(timeout=15) for future in results]

    assert {item[0] for item in created} == {"thread-alpha", "thread-beta"}
    assert {item[1] for item in created} == {"session-alpha", "session-beta"}

    conn = database.get_conn()
    try:
        sessions = conn.execute(
            "SELECT id, thread_id, user_input FROM planning_sessions ORDER BY id"
        ).fetchall()
        message_counts = conn.execute(
            """
            SELECT thread_id, COUNT(*) AS total
            FROM command_messages
            GROUP BY thread_id
            ORDER BY thread_id
            """
        ).fetchall()
    finally:
        conn.close()

    assert [dict(row) for row in sessions] == [
        {
            "id": "session-alpha",
            "thread_id": "thread-alpha",
            "user_input": "goal-alpha",
        },
        {
            "id": "session-beta",
            "thread_id": "thread-beta",
            "user_input": "goal-beta",
        },
    ]
    assert [dict(row) for row in message_counts] == [
        {"thread_id": "thread-alpha", "total": 12},
        {"thread_id": "thread-beta", "total": 12},
    ]


def test_wal_reader_does_not_observe_an_uncommitted_session(tmp_path, monkeypatch) -> None:
    _use_database(monkeypatch, tmp_path / "wal-read-isolation.db")
    write_started = threading.Event()
    read_finished = threading.Event()

    def write_session() -> None:
        conn = database.get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO command_threads(id, title) VALUES ('thread-writer', 'Writer')"
            )
            conn.execute(
                """
                INSERT INTO planning_sessions(id, thread_id, status, user_input)
                VALUES ('session-writer', 'thread-writer', 'understanding_goal', 'private goal')
                """
            )
            write_started.set()
            assert read_finished.wait(timeout=5)
            conn.commit()
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(write_session)
        assert write_started.wait(timeout=5)

        reader = database.get_conn()
        try:
            assert reader.execute(
                "SELECT id FROM planning_sessions WHERE id = 'session-writer'"
            ).fetchone() is None
        finally:
            reader.close()
            read_finished.set()

        writer.result(timeout=10)

    reader = database.get_conn()
    try:
        row = reader.execute(
            "SELECT thread_id, user_input FROM planning_sessions WHERE id = 'session-writer'"
        ).fetchone()
    finally:
        reader.close()

    assert dict(row) == {"thread_id": "thread-writer", "user_input": "private goal"}


def test_schema_initialization_is_serialized_across_reload_processes(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "reload-processes.db"
    _use_database(monkeypatch, database_path)
    setup = database.get_conn()
    setup.close()

    # Remove one additive column to model a database created by an older build.
    raw = sqlite3.connect(database_path)
    try:
        raw.execute("ALTER TABLE command_messages DROP COLUMN kind")
        raw.commit()
    finally:
        raw.close()

    start_flag = tmp_path / "start.flag"
    backend_root = Path(__file__).resolve().parents[1]
    child_code = r"""
import json
import sys
import time
from pathlib import Path

from app import db

start_flag = Path(sys.argv[1])
while not start_flag.exists():
    time.sleep(0.01)

original = db.init_db

def measured_init(conn):
    started = time.time_ns()
    original(conn)
    time.sleep(0.25)
    completed = time.time_ns()
    print(json.dumps({"started": started, "completed": completed}), flush=True)

db.init_db = measured_init
conn = db.get_conn()
conn.close()
"""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path}"
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(backend_root), env.get("PYTHONPATH", "")) if item
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child_code, str(start_flag)],
            cwd=backend_root.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    start_flag.write_text("start", encoding="utf-8")

    intervals: list[dict[str, int]] = []
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr
        lines = [line for line in stdout.splitlines() if line.strip()]
        assert lines
        intervals.append(json.loads(lines[-1]))

    first, second = sorted(intervals, key=lambda item: item["started"])
    assert first["completed"] <= second["started"]

    conn = database.get_conn()
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(command_messages)").fetchall()
        }
    finally:
        conn.close()
    assert "kind" in columns
