from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db import get_conn, init_db
from app.harness.contracts import (
    ApprovalRecord,
    ArtifactRef,
    HarnessDecision,
    PolicyDecision,
    RecoveryAction,
)
from app.harness.controllers import HumanApprovalController
from app.harness.observability import HarnessObservability
from app.harness.persistence import (
    HarnessCheckpointConflict,
    HarnessStateRepository,
)
from app.harness.state import HarnessError, PersistentCognitiveState
from app.services.cognitive_planning.compatibility import SessionApiAdapter


@pytest.fixture()
def harness_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'harness.db'}")
    session_id = str(uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO planning_sessions(id, thread_id, status, user_input)
            VALUES (?, 'harness-thread', 'needs_goal_clarification', 'saved goal')
            """,
            (session_id,),
        )
    return session_id


def test_harness_checkpoint_and_event_restore_from_new_repository(harness_session: str) -> None:
    repository = HarnessStateRepository()
    initial = repository.create_or_load(
        harness_session,
        PersistentCognitiveState(
            sessionId=harness_session,
            currentStage="strategy",
            completedAgents=["goal_intelligence", "goal_completion", "reality", "evidence"],
            pendingAgent="strategy",
            artifactVersions={
                "user_goal_model": 1,
                "goal_completion": 1,
                "reality_assessment": 1,
                "evidence_pack": 1,
            },
            errors=[
                HarnessError(
                    stage="strategy",
                    errorType="auth_error",
                    message="No configured model is available.",
                    retryable=False,
                )
            ],
            waitingState="model_recovery",
            checkpoint={"resumeNode": "strategy"},
        ),
    )
    decision = HarnessDecision(
        directive="block_runtime",
        nextAgent="strategy",
        graphNode="strategy",
        reason="Strategy is waiting for a working model.",
        waitState="model_recovery",
    )

    committed = repository.checkpoint(
        initial.model_copy(update={"last_decision": decision}),
        event_type="harness_decision",
        agent_id="strategy",
        decision="block_runtime",
        payload={"resumeNode": "strategy"},
    )

    assert committed.state.checkpoint_version == 2
    assert committed.event.sequence == 1
    assert committed.event.event_type == "harness_decision"

    recovered = HarnessStateRepository().recover(harness_session)
    assert recovered == committed.state
    assert recovered.current_stage == "strategy"
    assert recovered.pending_agent == "strategy"
    assert recovered.completed_agents == ["goal_intelligence", "goal_completion", "reality", "evidence"]
    assert recovered.artifact_versions["evidence_pack"] == 1
    assert recovered.waiting_state == "model_recovery"
    assert recovered.errors[0].error_type == "auth_error"
    assert HarnessStateRepository().events(harness_session) == [committed.event]


def test_harness_checkpoint_is_compare_and_swap_and_rolls_back_invalid_event(harness_session: str) -> None:
    first_repository = HarnessStateRepository()
    stale = first_repository.create_or_load(harness_session)

    with pytest.raises(ValidationError):
        first_repository.checkpoint(
            stale,
            event_type="not-a-harness-event",  # type: ignore[arg-type]
            decision="invalid",
        )
    assert first_repository.recover(harness_session).checkpoint_version == 1
    assert first_repository.events(harness_session) == []

    first = first_repository.checkpoint(
        stale.model_copy(update={"current_stage": "goal_intelligence"}),
        event_type="harness_decision",
        decision="invoke_agent",
    )
    with pytest.raises(HarnessCheckpointConflict) as conflict:
        HarnessStateRepository().checkpoint(
            stale.model_copy(update={"current_stage": "evidence"}),
            event_type="harness_decision",
            decision="invoke_agent",
        )

    assert conflict.value.expected_version == 1
    assert conflict.value.actual_version == 2
    recovered = first_repository.recover(harness_session)
    assert recovered == first.state
    assert [event.sequence for event in first_repository.events(harness_session)] == [1]


def test_version_bound_approvals_and_policy_decision_survive_create_update_and_recovery(
    harness_session: str,
) -> None:
    strategy = ArtifactRef(
        id="strategy-v3",
        sessionId=harness_session,
        kind="strategy_portfolio",
        version=3,
        owner="strategy",
        status="approved",
    )
    execution = ArtifactRef(
        id="execution-v2",
        sessionId=harness_session,
        kind="execution_blueprint",
        version=2,
        owner="execution",
        status="approved",
    )
    strategy_approval = ApprovalRecord(
        id="approval-strategy-v3",
        sessionId=harness_session,
        gate="strategy",
        artifact=strategy,
        status="approved",
        createdAt="2026-07-11T10:00:00Z",
        decidedAt="2026-07-11T10:01:00Z",
    )
    waiting_execution = PolicyDecision(
        subject="planning_progress",
        action="wait_approval",
        allowed=False,
        reason="Execution approval is required.",
        sessionId=harness_session,
        requiredApproval="execution",
        requiredGates=("execution_approval",),
        failedGates=("execution_approval",),
    )
    repository = HarnessStateRepository()
    created = repository.create_or_load(
        harness_session,
        PersistentCognitiveState(
            sessionId=harness_session,
            currentStage="execution",
            artifactVersions={"strategy_portfolio": 3, "execution_blueprint": 2},
            approvals=[strategy_approval],
            lastPolicyDecision=waiting_execution,
        ),
    )

    restored_after_create = HarnessStateRepository().recover(harness_session)
    assert restored_after_create == created
    approval_controller = HumanApprovalController(restored_after_create.approvals)
    assert approval_controller.is_approved(
        session_id=harness_session,
        gate="strategy",
        artifact=strategy,
    )
    assert not approval_controller.is_approved(
        session_id=harness_session,
        gate="strategy",
        artifact=strategy.model_copy(update={"id": "strategy-v4", "version": 4}),
    )
    assert restored_after_create.last_policy_decision == waiting_execution

    execution_approval = ApprovalRecord(
        id="approval-execution-v2",
        sessionId=harness_session,
        gate="execution",
        artifact=execution,
        status="approved",
        createdAt="2026-07-11T10:02:00Z",
        decidedAt="2026-07-11T10:03:00Z",
    )
    allow_critic = PolicyDecision(
        subject="planning_progress",
        action="invoke_agent",
        allowed=True,
        reason="Current execution version is approved; invoke Critic.",
        sessionId=harness_session,
        nextAgent="critic",
    )
    updated = repository.checkpoint(
        restored_after_create.model_copy(
            update={
                "approvals": [*restored_after_create.approvals, execution_approval],
                "last_policy_decision": allow_critic,
            }
        ),
        event_type="policy_decision",
        agent_id="critic",
        decision="invoke_agent",
        payload=allow_critic.model_dump(by_alias=True),
    )

    restored_after_update = HarnessStateRepository().recover(harness_session)
    assert restored_after_update == updated.state
    assert [record.id for record in restored_after_update.approvals] == [
        "approval-strategy-v3",
        "approval-execution-v2",
    ]
    assert restored_after_update.approvals[1].artifact.same_version(execution)
    assert restored_after_update.last_policy_decision == allow_critic


def test_observability_updates_versions_progress_and_redacts_secrets(harness_session: str) -> None:
    repository = HarnessStateRepository()
    observability = HarnessObservability(repository)
    state = repository.create_or_load(harness_session)

    running = observability.agent_invocation(
        state,
        agent_id="strategy",
        status="running",
        invocation_id="invocation-1",
        stage="strategy",
        input_artifacts={"user_goal_model": 1, "evidence_pack": 1},
    )
    changed = observability.artifact_changed(
        running.state,
        agent_id="strategy",
        artifact_type="strategy_portfolio",
        artifact_id="artifact-1",
        version=1,
    )
    completed = observability.agent_invocation(
        changed.state,
        agent_id="strategy",
        status="succeeded",
        invocation_id="invocation-1",
        stage="strategy",
        output_artifact={"artifactId": "artifact-1", "version": 1},
    )
    recovered = observability.recovery_action(
        completed.state,
        RecoveryAction(
            action="checkpoint_resume",
            stage="strategy",
            reason="A new runtime loaded the committed checkpoint.",
            retryable=True,
        ),
        agent_id="strategy",
    )
    redacted = observability.record(
        recovered.state,
        event_type="model_routing",
        agent_id="strategy",
        decision="selected",
        payload={
            "provider": "deepseek",
            "model": "safe-model-name",
            "apiKey": "should-not-be-stored",
            "nested": {"Authorization": "Bearer should-not-be-stored"},
        },
    )

    restored = HarnessStateRepository().recover(harness_session)
    assert restored == redacted.state
    assert restored.completed_agents == ["strategy"]
    assert restored.pending_agent is None
    assert restored.artifact_versions == {"strategy_portfolio": 1}
    assert restored.recovery_actions[-1].action == "checkpoint_resume"

    events = repository.events(harness_session)
    assert [event.event_type for event in events] == [
        "agent_invocation",
        "artifact_changed",
        "agent_invocation",
        "recovery_action",
        "model_routing",
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert events[-1].payload["apiKey"] == "[REDACTED]"
    assert events[-1].payload["nested"]["Authorization"] == "[REDACTED]"


def test_harness_schema_is_additive_and_does_not_change_session_api_shape(harness_session: str) -> None:
    adapter = SessionApiAdapter()
    with get_conn() as conn:
        before_row = conn.execute(
            "SELECT * FROM planning_sessions WHERE id = ?",
            (harness_session,),
        ).fetchone()
        state_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(harness_states)").fetchall()
        }
        event_indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(harness_events)").fetchall()
        }
    before = adapter.from_row(before_row).model_dump(by_alias=True)

    assert {
        "session_id",
        "current_stage",
        "completed_agents_json",
        "pending_agent",
        "artifact_versions_json",
        "waiting_state",
        "errors_json",
        "approvals_json",
        "checkpoint_version",
        "last_policy_decision_json",
        "last_event_sequence",
    } <= state_columns
    assert "idx_harness_events_session_sequence" in event_indexes

    repository = HarnessStateRepository()
    state = repository.create_or_load(harness_session)
    repository.checkpoint(
        state,
        event_type="policy_decision",
        decision="wait_user",
        payload={"policy": "continue_planning", "allowed": False},
    )

    with get_conn() as conn:
        after_row = conn.execute(
            "SELECT * FROM planning_sessions WHERE id = ?",
            (harness_session,),
        ).fetchone()
        stored = conn.execute(
            "SELECT checkpoint_version, payload_json FROM harness_events WHERE session_id = ?",
            (harness_session,),
        ).fetchone()
    after = adapter.from_row(after_row).model_dump(by_alias=True)

    assert after == before
    assert int(stored["checkpoint_version"]) == 2
    assert json.loads(stored["payload_json"])["policy"] == "continue_planning"


def test_harness_approval_columns_migrate_additively_from_previous_schema() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE harness_states (
          session_id TEXT PRIMARY KEY,
          lifecycle TEXT NOT NULL DEFAULT 'active',
          current_stage TEXT NOT NULL DEFAULT 'session_guard',
          completed_agents_json TEXT NOT NULL DEFAULT '[]',
          pending_agent TEXT NOT NULL DEFAULT '',
          artifact_versions_json TEXT NOT NULL DEFAULT '{}',
          waiting_state TEXT NOT NULL DEFAULT 'none',
          errors_json TEXT NOT NULL DEFAULT '[]',
          recovery_actions_json TEXT NOT NULL DEFAULT '[]',
          repair_target TEXT NOT NULL DEFAULT '',
          checkpoint_version INTEGER NOT NULL DEFAULT 1,
          checkpoint_json TEXT NOT NULL DEFAULT '{}',
          last_decision_json TEXT NOT NULL DEFAULT '{}',
          last_event_sequence INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO harness_states(
          session_id, current_stage, checkpoint_version, artifact_versions_json
        ) VALUES ('legacy-session', 'execution', 7, '{"execution_blueprint":2}');
        """
    )

    init_db(conn)
    init_db(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(harness_states)").fetchall()}
    migrated = conn.execute(
        "SELECT * FROM harness_states WHERE session_id = 'legacy-session'"
    ).fetchone()
    assert {"approvals_json", "last_policy_decision_json"} <= columns
    assert migrated["current_stage"] == "execution"
    assert int(migrated["checkpoint_version"]) == 7
    assert json.loads(migrated["artifact_versions_json"])["execution_blueprint"] == 2
    assert json.loads(migrated["approvals_json"]) == []
    assert json.loads(migrated["last_policy_decision_json"]) == {}
    conn.close()
