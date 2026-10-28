"""Immutable re-upload lineage and superseded origins.

Revision ID: 0016_ingestion_retry
Revises: 0015_ingestion
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_ingestion_retry"
down_revision = "0015_ingestion"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ingestion_items", sa.Column("retry_of_item_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_ingestion_namespace_id", "ingestion_items", ["namespace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_ingestion_retry_origin", "ingestion_items", ["retry_of_item_id"]
    )
    op.create_foreign_key(
        "fk_ingestion_retry_origin",
        "ingestion_items",
        "ingestion_items",
        ["namespace_id", "retry_of_item_id"],
        ["namespace_id", "id"],
    )
    op.create_check_constraint(
        "ck_ingestion_retry_self",
        "ingestion_items",
        "retry_of_item_id IS NULL OR retry_of_item_id <> id",
    )
    op.drop_constraint("ck_ingestion_state", "ingestion_items", type_="check")
    op.create_check_constraint(
        "ck_ingestion_state",
        "ingestion_items",
        "state IN ('awaiting_upload','uploading','ready','accepted','failed',"
        "'cancelled','superseded')",
    )
    op.execute("""
      CREATE FUNCTION guard_ingestion_retry() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE matching uuid;
      BEGIN
        IF TG_OP = 'UPDATE' THEN
          IF NEW.retry_of_item_id IS DISTINCT FROM OLD.retry_of_item_id OR
             (OLD.state = 'superseded' AND NEW IS DISTINCT FROM OLD) THEN
            RAISE EXCEPTION 'reupload lineage and superseded outcome are immutable';
          END IF;
        ELSIF NEW.retry_of_item_id IS NOT NULL THEN
          SELECT original.id INTO matching FROM ingestion_items original
            JOIN operations o ON o.namespace_id = original.namespace_id
              AND o.id = original.operation_id
            JOIN operations child_operation ON child_operation.namespace_id = NEW.namespace_id
              AND child_operation.id = NEW.operation_id
            JOIN artifacts old_artifact ON old_artifact.namespace_id = original.namespace_id
              AND old_artifact.id = original.artifact_id
            JOIN artifacts new_artifact ON new_artifact.namespace_id = NEW.namespace_id
              AND new_artifact.id = NEW.artifact_id
            WHERE original.namespace_id = NEW.namespace_id AND original.id = NEW.retry_of_item_id
              AND original.state = 'superseded' AND o.status IN ('failed', 'cancelled')
              AND o.kind = 'ingest_upload'
              AND child_operation.id <> o.id AND child_operation.kind = 'ingest_upload'
              AND child_operation.status = 'running'
              AND NEW.artifact_id <> original.artifact_id
              AND NEW.staging_key <> original.staging_key
              AND ROW(original.source_key, original.modality, original.metadata,
                      original.target_sample_id, original.expected_revision,
                      old_artifact.sha256, old_artifact.size_bytes)
                  IS NOT DISTINCT FROM
                  ROW(NEW.source_key, NEW.modality, NEW.metadata,
                      NEW.target_sample_id, NEW.expected_revision,
                      new_artifact.sha256, new_artifact.size_bytes)
            FOR SHARE OF original, o, child_operation;
          IF matching IS NULL THEN
            RAISE EXCEPTION 'reupload requires fresh operation and exact superseded declaration';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER immutable_ingestion_retry BEFORE INSERT OR UPDATE ON ingestion_items
        FOR EACH ROW EXECUTE FUNCTION guard_ingestion_retry();
      CREATE FUNCTION require_ingestion_replacement() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.state = 'superseded' AND NOT EXISTS (
          SELECT 1 FROM ingestion_items child WHERE child.namespace_id = NEW.namespace_id
            AND child.retry_of_item_id = NEW.id
        ) THEN
          RAISE EXCEPTION 'superseded item requires its replacement in the same transaction';
        END IF;
        RETURN NEW;
      END $$;
      CREATE CONSTRAINT TRIGGER durable_ingestion_replacement AFTER UPDATE ON ingestion_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION require_ingestion_replacement();
    """)


def downgrade():
    # Preserve evidence: the older schema cannot represent already-used recovery lineage.
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM ingestion_items
                   WHERE retry_of_item_id IS NOT NULL OR state = 'superseded') THEN
          RAISE EXCEPTION 'used reupload lineage requires a preserving migration before downgrade';
        END IF;
      END $$;
    """)
    op.execute("DROP TRIGGER durable_ingestion_replacement ON ingestion_items")
    op.execute("DROP TRIGGER immutable_ingestion_retry ON ingestion_items")
    op.execute("DROP FUNCTION require_ingestion_replacement()")
    op.execute("DROP FUNCTION guard_ingestion_retry()")
    op.drop_constraint("ck_ingestion_state", "ingestion_items", type_="check")
    op.create_check_constraint(
        "ck_ingestion_state",
        "ingestion_items",
        "state IN ('awaiting_upload','uploading','ready','accepted','failed','cancelled')",
    )
    op.drop_constraint("ck_ingestion_retry_self", "ingestion_items", type_="check")
    op.drop_constraint("fk_ingestion_retry_origin", "ingestion_items", type_="foreignkey")
    op.drop_constraint("uq_ingestion_retry_origin", "ingestion_items", type_="unique")
    op.drop_constraint("uq_ingestion_namespace_id", "ingestion_items", type_="unique")
    op.drop_column("ingestion_items", "retry_of_item_id")
