"""Allow recoverable Learning evidence waits.

Revision ID: 20260812_02
Revises: 20260812_01
"""

from alembic import op


revision = "20260812_02"
down_revision = "20260812_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_learning_runs_status", "learning_runs", type_="check")
    op.create_check_constraint(
        "ck_learning_runs_status",
        "learning_runs",
        "status IN ('created', 'running', 'completed', 'failed', 'waiting_evidence')",
    )
    op.drop_constraint(
        "ck_learning_checkpoints_status",
        "learning_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_checkpoints_status",
        "learning_checkpoints",
        "status IN ('created', 'running', 'completed', 'failed', 'waiting_evidence')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE learning_checkpoints SET status = 'failed' "
        "WHERE status = 'waiting_evidence'"
    )
    op.execute(
        "UPDATE learning_runs SET status = 'failed' "
        "WHERE status = 'waiting_evidence'"
    )
    op.drop_constraint(
        "ck_learning_checkpoints_status",
        "learning_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_checkpoints_status",
        "learning_checkpoints",
        "status IN ('created', 'running', 'completed', 'failed')",
    )
    op.drop_constraint("ck_learning_runs_status", "learning_runs", type_="check")
    op.create_check_constraint(
        "ck_learning_runs_status",
        "learning_runs",
        "status IN ('created', 'running', 'completed', 'failed')",
    )
