"""Add isolated PostgreSQL persistence for the Learning runtime.

Revision ID: 20260811_01
Revises: 20260809_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260811_01"
down_revision = "20260809_01"
branch_labels = None
depends_on = None


NOW = sa.text("CURRENT_TIMESTAMP")
JSON_ARRAY = sa.text("'[]'::jsonb")
JSON_OBJECT = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "learning_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("run_fingerprint", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=False),
        sa.Column(
            "completed_stages_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=JSON_ARRAY,
        ),
        sa.Column(
            "current_artifact_ref_json",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column("error_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'completed', 'failed')",
            name="ck_learning_runs_status",
        ),
    )
    op.create_index("idx_learning_runs_fingerprint", "learning_runs", ["run_fingerprint"])
    op.create_index(
        "idx_learning_runs_status_updated",
        "learning_runs",
        ["status", "updated_at"],
    )

    op.create_table(
        "learning_artifacts",
        sa.Column("row_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("learning_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="valid"),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalid_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("version >= 1", name="ck_learning_artifacts_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_learning_artifacts_schema_version"),
        sa.CheckConstraint(
            "status IN ('valid', 'invalid')",
            name="ck_learning_artifacts_status",
        ),
        sa.UniqueConstraint(
            "run_id",
            "artifact_type",
            "artifact_id",
            "version",
            name="uq_learning_artifact_version",
        ),
    )
    op.create_index(
        "idx_learning_artifacts_run_type_version",
        "learning_artifacts",
        ["run_id", "artifact_type", "version"],
    )
    op.create_index(
        "idx_learning_artifacts_run_status",
        "learning_artifacts",
        ["run_id", "status"],
    )

    op.create_table(
        "learning_checkpoints",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("learning_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "artifact_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=JSON_ARRAY,
        ),
        sa.Column("last_successful_stage", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("checkpoint_version >= 1", name="ck_learning_checkpoint_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_learning_checkpoint_schema_version"),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'completed', 'failed')",
            name="ck_learning_checkpoints_status",
        ),
    )

    op.create_table(
        "learning_resume_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("learning_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("previous_stage", sa.Text(), nullable=True),
        sa.Column("resume_stage", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "artifact_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=JSON_ARRAY,
        ),
        sa.Column("checkpoint_before_json", postgresql.JSONB(), nullable=True),
        sa.Column("checkpoint_after_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("run_id", "sequence", name="uq_learning_resume_event_sequence"),
    )
    op.create_index(
        "idx_learning_resume_events_run_sequence",
        "learning_resume_events",
        ["run_id", "sequence"],
    )
    op.create_index(
        "idx_learning_resume_events_run_created",
        "learning_resume_events",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("learning_resume_events")
    op.drop_table("learning_checkpoints")
    op.drop_table("learning_artifacts")
    op.drop_table("learning_runs")
