"""Aggregate publication retention and immutable dataset versions.

Revision ID: 0020_publication
Revises: 0019_quality
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_publication"
down_revision = "0019_quality"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "datasets",
        sa.Column("availability", sa.String(20), server_default="active", nullable=False),
    )
    op.create_check_constraint(
        "ck_dataset_availability", "datasets", "availability IN ('active', 'archived')"
    )
    op.add_column(
        "artifacts",
        sa.Column("collection_state", sa.String(20), server_default="active", nullable=False),
    )
    op.create_check_constraint(
        "ck_artifact_collection", "artifacts", "collection_state IN ('active','deleting','deleted')"
    )
    op.create_index(
        "ix_ingestion_artifact_protection",
        "ingestion_items",
        ["namespace_id", "artifact_id", "state"],
    )
    op.create_index(
        "ix_sample_artifact_protection", "sample_artifacts", ["namespace_id", "artifact_id"]
    )
    op.create_table(
        "publication_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("expected_count", sa.BigInteger(), nullable=False),
        sa.Column("prepared_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(20), server_default="building", nullable=False),
        sa.Column("epoch", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp() + interval '24 hours'"),
            nullable=False,
        ),
        sa.Column("manifest_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("manifest_digest", sa.String(64), nullable=True),
        sa.Column("manifest_size", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_publication_group_namespace"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "id"], ["operations.namespace_id", "operations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["publication_candidates.id"]),
        sa.ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        sa.ForeignKeyConstraint(["report_id"], ["quality_reports.id"]),
        sa.ForeignKeyConstraint(
            ["namespace_id", "manifest_artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        sa.CheckConstraint(
            "state IN ('building','prepared','published','releasing','released')",
            name="ck_publication_group_state",
        ),
        sa.CheckConstraint(
            "0 <= prepared_count AND prepared_count <= expected_count AND epoch >= 0",
            name="ck_publication_group_progress",
        ),
        sa.CheckConstraint(
            "(manifest_artifact_id IS NULL AND manifest_digest IS NULL AND manifest_size IS NULL)"
            " OR (manifest_artifact_id IS NOT NULL AND manifest_digest IS NOT NULL"
            " AND manifest_size IS NOT NULL AND manifest_digest ~ '^[0-9a-f]{64}$'"
            " AND manifest_size BETWEEN 0 AND 268435456)",
            name="ck_publication_group_manifest",
        ),
    )
    op.create_index(
        "ix_publication_group_target",
        "publication_groups",
        ["namespace_id", "dataset_id", "candidate_id", "id"],
    )
    op.create_table(
        "retention_references",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("group_id", "artifact_id"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "group_id"],
            ["publication_groups.namespace_id", "publication_groups.id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
    )
    op.create_index(
        "ix_retention_artifact_group", "retention_references", ["artifact_id", "group_id"]
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("manifest_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "dataset_id", "label", name="uq_dataset_version_label"),
        sa.UniqueConstraint("group_id", name="uq_dataset_version_group"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "group_id"],
            ["publication_groups.namespace_id", "publication_groups.id"],
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["publication_candidates.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["quality_reports.id"]),
        sa.ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "manifest_artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        sa.CheckConstraint("length(btrim(label)) > 0", name="ck_dataset_version_label"),
        sa.CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="ck_dataset_version_digest"),
        sa.CheckConstraint("manifest_size BETWEEN 0 AND 268435456", name="ck_dataset_version_size"),
    )
    op.create_index(
        "ix_dataset_version_page", "dataset_versions", ["namespace_id", "dataset_id", "id"]
    )
    op.execute(_PUBLICATION_GUARDS)
    op.execute(_ARTIFACT_GUARDS)


_PUBLICATION_GUARDS = """
CREATE FUNCTION guard_publication_group() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c publication_candidates;
  r quality_reports;
  o operations;
  renewing boolean := false;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Publication identity cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT pc.* INTO c FROM publication_candidates pc
      JOIN dataset_drafts d ON d.id = pc.draft_id AND d.namespace_id = pc.namespace_id
      WHERE pc.id = NEW.candidate_id AND pc.namespace_id = NEW.namespace_id
        AND d.dataset_id = NEW.dataset_id FOR SHARE OF pc;
    SELECT * INTO r FROM quality_reports WHERE id = NEW.report_id
      AND namespace_id = NEW.namespace_id AND dataset_id = NEW.dataset_id;
    SELECT * INTO o FROM operations WHERE id = NEW.id AND namespace_id = NEW.namespace_id;
    IF c.id IS NULL OR r.id IS NULL OR o.id IS NULL OR c.state <> 'sealed'
      OR c.generation_id <> NEW.generation_id OR c.prepared_count <> NEW.expected_count
      OR r.candidate_id <> NEW.candidate_id OR r.generation_id <> NEW.generation_id
      OR r.source_revision <> c.source_revision OR r.state <> 'complete'
      OR r.processed_count <> NEW.expected_count OR r.expected_count <> NEW.expected_count
      OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.results) e WHERE e->'required' = 'true')
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(r.results) e
        WHERE e->'required' = 'true' AND e->>'status' IS DISTINCT FROM 'passed')
      OR o.kind <> 'publish_dataset_version' OR o.status <> 'running'
      OR o.inputs->>'dataset_id' IS DISTINCT FROM NEW.dataset_id::text
      OR o.inputs->>'candidate_id' IS DISTINCT FROM NEW.candidate_id::text
      OR o.inputs->>'report_id' IS DISTINCT FROM NEW.report_id::text
      OR coalesce(o.inputs->>'mode', '') NOT IN ('selected_candidate','current_draft')
      OR NEW.state <> 'building' OR NEW.epoch <> 0 OR NEW.prepared_count <> 0
      OR NEW.cursor IS NOT NULL OR NEW.manifest_artifact_id IS NOT NULL
      OR NEW.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'Publication requires a sealed candidate and exact passing report'
        USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.id, NEW.namespace_id, NEW.dataset_id, NEW.candidate_id, NEW.generation_id,
      NEW.report_id, NEW.expected_count, NEW.created_at)
    IS DISTINCT FROM
     (OLD.id, OLD.namespace_id, OLD.dataset_id, OLD.candidate_id, OLD.generation_id,
      OLD.report_id, OLD.expected_count, OLD.created_at)
    OR NEW.epoch < OLD.epoch THEN
    RAISE EXCEPTION 'Publication identity and attempt fence are immutable'
      USING ERRCODE='23514';
  END IF;
  IF OLD.state IN ('published','released') THEN
    IF NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'Terminal publication groups are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  renewing := OLD.state IN ('building','prepared') AND NEW.state = 'building'
    AND OLD.expires_at <= clock_timestamp() AND NEW.expires_at > clock_timestamp()
    AND NEW.epoch > OLD.epoch AND NEW.prepared_count = 0 AND NEW.cursor IS NULL;
  IF renewing THEN
    RETURN NEW;
  END IF;
  IF NEW.expires_at IS DISTINCT FROM OLD.expires_at
    OR NEW.prepared_count < OLD.prepared_count
    OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor < OLD.cursor)) THEN
    RAISE EXCEPTION 'Only explicit expired-group renewal resets preparation'
      USING ERRCODE='23514';
  END IF;
  IF OLD.state = 'releasing' THEN
    IF NEW.state NOT IN ('releasing','released')
      OR (NEW.prepared_count, NEW.cursor, NEW.manifest_artifact_id,
          NEW.manifest_digest, NEW.manifest_size)
        IS DISTINCT FROM
         (OLD.prepared_count, OLD.cursor, OLD.manifest_artifact_id,
          OLD.manifest_digest, OLD.manifest_size)
      OR (NEW.state = 'released' AND EXISTS (
        SELECT 1 FROM retention_references WHERE group_id = OLD.id
      )) THEN
      RAISE EXCEPTION 'Release must finish removing references before completion'
        USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.state = 'releasing' THEN
    IF NEW.epoch <= OLD.epoch OR
      (NEW.prepared_count, NEW.cursor, NEW.manifest_artifact_id,
       NEW.manifest_digest, NEW.manifest_size)
      IS DISTINCT FROM
      (OLD.prepared_count, OLD.cursor, OLD.manifest_artifact_id,
       OLD.manifest_digest, OLD.manifest_size) THEN
      RAISE EXCEPTION 'Release must fence preparation and retain its identity'
        USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.state = 'prepared' THEN
    IF NEW.state NOT IN ('prepared','published') OR
      (NEW.epoch, NEW.prepared_count, NEW.cursor, NEW.manifest_artifact_id,
       NEW.manifest_digest, NEW.manifest_size)
      IS DISTINCT FROM
      (OLD.epoch, OLD.prepared_count, OLD.cursor, OLD.manifest_artifact_id,
       OLD.manifest_digest, OLD.manifest_size) THEN
      RAISE EXCEPTION 'Prepared evidence cannot change before publication'
        USING ERRCODE='23514';
    END IF;
    IF NEW.state = 'published' THEN
      -- Constant aggregate checks only: membership/reference scans belong to preparation.
      SELECT * INTO o FROM operations WHERE id = NEW.id AND namespace_id = NEW.namespace_id;
      IF NEW.expires_at <= clock_timestamp() OR o.status IS DISTINCT FROM 'succeeded'
        OR NOT EXISTS (SELECT 1 FROM datasets WHERE id = NEW.dataset_id
          AND namespace_id = NEW.namespace_id AND availability = 'active')
        OR (o.inputs->>'mode' = 'current_draft' AND NOT EXISTS (
          SELECT 1 FROM publication_candidates pc JOIN dataset_drafts d ON d.id = pc.draft_id
          WHERE pc.id = NEW.candidate_id AND pc.namespace_id = NEW.namespace_id
            AND d.revision = pc.source_revision
        )) THEN
        RAISE EXCEPTION 'Publication is expired, unavailable or lacks its committed result'
          USING ERRCODE='23514';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.state NOT IN ('building','prepared') THEN
    RAISE EXCEPTION 'Invalid publication state transition' USING ERRCODE='23514';
  END IF;
  IF (NEW.prepared_count, NEW.cursor) IS DISTINCT FROM (OLD.prepared_count, OLD.cursor)
    AND (NEW.prepared_count <= OLD.prepared_count OR NEW.cursor IS NULL
      OR (OLD.cursor IS NOT NULL AND NEW.cursor <= OLD.cursor)) THEN
    RAISE EXCEPTION 'A preparation checkpoint must advance both count and cursor'
      USING ERRCODE='23514';
  END IF;
  IF NEW.state = 'prepared' THEN
    IF NEW.expires_at <= clock_timestamp() OR NEW.epoch = 0
      OR NEW.prepared_count <> NEW.expected_count OR NEW.manifest_artifact_id IS NULL
      OR (SELECT count(*) FROM generation_members m
        WHERE m.namespace_id = NEW.namespace_id AND m.generation_id = NEW.generation_id)
        <> NEW.expected_count
      OR EXISTS (
        SELECT 1 FROM generation_members m
        LEFT JOIN sample_artifacts s ON s.namespace_id = m.namespace_id
          AND s.sample_id = m.sample_id AND s.revision_id = m.sample_revision_id
        LEFT JOIN retention_references rr ON rr.namespace_id = NEW.namespace_id
          AND rr.group_id = NEW.id AND rr.artifact_id = s.artifact_id
        LEFT JOIN artifacts a ON a.namespace_id = s.namespace_id AND a.id = s.artifact_id
        WHERE m.namespace_id = NEW.namespace_id AND m.generation_id = NEW.generation_id
          AND (s.artifact_id IS NULL OR rr.verified_at IS NULL OR a.verified_at IS NULL
            OR a.collection_state IS DISTINCT FROM 'active')
      ) OR EXISTS (
        SELECT 1 FROM retention_references rr JOIN artifacts a
          ON a.id = rr.artifact_id AND a.namespace_id = rr.namespace_id
        WHERE rr.group_id = NEW.id
          AND (rr.verified_at IS NULL OR a.verified_at IS NULL OR a.collection_state <> 'active')
      ) OR NOT EXISTS (
        SELECT 1 FROM retention_references rr JOIN artifacts a
          ON a.id = rr.artifact_id AND a.namespace_id = rr.namespace_id
        WHERE rr.group_id = NEW.id AND rr.namespace_id = NEW.namespace_id
          AND rr.artifact_id = NEW.manifest_artifact_id AND rr.verified_at IS NOT NULL
          AND a.verified_at IS NOT NULL AND a.collection_state = 'active'
          AND a.sha256 = NEW.manifest_digest AND a.size_bytes = NEW.manifest_size
      ) THEN
      RAISE EXCEPTION 'Readiness requires complete protected verified source and manifest evidence'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_group_guard BEFORE INSERT OR UPDATE OR DELETE ON publication_groups
  FOR EACH ROW EXECUTE FUNCTION guard_publication_group();

