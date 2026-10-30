"""Configured read-only sources share verified ingestion and linked cleanup.

Revision ID: 0018_ingestion_sources
Revises: 0017_ingestion_execution
"""

from alembic import op

revision = "0018_ingestion_sources"
down_revision = "0017_ingestion_execution"
branch_labels = None
depends_on = None


def guard_cleanup(kinds):
    op.execute(f"""
      CREATE OR REPLACE FUNCTION guard_cleanup_target() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE matching uuid;
      BEGIN
        SELECT parent.id INTO matching FROM operations parent
          JOIN operations child ON child.namespace_id = parent.namespace_id
          WHERE parent.namespace_id = NEW.namespace_id AND parent.id = NEW.parent_operation_id
            AND parent.epoch = NEW.parent_epoch AND parent.kind IN ({kinds})
            AND parent.status IN ('cancellation_requested', 'cancelled', 'succeeded')
            AND child.id = NEW.operation_id AND child.kind = 'ingest_cleanup'
            AND child.status = 'queued'
            AND child.inputs = jsonb_build_object(
              'parent_operation_id', parent.id::text, 'parent_epoch', parent.epoch)
          FOR SHARE OF parent, child;
        IF matching IS NULL THEN
          RAISE EXCEPTION 'cleanup requires its exact namespace-owned immutable target';
        END IF;
        RETURN NEW;
      END $$;
    """)


def guard_retry(kinds):
    op.execute(f"""
      CREATE OR REPLACE FUNCTION guard_ingestion_retry() RETURNS trigger LANGUAGE plpgsql AS $$
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
              AND o.kind IN ({kinds})
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
    """)


def upgrade():
    guard_cleanup("'ingest_upload', 'ingest_source'")
    guard_retry("'ingest_upload', 'ingest_source'")


def downgrade():
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM operations WHERE kind = 'ingest_source') THEN
          RAISE EXCEPTION 'source history needs a preserving migration before downgrade';
        END IF;
      END $$;
    """)
    guard_cleanup("'ingest_upload'")
    guard_retry("'ingest_upload'")
