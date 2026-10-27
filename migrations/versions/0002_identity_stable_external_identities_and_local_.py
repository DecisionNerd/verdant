"""stable external identities and local namespace grants

Revision ID: 0002_identity
Revises: 0001_registry
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity"
down_revision = "0001_registry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "principals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('human', 'service')", name="ck_principal_kind"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.CheckConstraint(
            "length(issuer) > 0 AND length(subject) > 0", name="ck_external_subject"
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_subject"),
        sa.UniqueConstraint("principal_id"),
    )
    op.create_table(
        "namespace_grants",
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("access", sa.String(length=10), nullable=False),
        sa.CheckConstraint("access IN ('read', 'write')", name="ck_namespace_access"),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
        ),
        sa.PrimaryKeyConstraint("principal_id", "namespace_id"),
    )


def downgrade():
    op.drop_table("namespace_grants")
    op.drop_table("external_identities")
    op.drop_table("principals")