CREATE FUNCTION guard_retention_reference() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g publication_groups; a artifacts; target_group uuid; target_namespace uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT * INTO g FROM publication_groups
      WHERE id = OLD.group_id AND namespace_id = OLD.namespace_id FOR UPDATE;
    IF g.id IS NULL OR g.state <> 'releasing' THEN
      RAISE EXCEPTION 'Only releasing groups may lose protection' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND
    (NEW.group_id, NEW.artifact_id, NEW.namespace_id) IS DISTINCT FROM
    (OLD.group_id, OLD.artifact_id, OLD.namespace_id) THEN
    RAISE EXCEPTION 'Retention reference identity is immutable' USING ERRCODE='23514';
  END IF;
  target_group := NEW.group_id; target_namespace := NEW.namespace_id;
  SELECT * INTO g FROM publication_groups
    WHERE id = target_group AND namespace_id = target_namespace FOR UPDATE;
  IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
    RETURN NEW;
  END IF;
  SELECT * INTO a FROM artifacts WHERE id = NEW.artifact_id
    AND namespace_id = NEW.namespace_id FOR SHARE;
  IF g.id IS NULL OR g.state <> 'building' OR g.expires_at <= clock_timestamp()
    OR a.id IS NULL OR a.collection_state <> 'active'
    OR (TG_OP = 'INSERT' AND NEW.verified_at IS NOT NULL)
    OR (TG_OP = 'UPDATE' AND (OLD.verified_at IS NOT NULL OR NEW.verified_at IS NULL)) THEN
    RAISE EXCEPTION 'Protection requires active artifacts and an unexpired building group'
      USING ERRCODE='23514';
  END IF;
  IF TG_OP = 'UPDATE' AND a.verified_at IS NULL THEN
    RAISE EXCEPTION 'Retention verification requires accepted artifact evidence'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER retention_reference_guard BEFORE INSERT OR UPDATE OR DELETE ON retention_references
  FOR EACH ROW EXECUTE FUNCTION guard_retention_reference();

