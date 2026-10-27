"""browser sessions and single use login attempts

Revision ID: 0003_sessions
Revises: 0002_identity
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_sessions"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
    )


def downgrade():
    op.drop_table("login_attempts")
    op.drop_table("browser_sessions")
