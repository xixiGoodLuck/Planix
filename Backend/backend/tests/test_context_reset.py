from app.db import get_conn


def _seed_context() -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO command_threads(id, title) VALUES ('thread-1', 'History')")
        conn.execute(
            "INSERT INTO command_messages(id, thread_id, role, content) VALUES ('message-1', 'thread-1', 'user', 'hello')"
        )
        conn.execute(
            "INSERT INTO command_drafts(id, thread_id, title) VALUES ('draft-1', 'thread-1', 'Draft')"
        )
        conn.execute(
            """
            INSERT INTO command_actions(id, thread_id, draft_id, target, operation, risk, status)
            VALUES ('action-1', 'thread-1', 'draft-1', 'calendar', 'create', 'medium', 'pending')
            """
        )
        conn.execute(
            """
            INSERT INTO command_approvals(id, thread_id, action_id, permission, decision)
            VALUES ('approval-1', 'thread-1', 'action-1', 'medium', 'approve')
            """
        )
        conn.execute(
            """
            INSERT INTO planning_sessions(id, thread_id, status, user_input)
            VALUES ('session-1', 'thread-1', 'planning', 'make a plan')
            """
        )
        conn.execute(
            """
            INSERT INTO planning_artifacts(id, session_id, owner_agent, artifact_type)
            VALUES ('artifact-1', 'session-1', 'Understanding Agent', 'understanding_snapshot')
            """
        )
        conn.execute("INSERT INTO harness_states(session_id) VALUES ('session-1')")
        conn.execute(
            """
            INSERT INTO harness_events(id, session_id, sequence, checkpoint_version, event_type)
            VALUES ('event-1', 'session-1', 1, 1, 'started')
            """
        )


def _seed_memories() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_model_memories(
              id, category, statement, statement_key, first_observed_at, last_validated_at
            ) VALUES ('memory-1', 'preference', 'Prefers mornings', 'prefers-mornings', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO user_planning_hypotheses(
              id, statement, statement_key, first_observed_at, last_validated_at
            ) VALUES ('hypothesis-1', 'May prefer short tasks', 'short-tasks', 'now', 'now')
            """
        )


def _counts(*tables: str) -> dict[str, int]:
    with get_conn() as conn:
        return {table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]) for table in tables}


def test_context_reset_deletes_all_runtime_context(client):
    _seed_context()

    stats = client.get("/api/settings/context")
    response = client.request("DELETE", "/api/settings/context", json={"clearMemory": False})

    assert stats.status_code == 200
    assert stats.json() == {"conversations": 1, "planningSessions": 1, "artifacts": 1, "memories": 0}
    assert response.status_code == 200
    assert response.json() == {
        "deletedThreads": 1,
        "deletedSessions": 1,
        "deletedArtifacts": 1,
        "deletedEvents": 1,
        "deletedMemories": 0,
    }
    tables = (
        "command_threads",
        "command_messages",
        "command_drafts",
        "command_actions",
        "command_approvals",
        "planning_sessions",
        "planning_artifacts",
        "harness_states",
        "harness_events",
    )
    assert _counts(*tables) == {table: 0 for table in tables}
    assert client.get("/api/settings/context").json() == {
        "conversations": 0,
        "planningSessions": 0,
        "artifacts": 0,
        "memories": 0,
    }
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM planning_artifacts a LEFT JOIN planning_sessions s ON s.id = a.session_id WHERE s.id IS NULL").fetchall() == []


def test_context_reset_preserves_ai_configuration(client):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_settings(id, provider, api_key_source)
            VALUES ('local-default', 'deepseek', 'secret_store')
            """
        )
        conn.execute(
            """
            INSERT INTO ai_provider_configs(provider, model, api_key_source)
            VALUES ('deepseek', 'deepseek-chat', 'secret_store')
            """
        )

    assert client.request("DELETE", "/api/settings/context", json={"clearMemory": False}).status_code == 200
    assert _counts("ai_settings", "ai_provider_configs") == {"ai_settings": 1, "ai_provider_configs": 1}
    with get_conn() as conn:
        assert conn.execute("SELECT api_key_source FROM ai_settings").fetchone()["api_key_source"] == "secret_store"
        assert conn.execute("SELECT api_key_source FROM ai_provider_configs").fetchone()["api_key_source"] == "secret_store"


def test_context_reset_preserves_saved_plans(client):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO plans(id, date, content, source)
            VALUES ('plan-1', '2026-08-09', 'Existing calendar plan', 'ai')
            """
        )

    assert client.request("DELETE", "/api/settings/context", json={"clearMemory": False}).status_code == 200
    assert _counts("plans") == {"plans": 1}


def test_context_reset_keeps_long_term_memory_by_default(client):
    _seed_memories()

    response = client.request("DELETE", "/api/settings/context", json={"clearMemory": False})

    assert response.status_code == 200
    assert response.json()["deletedMemories"] == 0
    assert _counts("user_model_memories", "user_planning_hypotheses") == {
        "user_model_memories": 1,
        "user_planning_hypotheses": 1,
    }


def test_context_reset_clears_long_term_memory_when_requested(client):
    _seed_memories()

    response = client.request("DELETE", "/api/settings/context", json={"clearMemory": True})

    assert response.status_code == 200
    assert response.json()["deletedMemories"] == 2
    assert _counts("user_model_memories", "user_planning_hypotheses") == {
        "user_model_memories": 0,
        "user_planning_hypotheses": 0,
    }


def test_context_reset_rolls_back_every_delete_on_failure(client):
    _seed_context()
    with get_conn() as conn:
        conn.execute(
            """
            CREATE FUNCTION fail_context_reset() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'forced reset failure';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        conn.execute("CREATE TRIGGER fail_context_reset BEFORE DELETE ON planning_sessions FOR EACH STATEMENT EXECUTE FUNCTION fail_context_reset()")

    response = client.request("DELETE", "/api/settings/context", json={"clearMemory": False})

    assert response.status_code == 500
    assert _counts("command_threads", "command_messages", "planning_sessions", "planning_artifacts") == {
        "command_threads": 1,
        "command_messages": 1,
        "planning_sessions": 1,
        "planning_artifacts": 1,
    }
    with get_conn() as conn:
        conn.execute("DROP TRIGGER fail_context_reset ON planning_sessions")
        conn.execute("DROP FUNCTION fail_context_reset()")