CREATE FUNCTION guard_dataset_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g publication_groups; o operations;
BEGIN
  SELECT * INTO g FROM publication_groups WHERE id = NEW.group_id
    AND namespace_id = NEW.namespace_id FOR SHARE;
  SELECT * INTO o FROM operations WHERE id = NEW.group_id AND namespace_id = NEW.namespace_id;
  IF g.id IS NULL OR o.id IS NULL OR g.state <> 'published' OR o.status <> 'succeeded'
    OR (NEW.dataset_id, NEW.candidate_id, NEW.generation_id, NEW.report_id,
        NEW.manifest_artifact_id, NEW.manifest_digest, NEW.manifest_size)
      IS DISTINCT FROM
       (g.dataset_id, g.candidate_id, g.generation_id, g.report_id,
        g.manifest_artifact_id, g.manifest_digest, g.manifest_size)
    OR o.inputs->>'label' IS DISTINCT FROM NEW.label THEN
    RAISE EXCEPTION 'Version requires its exact published group and operation result'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER dataset_version_guard BEFORE INSERT ON dataset_versions
  FOR EACH ROW EXECUTE FUNCTION guard_dataset_version();
CREATE TRIGGER immutable_dataset_version BEFORE UPDATE OR DELETE ON dataset_versions
  FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation();
