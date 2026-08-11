"""Add the isolated Learning transcript evidence registry.

Revision ID: 20260811_02
Revises: 20260811_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260811_02"
down_revision = "20260811_01"
branch_labels = None
depends_on = None


NOW = sa.text("CURRENT_TIMESTAMP")
JSON_OBJECT = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "learning_video_resources",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False, server_default=""),
        sa.Column("language", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "technology_versions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=JSON_OBJECT,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_learning_video_resources_duration",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            "content_fingerprint",
            name="pk_learning_video_resources",
        ),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            "content_fingerprint",
            name="uq_learning_video_resource_identity",
        ),
    )
    op.create_index(
        "idx_learning_video_resources_provider_external",
        "learning_video_resources",
        ["provider", "external_id"],
    )

    op.create_table(
        "learning_transcript_sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("resource_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_format", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_checksum", sa.Text(), nullable=False),
        sa.Column("authorization_status", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["resource_id", "resource_fingerprint"],
            [
                "learning_video_resources.id",
                "learning_video_resources.content_fingerprint",
            ],
            name="fk_learning_transcript_source_resource",
        ),
        sa.CheckConstraint(
            "source_type IN ('authorized', 'srt_vtt')",
            name="ck_learning_transcript_sources_type",
        ),
        sa.CheckConstraint(
            "source_format IN ('srt', 'vtt')",
            name="ck_learning_transcript_sources_format",
        ),
        sa.CheckConstraint(
            "authorization_status = 'authorized'",
            name="ck_learning_transcript_sources_authorization",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'stale', 'invalid', 'revoked')",
            name="ck_learning_transcript_sources_status",
        ),
        sa.UniqueConstraint(
            "resource_id",
            "source_checksum",
            name="uq_learning_transcript_source_checksum",
        ),
        sa.UniqueConstraint(
            "resource_id",
            "source_type",
            "source_name",
            name="uq_learning_transcript_source_name",
        ),
    )
    op.create_index(
        "idx_learning_transcript_sources_resource_active",
        "learning_transcript_sources",
        ["resource_id", "resource_fingerprint", "status"],
    )

    op.create_table(
        "learning_transcript_segments",
        sa.Column(
            "source_id",
            sa.Text(),
            sa.ForeignKey("learning_transcript_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_checksum", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "segment_index >= 0",
            name="ck_learning_transcript_segments_index",
        ),
        sa.CheckConstraint(
            "start_ms >= 0",
            name="ck_learning_transcript_segments_start",
        ),
        sa.CheckConstraint(
            "end_ms > start_ms",
            name="ck_learning_transcript_segments_range",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0",
            name="ck_learning_transcript_segments_text",
        ),
        sa.PrimaryKeyConstraint(
            "source_id",
            "segment_index",
            name="pk_learning_transcript_segments",
        ),
    )
    op.create_index(
        "idx_learning_transcript_segments_source_range",
        "learning_transcript_segments",
        ["source_id", "start_ms", "end_ms"],
    )


def downgrade() -> None:
    op.drop_table("learning_transcript_segments")
    op.drop_table("learning_transcript_sources")
    op.drop_table("learning_video_resources")
