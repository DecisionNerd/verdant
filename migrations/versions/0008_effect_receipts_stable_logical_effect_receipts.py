"""Stable logical effect receipts

Revision ID: 0008_effect_receipts
Revises: 0007_operations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_effect_receipts"
down_revision = "0007_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "effect_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("effect_key", sa.String(length=64), nullable=False),
        sa.Column("identity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="prepared", nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=True),
        sa.Column("dispatch_epoch", sa.BigInteger(), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("provider_reference", sa.String(length=1000), nullable=True),
        sa.Column("reconciliation_evidence", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_effect_digest"),
        sa.CheckConstraint("status IN ('prepared','unknown','succeeded')", name="ck_effect_status"),
        sa.CheckConstraint("revision >= 0", name="ck_effect_revision"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "operation_id"],
            ["operations.namespace_id", "operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "effect_key", name="uq_logical_effect"),
    )


def downgrade():
    op.drop_table("effect_receipts")
