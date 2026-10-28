"""Durable frozen candidate capture.

Capture extends the accepted durable operations migration chain.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_candidates"
down_revision = "0011_workflow_deployment"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publication_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), server_default="preparing", nullable=False),
        sa.Column("prepared_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "draft_id"], ["dataset_drafts.namespace_id", "dataset_drafts.id"]
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["principals.id"]),
        sa.UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_candidate_request"
        ),
        sa.UniqueConstraint("namespace_id", "generation_id", name="uq_candidate_generation"),
        sa.CheckConstraint(
            "source_revision >= 0 AND prepared_count >= 0", name="ck_candidate_progress"
        ),
        sa.CheckConstraint("state IN ('preparing', 'sealed')", name="ck_candidate_state"),
        sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_candidate_request_digest"),
    )
    op.execute("""
        CREATE FUNCTION guard_candidate_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'candidate capture cannot be deleted';
          END IF;
          IF (NEW.id, NEW.namespace_id, NEW.draft_id, NEW.source_revision, NEW.generation_id,
              NEW.creator_id, NEW.request_key, NEW.request_digest, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.namespace_id, OLD.draft_id, OLD.source_revision, OLD.generation_id,
              OLD.creator_id, OLD.request_key, OLD.request_digest, OLD.created_at) THEN
            RAISE EXCEPTION 'candidate capture identity is immutable';
          END IF;
          IF OLD.state = 'sealed' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'sealed candidate is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER candidate_identity BEFORE UPDATE OR DELETE ON publication_candidates
        FOR EACH ROW EXECUTE FUNCTION guard_candidate_identity();
    """)


def downgrade():
    op.drop_table("publication_candidates")
    op.execute("DROP FUNCTION guard_candidate_identity()")
