"""Accepted artifact verification result

Revision ID: 0009_verification_result
Revises: 0008_effect_receipts
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_verification_result"
down_revision = "0008_effect_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_verification_results",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("sample_id", sa.Uuid(), nullable=False),
        sa.Column("sample_revision_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("operation_id"),
    )


def downgrade():
    op.drop_table("operation_verification_results")
