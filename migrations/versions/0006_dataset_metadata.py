"""Revision-safe mutable catalog metadata; stable dataset and draft identities remain intact."""

import sqlalchemy as sa
from alembic import op

revision = "0006_dataset_metadata"
down_revision = "0005_identity_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "datasets", sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.create_check_constraint("ck_dataset_revision", "datasets", "revision >= 0")


def downgrade():
    # Rolling back removes edit concurrency history; back up before an operator-requested downgrade.
    op.drop_constraint("ck_dataset_revision", "datasets", type_="check")
    op.drop_column("datasets", "revision")
