"""Remove retired generic planning, command, and calendar storage.

Revision ID: 20260812_01
Revises: 20260811_02
"""

from alembic import op


revision = "20260812_01"
down_revision = "20260811_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the former semantic-learning route configuration under the only
    # production Learning model task before removing retired routing rows.
    op.execute(
        """
        INSERT INTO ai_model_routing_rules(
            task_type, primary_provider, fallback_providers_json,
            local_fallback_enabled, updated_at
        )
        SELECT
            'learning_semantic', primary_provider, fallback_providers_json,
            FALSE, updated_at
        FROM ai_model_routing_rules
        WHERE task_type = 'planning_learning'
        ON CONFLICT (task_type) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_routing_rules(
            task_type, primary_provider, fallback_providers_json,
            local_fallback_enabled, updated_at
        )
        SELECT 'learning_semantic', 'auto', '["deepseek"]'::jsonb, FALSE, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM ai_model_routing_rules WHERE task_type = 'learning_semantic'
        )
        """
    )
    op.execute("DELETE FROM ai_model_routing_rules WHERE task_type <> 'learning_semantic'")

    # The runtime normalizes this preference on first read; clearing only the
    # retired task map preserves provider ordering and unrelated preferences.
    op.execute(
        """
        UPDATE user_preferences
        SET value = jsonb_set(value, '{taskStrategy}',
            jsonb_build_object(
                'learning_semantic',
                COALESCE(value->'taskStrategy'->'planning_learning', '"knowledge_reasoning"'::jsonb)
            )
        ), updated_at = CURRENT_TIMESTAMP
        WHERE key = 'ai.autoModelPolicy' AND jsonb_typeof(value) = 'object'
        """
    )

    # Child tables are removed before their parents so the migration is
    # explicit and does not rely on broad CASCADE operations.
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
        "user_model_memories",
        "user_planning_hypotheses",
        "calendar_state",
        "month_notes",
        "plans",
    ):
        op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError("The Learning-only product cutover is intentionally irreversible.")