"""


_ARTIFACT_GUARDS = """
CREATE OR REPLACE FUNCTION guard_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.collection_state <> 'active' THEN
      RAISE EXCEPTION 'Artifacts must begin active' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Artifact identity cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF (NEW.id, NEW.namespace_id, NEW.sha256, NEW.size_bytes)
    IS DISTINCT FROM (OLD.id, OLD.namespace_id, OLD.sha256, OLD.size_bytes)
    OR (OLD.verified_at IS NOT NULL AND NEW.verified_at IS DISTINCT FROM OLD.verified_at)
    OR (NEW.verified_at IS DISTINCT FROM OLD.verified_at
      AND (OLD.collection_state <> 'active' OR NEW.collection_state <> 'active')) THEN
    RAISE EXCEPTION 'Artifact identity and accepted verification are immutable'
      USING ERRCODE='23514';
  END IF;
  IF NEW.collection_state IS DISTINCT FROM OLD.collection_state THEN
    IF OLD.collection_state = 'active' AND NEW.collection_state = 'deleting' THEN
      IF EXISTS (SELECT 1 FROM retention_references rr JOIN publication_groups g
        ON g.id = rr.group_id AND g.namespace_id = rr.namespace_id
        WHERE rr.artifact_id = OLD.id AND rr.namespace_id = OLD.namespace_id
          AND g.state IN ('building','prepared','published'))
        OR EXISTS (SELECT 1 FROM sample_artifacts
          WHERE artifact_id = OLD.id AND namespace_id = OLD.namespace_id)
        OR EXISTS (SELECT 1 FROM ingestion_items
          WHERE artifact_id = OLD.id AND namespace_id = OLD.namespace_id
            AND state IN ('awaiting_upload','uploading','ready')) THEN
        RAISE EXCEPTION 'Referenced artifacts cannot be collected' USING ERRCODE='23514';
      END IF;
    ELSIF OLD.collection_state <> 'deleting' OR NEW.collection_state <> 'deleted' THEN
      RAISE EXCEPTION 'Artifact collection cannot be reversed or skipped' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER immutable_artifact ON artifacts;
