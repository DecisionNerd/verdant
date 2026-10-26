"""Registry identities and revisioned draft membership

Revision ID: 0001_registry
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_registry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "namespaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_audit_version"),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("modality", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(name) > 0 AND length(modality) > 0", name="ck_dataset_values"),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_dataset_namespace_id"),
        sa.UniqueConstraint("namespace_id", "slug", name="uq_dataset_slug"),
    )
    op.create_table(
        "samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=500), nullable=False),
        sa.Column("modality", sa.String(length=50), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "length(source_key) > 0 AND length(modality) > 0", name="ck_sample_values"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_sample_revision"),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_sample_namespace_id"),
        sa.UniqueConstraint("namespace_id", "source_key", name="uq_sample_source"),
    )
    op.create_table(
        "dataset_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_draft_revision"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "dataset_id"],
            ["datasets.namespace_id", "datasets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "dataset_id", name="uq_dataset_draft"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_draft_namespace_id"),
    )
    op.create_table(
        "sample_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("sample_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_revision_metadata"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_digest"),
        sa.CheckConstraint("length(object_key) > 0", name="ck_revision_object"),
        sa.CheckConstraint("revision >= 1 AND size_bytes >= 0", name="ck_revision_values"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "sample_id"],
            ["samples.namespace_id", "samples.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "sample_id", "id", name="uq_sample_revision_identity"),
        sa.UniqueConstraint(
            "namespace_id", "sample_id", "revision", name="uq_sample_revision_number"
        ),
    )
    op.create_table(
        "draft_memberships",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("sample_id", sa.Uuid(), nullable=False),
        sa.Column("valid_from", sa.BigInteger(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("sample_revision_id", sa.Uuid(), nullable=False),
        sa.Column("split", sa.String(length=50), nullable=False),
        sa.Column("valid_to", sa.BigInteger(), nullable=True),
        postgresql.ExcludeConstraint(
            (sa.column("draft_id"), "="),
            (sa.column("sample_id"), "="),
            (sa.text("int8range(valid_from, valid_to, '[)')"), "&&"),
            using="gist",
            name="ex_membership_intervals",
        ),
        sa.CheckConstraint("split ~ '^[a-z][a-z0-9_-]*$'", name="ck_membership_split"),
        sa.CheckConstraint(
            "valid_from >= 1 AND (valid_to IS NULL OR valid_to > valid_from)",
            name="ck_membership_interval",
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "draft_id"],
            ["dataset_drafts.namespace_id", "dataset_drafts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("draft_id", "sample_id", "valid_from"),
    )
    op.create_index(
        "uq_current_membership",
        "draft_memberships",
        ["draft_id", "sample_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )

    op.execute("""
        CREATE FUNCTION registry_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Immutable registry evidence cannot be changed' USING ERRCODE='23514';
        END $$;
        CREATE TRIGGER immutable_sample_revision BEFORE UPDATE OR DELETE ON sample_revisions
            FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation();
        CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation();
        CREATE FUNCTION registry_close_membership() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Membership history cannot be deleted' USING ERRCODE='23514';
            END IF;
            IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
                OR ROW(NEW.namespace_id, NEW.draft_id, NEW.sample_id, NEW.sample_revision_id,
                       NEW.valid_from, NEW.split)
                   IS DISTINCT FROM
                   ROW(OLD.namespace_id, OLD.draft_id, OLD.sample_id, OLD.sample_revision_id,
                       OLD.valid_from, OLD.split) THEN
                RAISE EXCEPTION 'Only an open membership interval can be closed'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_membership BEFORE UPDATE OR DELETE ON draft_memberships
            FOR EACH ROW EXECUTE FUNCTION registry_close_membership();
    """)


def downgrade():
    op.drop_index(
        "uq_current_membership",
        table_name="draft_memberships",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_table("draft_memberships")
    op.drop_table("sample_revisions")
    op.drop_table("dataset_drafts")
    op.drop_table("samples")
    op.drop_table("datasets")
    op.drop_table("audit_events")
    op.drop_table("namespaces")
    op.execute("DROP FUNCTION registry_close_membership()")
    op.execute("DROP FUNCTION registry_reject_mutation()")
    # btree_gist may be shared with other schemas; never remove it in downgrade.
