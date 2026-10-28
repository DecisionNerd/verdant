"""Workflow inbox checkpoints and dispatch identity

Revision ID: 0010_workflow_inbox
Revises: 0009_verification_result
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_workflow_inbox"
down_revision = "0009_verification_result"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "consumer_checkpoints",
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id", "operation_id"],
            ["operations.namespace_id", "operations.id"],
        ),
        sa.PrimaryKeyConstraint("consumer", "operation_id"),
    )
    op.create_table(
        "consumer_inbox",
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('applied','stale','ignored','quarantined')", name="ck_inbox_outcome"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["operation_events.id"],
        ),
        sa.PrimaryKeyConstraint("consumer", "event_id"),
    )
    op.create_table(
        "workflow_dispatches",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_key", sa.String(length=200), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("provider_reference", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','unknown','submitted')", name="ck_workflow_dispatch_status"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["operation_events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "operation_id"],
            ["operations.namespace_id", "operations.id"],
        ),
        sa.PrimaryKeyConstraint("operation_id", "epoch"),
        sa.UniqueConstraint("event_id", name="uq_workflow_dispatch_event"),
        sa.UniqueConstraint("invocation_key", name="uq_workflow_invocation"),
    )


def downgrade():
    op.drop_table("workflow_dispatches")
    op.drop_table("consumer_inbox")
    op.drop_table("consumer_checkpoints")
