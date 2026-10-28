"""Bounded immutable generation preparation; provisional numbering before #19 integration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_generations"
down_revision = "0007_candidates"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generation_members",
        sa.Column("generation_id", sa.Uuid(), primary_key=True),
        sa.Column("sample_id", sa.Uuid(), primary_key=True),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("sample_revision_id", sa.Uuid(), nullable=False),
        sa.Column("split", sa.String(50), nullable=False),
        sa.Column("interpretation", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        sa.CheckConstraint("split ~ '^[a-z][a-z0-9_-]*$'", name="ck_generation_split"),
        sa.CheckConstraint(
            "jsonb_typeof(interpretation) = 'object'", name="ck_generation_interpretation"
        ),
    )
    op.execute("""
      CREATE FUNCTION guard_generation_member() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE generation_state text;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'generation membership is immutable';
        END IF;
        SELECT state INTO generation_state FROM publication_candidates
          WHERE namespace_id = NEW.namespace_id AND generation_id = NEW.generation_id
          FOR UPDATE;
        IF generation_state IS DISTINCT FROM 'preparing' THEN
          RAISE EXCEPTION 'generation no longer accepts members';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER immutable_generation_member BEFORE INSERT OR UPDATE OR DELETE
        ON generation_members FOR EACH ROW EXECUTE FUNCTION guard_generation_member();
    """)


def downgrade():
    op.drop_table("generation_members")
    op.execute("DROP FUNCTION guard_generation_member()")
