"""immutable quality policy revisions and resumable candidate reports

Revision ID: 0019_quality
Revises: 0018_ingestion_sources
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_quality"
down_revision = "0018_ingestion_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quality_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_quality_policy_definition"
        ),
        sa.CheckConstraint("revision >= 1 AND length(name) > 0", name="ck_quality_policy_revision"),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["principals.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "dataset_id"],
            ["datasets.namespace_id", "datasets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_quality_policy_request"
        ),
        sa.UniqueConstraint(
            "namespace_id", "dataset_id", "name", "revision", name="uq_quality_policy_revision"
        ),
        sa.UniqueConstraint("namespace_id", "id", name="uq_quality_policy_namespace"),
    )
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_revision", sa.BigInteger(), nullable=False),
        sa.Column("evaluator_version", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("expected_count", sa.BigInteger(), nullable=False),
        sa.Column("processed_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(results) = 'array'", name="ck_quality_report_results"),
        sa.CheckConstraint(
            "state <> 'complete' OR processed_count = expected_count",
            name="ck_quality_report_complete",
        ),
        sa.CheckConstraint("state IN ('running', 'complete')", name="ck_quality_report_state"),
        sa.CheckConstraint(
            "0 <= processed_count AND processed_count <= expected_count",
            name="ck_quality_report_progress",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["publication_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["principals.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "dataset_id"],
            ["datasets.namespace_id", "datasets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "policy_id"],
            ["quality_policies.namespace_id", "quality_policies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_quality_report_request"
        ),
    )
    op.execute("""
      CREATE TRIGGER immutable_quality_policy BEFORE UPDATE OR DELETE ON quality_policies
      FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation();

      CREATE FUNCTION guard_quality_report() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE c publication_candidates; p quality_policies;
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Validation evidence cannot be deleted' USING ERRCODE='23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
          SELECT pc.* INTO c FROM publication_candidates pc
          JOIN dataset_drafts d ON d.id = pc.draft_id
          WHERE pc.id = NEW.candidate_id AND pc.namespace_id = NEW.namespace_id
            AND d.dataset_id = NEW.dataset_id FOR SHARE OF pc;
          SELECT * INTO p FROM quality_policies WHERE id = NEW.policy_id
            AND namespace_id = NEW.namespace_id AND dataset_id = NEW.dataset_id;
          IF c.id IS NULL OR p.id IS NULL OR c.state <> 'sealed'
            OR c.generation_id <> NEW.generation_id OR c.source_revision <> NEW.source_revision
            OR c.prepared_count <> NEW.expected_count OR p.revision <> NEW.policy_revision
            OR NEW.state <> 'running' OR NEW.processed_count <> 0 OR NEW.cursor IS NOT NULL THEN
            RAISE EXCEPTION 'Validation must start at a sealed generation and exact policy'
              USING ERRCODE='23514';
          END IF;
        ELSE
          IF (NEW.id, NEW.namespace_id, NEW.dataset_id, NEW.candidate_id, NEW.generation_id,
              NEW.source_revision, NEW.policy_id, NEW.policy_revision, NEW.evaluator_version,
              NEW.expected_count, NEW.creator_id, NEW.request_key, NEW.request_digest,
              NEW.created_at)
            IS DISTINCT FROM
             (OLD.id, OLD.namespace_id, OLD.dataset_id, OLD.candidate_id, OLD.generation_id,
              OLD.source_revision, OLD.policy_id, OLD.policy_revision, OLD.evaluator_version,
              OLD.expected_count, OLD.creator_id, OLD.request_key, OLD.request_digest,
              OLD.created_at)
            OR (OLD.state = 'complete' AND NEW IS DISTINCT FROM OLD)
            OR NEW.processed_count < OLD.processed_count
            OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor < OLD.cursor)) THEN
            RAISE EXCEPTION 'Validation identity and completed evidence are immutable'
              USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER quality_report_guard BEFORE INSERT OR UPDATE OR DELETE ON quality_reports
      FOR EACH ROW EXECUTE FUNCTION guard_quality_report();
    """)


def downgrade():
    op.drop_table("quality_reports")
    op.execute("DROP FUNCTION guard_quality_report()")
    op.drop_table("quality_policies")