CREATE TRIGGER immutable_artifact BEFORE INSERT OR UPDATE OR DELETE ON artifacts
  FOR EACH ROW EXECUTE FUNCTION guard_artifact();

CREATE OR REPLACE FUNCTION guard_sample_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE matched uuid;
BEGIN
  SELECT a.id INTO matched FROM artifacts a
    JOIN sample_revisions r ON r.namespace_id = a.namespace_id
      AND r.id = NEW.revision_id AND r.sample_id = NEW.sample_id
      AND r.sha256 = a.sha256 AND r.size_bytes = a.size_bytes
    JOIN artifact_locations l ON l.namespace_id = a.namespace_id
      AND l.artifact_id = a.id AND l.object_key = r.object_key
    WHERE a.namespace_id = NEW.namespace_id AND a.id = NEW.artifact_id
      AND a.verified_at IS NOT NULL AND a.collection_state = 'active' FOR SHARE OF a;
  IF matched IS NULL THEN
    RAISE EXCEPTION 'Sample requires matching verified artifact, active ownership and placement'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION guard_active_artifact_reference() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE matched uuid;
BEGIN
  SELECT id INTO matched FROM artifacts WHERE id = NEW.artifact_id
    AND namespace_id = NEW.namespace_id AND collection_state = 'active' FOR SHARE;
  IF matched IS NULL THEN
    RAISE EXCEPTION 'Artifact references require an active artifact' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER active_artifact_location BEFORE INSERT ON artifact_locations
  FOR EACH ROW EXECUTE FUNCTION guard_active_artifact_reference();
CREATE TRIGGER active_ingestion_artifact BEFORE INSERT OR UPDATE OF state ON ingestion_items
  FOR EACH ROW WHEN (NEW.state IN ('awaiting_upload','uploading','ready','accepted'))
  EXECUTE FUNCTION guard_active_artifact_reference();
"""


def downgrade():
    op.execute("DROP TRIGGER active_ingestion_artifact ON ingestion_items")
    op.execute("DROP TRIGGER active_artifact_location ON artifact_locations")
    op.execute("DROP FUNCTION guard_active_artifact_reference()")
    # Restore predecessor guards before removing the retention relations they reference.
    op.execute(_PREDECESSOR_ARTIFACT_GUARDS)
    op.execute("DROP TRIGGER immutable_artifact ON artifacts")
    op.execute("""
      CREATE TRIGGER immutable_artifact BEFORE UPDATE OR DELETE ON artifacts
      FOR EACH ROW EXECUTE FUNCTION guard_artifact()
    """)
    op.drop_table("dataset_versions")
    op.execute("DROP FUNCTION guard_dataset_version()")
    op.drop_table("retention_references")
    op.execute("DROP FUNCTION guard_retention_reference()")
    op.drop_table("publication_groups")
    op.execute("DROP FUNCTION guard_publication_group()")
    op.drop_constraint("ck_artifact_collection", "artifacts", type_="check")
    op.drop_column("artifacts", "collection_state")
    op.drop_index("ix_ingestion_artifact_protection", table_name="ingestion_items")
    op.drop_index("ix_sample_artifact_protection", table_name="sample_artifacts")
    op.drop_constraint("ck_dataset_availability", "datasets", type_="check")
    op.drop_column("datasets", "availability")


_PREDECESSOR_ARTIFACT_GUARDS = """
CREATE OR REPLACE FUNCTION guard_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'artifact identity cannot be deleted';
  END IF;
  IF ROW(NEW.id, NEW.namespace_id, NEW.sha256, NEW.size_bytes)
    IS DISTINCT FROM ROW(OLD.id, OLD.namespace_id, OLD.sha256, OLD.size_bytes)
    OR OLD.verified_at IS NOT NULL THEN
    RAISE EXCEPTION 'artifact identity and verified evidence are immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION guard_sample_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE matched uuid;
BEGIN
  SELECT a.id INTO matched FROM artifacts a
    JOIN sample_revisions r ON r.namespace_id = a.namespace_id
      AND r.id = NEW.revision_id AND r.sample_id = NEW.sample_id
      AND r.sha256 = a.sha256 AND r.size_bytes = a.size_bytes
    JOIN artifact_locations l ON l.namespace_id = a.namespace_id
      AND l.artifact_id = a.id AND l.object_key = r.object_key
    WHERE a.namespace_id = NEW.namespace_id AND a.id = NEW.artifact_id
      AND a.verified_at IS NOT NULL FOR SHARE OF a;
  IF matched IS NULL THEN
    RAISE EXCEPTION 'sample requires matching verified artifact and placement';
  END IF;
  RETURN NEW;
END $$;
"""
