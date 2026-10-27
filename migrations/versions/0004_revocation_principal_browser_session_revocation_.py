"""principal browser session revocation generation

Revision ID: 0004_revocation
Revises: 0003_sessions
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_revocation"
down_revision = "0003_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "principals",
        sa.Column(
            "session_generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
    )

    op.alter_column("principals", "session_generation", server_default=None)


def downgrade():
    op.drop_column("principals", "session_generation")
