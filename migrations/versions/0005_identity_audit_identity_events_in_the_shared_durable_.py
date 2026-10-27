"""identity events in the shared durable audit stream

Revision ID: 0005_identity_audit
Revises: 0004_revocation
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_identity_audit"
down_revision = "0004_revocation"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("audit_events", "namespace_id", existing_type=sa.UUID(), nullable=True)

    op.create_check_constraint(
        "ck_audit_scope",
        "audit_events",
        "namespace_id IS NOT NULL OR action LIKE 'identity.%'",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM audit_events WHERE namespace_id IS NULL) THEN
                RAISE EXCEPTION 'Global identity audit prevents downgrade; preserve evidence';
            END IF;
        END $$
    """)
    op.drop_constraint("ck_audit_scope", "audit_events", type_="check")
    op.alter_column("audit_events", "namespace_id", existing_type=sa.UUID(), nullable=False)
