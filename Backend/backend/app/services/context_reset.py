import sqlite3
from collections.abc import Callable

from ..db import get_conn


class ContextResetService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection] = get_conn):
        self._connection_factory = connection_factory

    @staticmethod
    def _count(conn: sqlite3.Connection, table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def stats(self) -> dict[str, int]:
        conn = self._connection_factory()
        try:
            return {
                "conversations": self._count(conn, "command_threads"),
                "planning_sessions": self._count(conn, "planning_sessions"),
                "artifacts": self._count(conn, "planning_artifacts"),
                "memories": self._count(conn, "user_model_memories")
                + self._count(conn, "user_planning_hypotheses"),
            }
        finally:
            conn.close()

    def reset(self, *, clear_memory: bool = False) -> dict[str, int]:
        conn = self._connection_factory()
        try:
            conn.execute("BEGIN IMMEDIATE")
            deleted = {
                "deleted_threads": self._count(conn, "command_threads"),
                "deleted_sessions": self._count(conn, "planning_sessions"),
                "deleted_artifacts": self._count(conn, "planning_artifacts"),
                "deleted_events": self._count(conn, "harness_events"),
                "deleted_memories": 0,
            }

            # Delete children first so the operation remains valid even for
            # databases whose foreign-key actions predate the current schema.
            for table in (
                "command_approvals",
                "command_actions",
                "command_drafts",
                "command_messages",
                "command_threads",
                "harness_events",
                "harness_states",
                "agent_messages",
                "agent_decisions",
                "planning_artifacts",
                "planning_sessions",
            ):
                conn.execute(f"DELETE FROM {table}")

            if clear_memory:
                deleted["deleted_memories"] = self._count(conn, "user_model_memories") + self._count(
                    conn, "user_planning_hypotheses"
                )
                conn.execute("DELETE FROM user_model_memories")
                conn.execute("DELETE FROM user_planning_hypotheses")

            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
