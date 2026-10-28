"""Atomic operation admission and outbox

Revision ID: 0007_operations
Revises: 0006_dataset_metadata
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_operations"
down_revision = "0006_dataset_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="queued", nullable=False),
        sa.Column("epoch", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_operation_digest"),
        sa.CheckConstraint(
            "status IN ('queued','running','cancellation_requested','cancelled',"
            "'failed','succeeded','reconciliation_needed')",
            name="ck_operation_status",
        ),
        sa.CheckConstraint("epoch >= 1 AND revision >= 0", name="ck_operation_revision"),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["principals.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_operation_request"
        ),
        sa.UniqueConstraint("namespace_id", "id", name="uq_operation_namespace_id"),
    )
    op.create_table(
        "operation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "operation_id"],
            ["operations.namespace_id", "operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "revision", name="uq_operation_event_revision"),
    )
    op.create_table(
        "operation_outbox",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("dispatched", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["operation_events.id"],
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade():
    op.drop_table("operation_outbox")
    op.drop_table("operation_events")
    op.drop_table("operations")
