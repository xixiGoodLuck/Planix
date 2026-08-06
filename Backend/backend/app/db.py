import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .desktop_paths import resolve_database_path


_BUSY_TIMEOUT_MS = 5_000
_DATABASE_INIT_LOCK = threading.RLock()
_initialized_databases: dict[str, tuple[int, int, int, str]] = {}


def get_db_path() -> Path:
    return resolve_database_path()


def get_conn() -> sqlite3.Connection:
    db_path = get_db_path()
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:", timeout=_BUSY_TIMEOUT_MS / 1_000)
        _configure_connection(conn)
        init_db(conn)
        conn.commit()
        return conn

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1_000)
    try:
        _configure_connection(conn)
        initialized = _ensure_file_database_initialized(conn, db_path)
        if not initialized:
            _sync_legacy_provider_config(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")


def _ensure_file_database_initialized(conn: sqlite3.Connection, db_path: Path) -> bool:
    canonical_path = db_path.expanduser().resolve(strict=False)
    cache_key = os.path.normcase(str(canonical_path))

    # Schema setup contains DDL and data migrations. Serializing it per process and
    # caching the resulting database identity keeps ordinary connections off that
    # write-heavy path while still detecting a deleted, replaced, or externally
    # migrated test database at the same filesystem location.
    with _DATABASE_INIT_LOCK:
        with _database_init_file_lock(canonical_path):
            # Re-read schema/journal state only after the cross-process lock is
            # held. A Uvicorn reload can briefly overlap its old and new worker;
            # the second process must observe migrations committed by the first
            # before deciding whether to execute idempotent schema setup.
            current_state = _database_state(conn, canonical_path)
            if _initialized_databases.get(cache_key) == current_state:
                return False

            journal_mode = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if journal_mode != "wal":
                raise sqlite3.OperationalError(
                    f"could not enable WAL journal mode for SQLite database: {canonical_path}"
                )
            init_db(conn)
            conn.commit()
            _initialized_databases[cache_key] = _database_state(conn, canonical_path)
            return True


@contextmanager
def _database_init_file_lock(db_path: Path) -> Iterator[None]:
    """Serialize schema initialization across reload workers and processes."""

    lock_path = Path(f"{db_path}.init.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1_000)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        acquired = False
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise sqlite3.OperationalError(
                        f"timed out waiting for SQLite schema initialization: {db_path}"
                    ) from exc
                time.sleep(0.05)

        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _database_state(conn: sqlite3.Connection, db_path: Path) -> tuple[int, int, int, str]:
    stat = db_path.stat()
    schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    return (stat.st_dev, stat.st_ino, schema_version, journal_mode)


def _sync_legacy_provider_config(conn: sqlite3.Connection) -> None:
    # Older builds wrote a user-supplied key only to ai_settings. Keep that narrow
    # compatibility path live without rerunning the schema and all data migrations
    # for every connection. The common path is read-only; a write occurs once only
    # when a newly written legacy row still lacks its provider config.
    legacy = conn.execute(
        """
        SELECT provider, base_url, model, api_key_encrypted, api_key_source, updated_at
        FROM ai_settings
        WHERE id = 'local-default'
          AND provider != 'mock'
          AND api_key_source = 'user'
          AND api_key_encrypted != ''
          AND NOT EXISTS (
            SELECT 1
            FROM ai_provider_configs
            WHERE ai_provider_configs.provider = ai_settings.provider
          )
        """
    ).fetchone()
    if legacy is None:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO ai_provider_configs(
          provider, base_url, model, api_key_encrypted, api_key_source, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(legacy),
    )
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS plans (
          id TEXT PRIMARY KEY,
          date TEXT NOT NULL,
          time TEXT NOT NULL DEFAULT '09:00',
          content TEXT NOT NULL,
          done INTEGER NOT NULL DEFAULT 0,
          result TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'medium',
          estimated_minutes INTEGER NOT NULL DEFAULT 30,
          source TEXT NOT NULL DEFAULT 'manual',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_plans_date_time
          ON plans(date, time);

        CREATE TABLE IF NOT EXISTS month_notes (
          year INTEGER NOT NULL,
          month INTEGER NOT NULL,
          content TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(year, month)
        );

        CREATE TABLE IF NOT EXISTS daily_reviews (
          id TEXT PRIMARY KEY,
          date TEXT NOT NULL UNIQUE,
          summary TEXT NOT NULL,
          suggestions TEXT NOT NULL DEFAULT '[]',
          done_count INTEGER NOT NULL DEFAULT 0,
          total_count INTEGER NOT NULL DEFAULT 0,
          suggestions_json TEXT NOT NULL DEFAULT '[]',
          replan_tasks_json TEXT NOT NULL DEFAULT '[]',
          target_date TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS planning_goals (
          id TEXT PRIMARY KEY,
          goal TEXT NOT NULL,
          deadline TEXT NOT NULL DEFAULT '',
          daily_hours REAL NOT NULL DEFAULT 2,
          materials TEXT NOT NULL DEFAULT '',
          preferences TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          phases_json TEXT NOT NULL DEFAULT '[]',
          tasks_json TEXT NOT NULL DEFAULT '[]',
          structured_plan_json TEXT NOT NULL DEFAULT '{}',
          sources_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS planning_sessions (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL DEFAULT '',
          entry_point TEXT NOT NULL DEFAULT 'p_mode',
          status TEXT NOT NULL,
          business_status TEXT NOT NULL DEFAULT 'goal_clarification',
          runtime_status TEXT NOT NULL DEFAULT 'idle',
          user_input TEXT NOT NULL,
          user_need_contract_json TEXT NOT NULL DEFAULT '{}',
          slot_state_json TEXT NOT NULL DEFAULT '{}',
          pending_question_json TEXT NOT NULL DEFAULT '{}',
          memory_insight_json TEXT NOT NULL DEFAULT '{}',
          resource_brief_json TEXT NOT NULL DEFAULT '{}',
          design_proposal_json TEXT NOT NULL DEFAULT '{}',
          execution_draft_json TEXT NOT NULL DEFAULT '{}',
          latest_learning_patch_json TEXT NOT NULL DEFAULT '{}',
          cognitive_metadata_json TEXT NOT NULL DEFAULT '{}',
          goal_model_json TEXT NOT NULL DEFAULT '{}',
          goal_completion_json TEXT NOT NULL DEFAULT '{}',
          reality_assessment_json TEXT NOT NULL DEFAULT '{}',
          evidence_pack_json TEXT NOT NULL DEFAULT '{}',
          strategy_portfolio_json TEXT NOT NULL DEFAULT '{}',
          execution_blueprint_json TEXT NOT NULL DEFAULT '{}',
          critique_report_json TEXT NOT NULL DEFAULT '{}',
          planning_learning_update_json TEXT NOT NULL DEFAULT '{}',
          conversation_history_json TEXT NOT NULL DEFAULT '[]',
          request_context_json TEXT NOT NULL DEFAULT '{}',
          approved_strategy_id TEXT NOT NULL DEFAULT '',
          repair_count INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_planning_sessions_thread_status
          ON planning_sessions(thread_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS planning_artifacts (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          owner_agent TEXT NOT NULL,
          artifact_type TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'draft',
          content_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES planning_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_planning_artifacts_session_type
          ON planning_artifacts(session_id, artifact_type, version);

        CREATE TABLE IF NOT EXISTS agent_decisions (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          agent TEXT NOT NULL,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          confidence REAL NOT NULL DEFAULT 1,
          input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
          output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
          user_visible_summary TEXT NOT NULL DEFAULT '',
          model_usage_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES planning_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_agent_decisions_session_time
          ON agent_decisions(session_id, created_at);

        CREATE TABLE IF NOT EXISTS agent_messages (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          from_agent TEXT NOT NULL,
          to_agent TEXT NOT NULL,
          message_type TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          resolved INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES planning_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_agent_messages_session_time
          ON agent_messages(session_id, created_at);

        CREATE TABLE IF NOT EXISTS harness_states (
          session_id TEXT PRIMARY KEY,
          lifecycle TEXT NOT NULL DEFAULT 'active',
          current_stage TEXT NOT NULL DEFAULT 'session_guard',
          completed_agents_json TEXT NOT NULL DEFAULT '[]',
          pending_agent TEXT NOT NULL DEFAULT '',
          artifact_versions_json TEXT NOT NULL DEFAULT '{}',
          waiting_state TEXT NOT NULL DEFAULT 'none',
          errors_json TEXT NOT NULL DEFAULT '[]',
          recovery_actions_json TEXT NOT NULL DEFAULT '[]',
          approvals_json TEXT NOT NULL DEFAULT '[]',
          repair_target TEXT NOT NULL DEFAULT '',
          checkpoint_version INTEGER NOT NULL DEFAULT 1,
          checkpoint_json TEXT NOT NULL DEFAULT '{}',
          last_decision_json TEXT NOT NULL DEFAULT '{}',
          last_policy_decision_json TEXT NOT NULL DEFAULT '{}',
          last_event_sequence INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES planning_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS harness_events (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          checkpoint_version INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          agent_id TEXT NOT NULL DEFAULT '',
          decision TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES planning_sessions(id) ON DELETE CASCADE,
          UNIQUE(session_id, sequence)
        );

        CREATE INDEX IF NOT EXISTS idx_harness_events_session_sequence
          ON harness_events(session_id, sequence);

        CREATE INDEX IF NOT EXISTS idx_harness_events_session_checkpoint
          ON harness_events(session_id, checkpoint_version);

        CREATE TABLE IF NOT EXISTS ai_settings (
          id TEXT PRIMARY KEY,
          provider TEXT NOT NULL DEFAULT 'deepseek',
          base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com',
          model TEXT NOT NULL DEFAULT 'deepseek-v4-flash',
          api_key_encrypted TEXT NOT NULL DEFAULT '',
          api_key_source TEXT NOT NULL DEFAULT '',
          temperature REAL NOT NULL DEFAULT 0.3,
          timeout_seconds INTEGER NOT NULL DEFAULT 40,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ai_provider_configs (
          provider TEXT PRIMARY KEY,
          base_url TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          api_key_encrypted TEXT NOT NULL DEFAULT '',
          api_key_source TEXT NOT NULL DEFAULT '',
          key_status TEXT NOT NULL DEFAULT 'unchecked',
          key_error_type TEXT NOT NULL DEFAULT '',
          last_validated_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ai_model_routing_rules (
          task_type TEXT PRIMARY KEY,
          primary_provider TEXT NOT NULL,
          fallback_providers_json TEXT NOT NULL DEFAULT '[]',
          local_fallback_enabled INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          tags_json TEXT NOT NULL DEFAULT '[]',
          source TEXT NOT NULL DEFAULT 'user',
          source_id TEXT NOT NULL DEFAULT '',
          source_key TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memories_kind
          ON memories(kind);

        CREATE INDEX IF NOT EXISTS idx_memories_source_key
          ON memories(source_key);

        CREATE TABLE IF NOT EXISTS user_planning_hypotheses (
          id TEXT PRIMARY KEY,
          statement TEXT NOT NULL,
          statement_key TEXT NOT NULL UNIQUE,
          domain_scope_json TEXT NOT NULL DEFAULT '[]',
          evidence_count INTEGER NOT NULL DEFAULT 1,
          positive_evidence_json TEXT NOT NULL DEFAULT '[]',
          negative_evidence_json TEXT NOT NULL DEFAULT '[]',
          confidence REAL NOT NULL DEFAULT 0.5,
          status TEXT NOT NULL DEFAULT 'tentative',
          first_observed_at TEXT NOT NULL,
          last_validated_at TEXT NOT NULL,
          expires_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_user_planning_hypotheses_status
          ON user_planning_hypotheses(status, last_validated_at);

        CREATE TABLE IF NOT EXISTS user_model_memories (
          id TEXT PRIMARY KEY,
          category TEXT NOT NULL,
          statement TEXT NOT NULL,
          statement_key TEXT NOT NULL UNIQUE,
          domain_scope_json TEXT NOT NULL DEFAULT '[]',
          evidence_json TEXT NOT NULL DEFAULT '[]',
          contradiction_json TEXT NOT NULL DEFAULT '[]',
          observation_count INTEGER NOT NULL DEFAULT 1,
          confidence REAL NOT NULL DEFAULT 0.5,
          status TEXT NOT NULL DEFAULT 'tentative',
          source TEXT NOT NULL DEFAULT 'ai_inference',
          first_observed_at TEXT NOT NULL,
          last_validated_at TEXT NOT NULL,
          expires_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_user_model_memories_category_status
          ON user_model_memories(category, status, last_validated_at);

        CREATE TABLE IF NOT EXISTS planning_shadow_runs (
          id TEXT PRIMARY KEY,
          legacy_session_id TEXT NOT NULL,
          cognitive_session_id TEXT NOT NULL,
          comparison_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
          USING fts5(memory_id, kind, title, content, summary, tags);

        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'manual',
          source_type TEXT NOT NULL DEFAULT 'paste',
          summary TEXT NOT NULL DEFAULT '',
          content_hash TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS document_chunks (
          id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          content TEXT NOT NULL,
          token_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
          ON document_chunks(document_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts
          USING fts5(
            chunk_id UNINDEXED,
            document_id UNINDEXED,
            title,
            content,
            tokenize = 'unicode61'
          );

        CREATE TABLE IF NOT EXISTS ai_runs (
          id TEXT PRIMARY KEY,
          feature TEXT NOT NULL,
          provider TEXT NOT NULL DEFAULT 'mock',
          model TEXT NOT NULL DEFAULT 'local-rule',
          input_summary TEXT NOT NULL DEFAULT '',
          output_summary TEXT NOT NULL DEFAULT '',
          success INTEGER NOT NULL DEFAULT 1,
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
          id TEXT PRIMARY KEY,
          input TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          output_summary TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_events (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          node_id TEXT NOT NULL DEFAULT '',
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_agent_events_run_sequence
          ON agent_events(run_id, sequence);

        CREATE TABLE IF NOT EXISTS command_threads (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS command_messages (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL DEFAULT 'text',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(thread_id) REFERENCES command_threads(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_command_messages_thread_time
          ON command_messages(thread_id, created_at);

        CREATE TABLE IF NOT EXISTS command_drafts (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'calendar_plan',
          version INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'current',
          title TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          source_run_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(thread_id) REFERENCES command_threads(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_command_drafts_thread_status
          ON command_drafts(thread_id, kind, status);

        CREATE TABLE IF NOT EXISTS command_actions (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          draft_id TEXT NOT NULL DEFAULT '',
          target TEXT NOT NULL,
          operation TEXT NOT NULL,
          risk TEXT NOT NULL,
          status TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          error_message TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(thread_id) REFERENCES command_threads(id) ON DELETE CASCADE,
          FOREIGN KEY(draft_id) REFERENCES command_drafts(id) ON DELETE SET DEFAULT
        );

        CREATE INDEX IF NOT EXISTS idx_command_actions_thread_status
          ON command_actions(thread_id, status, created_at);

        CREATE TABLE IF NOT EXISTS command_approvals (
          id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          action_id TEXT NOT NULL,
          permission TEXT NOT NULL,
          decision TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(thread_id) REFERENCES command_threads(id) ON DELETE CASCADE,
          FOREIGN KEY(action_id) REFERENCES command_actions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_command_approvals_action
          ON command_approvals(action_id, created_at);

        """
    )
    ensure_column(conn, "command_messages", "kind", "TEXT NOT NULL DEFAULT 'text'")
    ensure_column(conn, "command_messages", "payload_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "command_drafts", "source_run_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "command_actions", "draft_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "command_actions", "error_message", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "command_approvals", "decision", "TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(conn, "planning_sessions", "slot_state_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "pending_question_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "cognitive_metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "goal_model_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "goal_completion_json", "TEXT NOT NULL DEFAULT '{}'")
    business_status_added = ensure_column(
        conn,
        "planning_sessions",
        "business_status",
        "TEXT NOT NULL DEFAULT 'goal_clarification'",
    )
    runtime_status_added = ensure_column(
        conn,
        "planning_sessions",
        "runtime_status",
        "TEXT NOT NULL DEFAULT 'idle'",
    )
    ensure_column(conn, "planning_sessions", "reality_assessment_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "evidence_pack_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "strategy_portfolio_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "execution_blueprint_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "critique_report_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "planning_learning_update_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "conversation_history_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "planning_sessions", "request_context_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_sessions", "approved_strategy_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "planning_sessions", "repair_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "harness_states", "approvals_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "harness_states", "last_policy_decision_json", "TEXT NOT NULL DEFAULT '{}'")
    if business_status_added:
        conn.execute(
            """
            UPDATE planning_sessions
            SET business_status = CASE
              WHEN status = 'written_to_calendar' THEN 'completed'
              WHEN status = 'cancelled' THEN 'cancelled'
              WHEN status IN ('ready_to_write_calendar', 'waiting_calendar_write_approval') THEN 'calendar_pending'
              WHEN status IN ('waiting_execution_approval', 'execution_revision', 'learning_from_feedback') THEN 'execution_pending'
              WHEN status IN ('waiting_design_approval', 'design_revision') THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%goal_intelligence%'
                OR cognitive_metadata_json LIKE '%goal_modeling%'
                OR cognitive_metadata_json LIKE '%goal_completion%'
              ) THEN 'goal_clarification'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%reality_assessment%'
                OR cognitive_metadata_json LIKE '%context_evidence%'
                OR cognitive_metadata_json LIKE '%evidence_synthesis%'
              ) THEN 'evidence_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%strategy_architecture%'
                OR cognitive_metadata_json LIKE '%strategy_design%'
              ) THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND TRIM(execution_blueprint_json) NOT IN ('', '{}', 'null') THEN 'execution_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND approved_strategy_id != '' THEN 'execution_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                TRIM(strategy_portfolio_json) NOT IN ('', '{}', 'null')
                OR TRIM(evidence_pack_json) NOT IN ('', '{}', 'null')
              ) THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND TRIM(goal_model_json) NOT IN ('', '{}', 'null') THEN 'evidence_pending'
              ELSE 'goal_clarification'
            END
            """
        )
    else:
        conn.execute(
            """
            UPDATE planning_sessions
            SET business_status = CASE
              WHEN status = 'written_to_calendar' THEN 'completed'
              WHEN status = 'cancelled' THEN 'cancelled'
              WHEN status IN ('ready_to_write_calendar', 'waiting_calendar_write_approval') THEN 'calendar_pending'
              WHEN status IN ('waiting_execution_approval', 'execution_revision', 'learning_from_feedback') THEN 'execution_pending'
              WHEN status IN ('waiting_design_approval', 'design_revision') THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%goal_intelligence%'
                OR cognitive_metadata_json LIKE '%goal_modeling%'
                OR cognitive_metadata_json LIKE '%goal_completion%'
              ) THEN 'goal_clarification'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%reality_assessment%'
                OR cognitive_metadata_json LIKE '%context_evidence%'
                OR cognitive_metadata_json LIKE '%evidence_synthesis%'
              ) THEN 'evidence_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                cognitive_metadata_json LIKE '%strategy_architecture%'
                OR cognitive_metadata_json LIKE '%strategy_design%'
              ) THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND TRIM(execution_blueprint_json) NOT IN ('', '{}', 'null') THEN 'execution_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND approved_strategy_id != '' THEN 'execution_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND (
                TRIM(strategy_portfolio_json) NOT IN ('', '{}', 'null')
                OR TRIM(evidence_pack_json) NOT IN ('', '{}', 'null')
              ) THEN 'strategy_pending'
              WHEN status = 'MODEL_UNAVAILABLE' AND TRIM(goal_model_json) NOT IN ('', '{}', 'null') THEN 'evidence_pending'
              ELSE 'goal_clarification'
            END
            WHERE business_status IS NULL
               OR TRIM(business_status) = ''
               OR (
                 business_status = 'goal_clarification'
                 AND (
                   status IN (
                     'written_to_calendar', 'cancelled', 'ready_to_write_calendar',
                     'waiting_calendar_write_approval', 'waiting_execution_approval',
                     'execution_revision', 'learning_from_feedback',
                     'waiting_design_approval', 'design_revision'
                   )
                   OR (
                     status = 'MODEL_UNAVAILABLE'
                     AND cognitive_metadata_json NOT LIKE '%goal_intelligence%'
                     AND cognitive_metadata_json NOT LIKE '%goal_modeling%'
                     AND cognitive_metadata_json NOT LIKE '%goal_completion%'
                     AND (
                       TRIM(execution_blueprint_json) NOT IN ('', '{}', 'null')
                       OR approved_strategy_id != ''
                       OR TRIM(strategy_portfolio_json) NOT IN ('', '{}', 'null')
                       OR TRIM(evidence_pack_json) NOT IN ('', '{}', 'null')
                       OR TRIM(goal_model_json) NOT IN ('', '{}', 'null')
                     )
                   )
                 )
               )
            """
        )
    if runtime_status_added:
        conn.execute(
            """
            UPDATE planning_sessions
            SET runtime_status = CASE
              WHEN status = 'MODEL_UNAVAILABLE' THEN 'blocked_model'
              ELSE 'idle'
            END
            """
        )
    else:
        conn.execute(
            """
            UPDATE planning_sessions
            SET runtime_status = CASE
              WHEN status = 'MODEL_UNAVAILABLE' THEN 'blocked_model'
              ELSE 'idle'
            END
            WHERE runtime_status IS NULL
               OR TRIM(runtime_status) = ''
               OR (runtime_status = 'idle' AND status = 'MODEL_UNAVAILABLE')
            """
        )
    action_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_actions)").fetchall()}
    if {"error", "error_message"} <= action_columns:
        conn.execute(
            """
            UPDATE command_actions
            SET error_message = error
            WHERE error_message = '' AND error != ''
            """
        )
    ensure_column(conn, "ai_settings", "temperature", "REAL NOT NULL DEFAULT 0.3")
    ensure_column(conn, "ai_settings", "timeout_seconds", "INTEGER NOT NULL DEFAULT 40")
    ensure_column(conn, "ai_settings", "api_key_source", "TEXT NOT NULL DEFAULT 'legacy'")
    ensure_column(conn, "ai_provider_configs", "key_status", "TEXT NOT NULL DEFAULT 'unchecked'")
    ensure_column(conn, "ai_provider_configs", "key_error_type", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "ai_provider_configs", "last_validated_at", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        INSERT OR IGNORE INTO ai_provider_configs(
          provider, base_url, model, api_key_encrypted, api_key_source, updated_at
        )
        SELECT provider, base_url, model, api_key_encrypted, api_key_source, updated_at
        FROM ai_settings
        WHERE id = 'local-default'
          AND provider != 'mock'
          AND api_key_source = 'user'
          AND api_key_encrypted != ''
        """
    )
    ensure_column(conn, "daily_reviews", "done_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "daily_reviews", "total_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "daily_reviews", "suggestions_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "daily_reviews", "replan_tasks_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "daily_reviews", "target_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "documents", "source_type", "TEXT NOT NULL DEFAULT 'paste'")
    ensure_column(conn, "documents", "summary", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "planning_goals", "structured_plan_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "planning_goals", "sources_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "plans", "refined_task_json", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "plans", "refined_task_updated_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "plans", "source_key", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        INSERT INTO document_chunks_fts(chunk_id, document_id, title, content)
        SELECT c.id, c.document_id, d.title, c.content
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE NOT EXISTS (
          SELECT 1 FROM document_chunks_fts f WHERE f.chunk_id = c.id
        )
        """
    )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        return True
    return False


def row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    return dict(row) if row else None


def save_event(kind: str, payload: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_runs(id, feature, input_summary, output_summary)
            VALUES (?, ?, ?, ?)
            """,
            (str(uuid4()), kind, payload[:4000], payload[:4000]),
        )


def save_memory(user_id: str, preferences: str) -> None:
    key = f"preferences:{user_id}"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_preferences(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, preferences),
        )


def load_memory(user_id: str = "local-user") -> str:
    key = f"preferences:{user_id}"
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def save_chunk(title: str, chunk: str) -> None:
    with get_conn() as conn:
        doc = conn.execute("SELECT id FROM documents WHERE title = ? ORDER BY created_at DESC LIMIT 1", (title,)).fetchone()
        document_id = doc["id"] if doc else str(uuid4())
        if not doc:
            conn.execute(
                "INSERT INTO documents(id, title, source) VALUES (?, ?, ?)",
                (document_id, title, "manual"),
            )
        chunk_index = conn.execute(
            "SELECT COUNT(*) AS total FROM document_chunks WHERE document_id = ?",
            (document_id,),
        ).fetchone()["total"]
        conn.execute(
            """
            INSERT INTO document_chunks(id, document_id, chunk_index, content, token_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chunk_id := str(uuid4()), document_id, chunk_index, chunk, len(chunk.split())),
        )
        conn.execute(
            """
            INSERT INTO document_chunks_fts(chunk_id, document_id, title, content)
            VALUES (?, ?, ?, ?)
            """,
            (chunk_id, document_id, title, chunk),
        )


def list_chunks(limit: int = 200) -> list[dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT documents.title, document_chunks.content AS chunk
            FROM document_chunks
            JOIN documents ON documents.id = document_chunks.document_id
            ORDER BY document_chunks.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [{"title": row["title"], "chunk": row["chunk"]} for row in rows]
