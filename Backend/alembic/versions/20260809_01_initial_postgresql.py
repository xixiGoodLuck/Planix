"""Create the clean PostgreSQL-only Planix schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260809_01"
down_revision = None
branch_labels = None
depends_on = None

JSON_OBJECT = sa.text("'{}'::jsonb")
JSON_ARRAY = sa.text("'[]'::jsonb")
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.Time(), nullable=False, server_default=sa.text("'09:00'::time")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.Text(), nullable=False, server_default="medium"),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("source_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_plans_date_time", "plans", ["date", "time"])
    op.create_index(
        "ux_plans_source_key",
        "plans",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("source_key <> ''"),
    )

    op.create_table(
        "month_notes",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    op.create_table(
        "planning_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("entry_point", sa.Text(), nullable=False, server_default="p_mode"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("business_status", sa.Text(), nullable=False, server_default="goal_clarification"),
        sa.Column("runtime_status", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("cognitive_metadata_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("conversation_history_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("request_context_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("repair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schedule_repair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index(
        "idx_planning_sessions_thread_status",
        "planning_sessions",
        ["thread_id", "status", "updated_at"],
    )

    op.create_table(
        "planning_artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("planning_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_agent", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("content_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("session_id", "artifact_type", "version", name="uq_planning_artifact_version"),
    )
    op.create_index(
        "idx_planning_artifacts_session_type",
        "planning_artifacts",
        ["session_id", "artifact_type", "version"],
    )

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("planning_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("input_artifact_ids_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("output_artifact_ids_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("user_visible_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_usage_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_agent_decisions_session_time", "agent_decisions", ["session_id", "created_at"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("planning_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_agent", sa.Text(), nullable=False),
        sa.Column("to_agent", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_agent_messages_session_time", "agent_messages", ["session_id", "created_at"])

    op.create_table(
        "harness_states",
        sa.Column("session_id", sa.Text(), sa.ForeignKey("planning_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default="active"),
        sa.Column("current_stage", sa.Text(), nullable=False, server_default="session_guard"),
        sa.Column("completed_agents_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("pending_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("artifact_versions_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("waiting_state", sa.Text(), nullable=False, server_default="none"),
        sa.Column("errors_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("recovery_actions_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("approvals_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("repair_target", sa.Text(), nullable=False, server_default=""),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("checkpoint_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("last_decision_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("last_policy_decision_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    op.create_table(
        "harness_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("planning_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("decision", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("session_id", "sequence", name="uq_harness_event_sequence"),
    )
    op.create_index("idx_harness_events_session_sequence", "harness_events", ["session_id", "sequence"])
    op.create_index("idx_harness_events_session_checkpoint", "harness_events", ["session_id", "checkpoint_version"])

    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False, server_default="deepseek"),
        sa.Column("base_url", sa.Text(), nullable=False, server_default="https://api.deepseek.com"),
        sa.Column("model", sa.Text(), nullable=False, server_default="deepseek-v4-flash"),
        sa.Column("api_key_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "ai_provider_configs",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("base_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("api_key_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_status", sa.Text(), nullable=False, server_default="unchecked"),
        sa.Column("key_error_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "ai_model_routing_rules",
        sa.Column("task_type", sa.Text(), primary_key=True),
        sa.Column("primary_provider", sa.Text(), nullable=False),
        sa.Column("fallback_providers_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("local_fallback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "calendar_state",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.execute("INSERT INTO calendar_state(id, revision) VALUES ('local', 0)")
    op.create_table(
        "user_preferences",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    op.create_table(
        "user_planning_hypotheses",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_key", sa.Text(), nullable=False, unique=True),
        sa.Column("domain_scope_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("positive_evidence_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("negative_evidence_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.Text(), nullable=False, server_default="tentative"),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_user_planning_hypotheses_status",
        "user_planning_hypotheses",
        ["status", "last_validated_at"],
    )
    op.create_table(
        "user_model_memories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_key", sa.Text(), nullable=False, unique=True),
        sa.Column("domain_scope_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("contradiction_json", postgresql.JSONB(), nullable=False, server_default=JSON_ARRAY),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.Text(), nullable=False, server_default="tentative"),
        sa.Column("source", sa.Text(), nullable=False, server_default="ai_inference"),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_user_model_memories_category_status",
        "user_model_memories",
        ["category", "status", "last_validated_at"],
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="mock"),
        sa.Column("model", sa.Text(), nullable=False, server_default="local-rule"),
        sa.Column("input_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    op.create_table(
        "command_threads",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "command_messages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), sa.ForeignKey("command_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.Text(), nullable=False, server_default="text"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_command_messages_thread_time", "command_messages", ["thread_id", "created_at"])
    op.create_table(
        "command_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), sa.ForeignKey("command_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="calendar_plan"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="current"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("source_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_command_drafts_thread_status", "command_drafts", ["thread_id", "kind", "status"])
    op.create_table(
        "command_actions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), sa.ForeignKey("command_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Text(), sa.ForeignKey("command_drafts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("risk", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("result_json", postgresql.JSONB(), nullable=False, server_default=JSON_OBJECT),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_command_actions_thread_status", "command_actions", ["thread_id", "status", "created_at"])
    op.create_table(
        "command_approvals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("thread_id", sa.Text(), sa.ForeignKey("command_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_id", sa.Text(), sa.ForeignKey("command_actions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_command_approvals_action", "command_approvals", ["action_id", "created_at"])


def downgrade() -> None:
    for table in (
        "command_approvals",
        "command_actions",
        "command_drafts",
        "command_messages",
        "command_threads",
        "ai_runs",
        "user_model_memories",
        "user_planning_hypotheses",
        "user_preferences",
        "calendar_state",
        "ai_model_routing_rules",
        "ai_provider_configs",
        "ai_settings",
        "harness_events",
        "harness_states",
        "agent_messages",
        "agent_decisions",
        "planning_artifacts",
        "planning_sessions",
        "month_notes",
        "plans",
    ):
        op.drop_table(table)
