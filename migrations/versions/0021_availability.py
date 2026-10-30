"""Mutable catalog availability over immutable published version content.

Revision ID: 0021_availability
Revises: 0020_publication
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_availability"
down_revision = "0020_publication"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("availability", "prior_availability"):
        op.add_column(
            "dataset_versions",
            sa.Column(name, sa.String(20), server_default="active", nullable=False),
        )
    op.add_column(
        "dataset_versions",
        sa.Column("availability_revision", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_dataset_version_availability",
        "dataset_versions",
        "availability IN ('active','deprecated','archived')"
        " AND prior_availability IN ('active','deprecated')"
        " AND (availability = 'archived' OR availability = prior_availability)"
        " AND availability_revision >= 0",
    )
    op.execute("DROP TRIGGER immutable_dataset_version ON dataset_versions")
    op.execute("""
      CREATE FUNCTION guard_version_availability() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Published content cannot be deleted' USING ERRCODE='23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NEW.availability <> 'active' OR NEW.prior_availability <> 'active'
            OR NEW.availability_revision <> 0 THEN
            RAISE EXCEPTION 'Published versions begin active at availability revision zero'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END IF;
        IF NEW IS NOT DISTINCT FROM OLD THEN
          RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - 'availability' - 'prior_availability' - 'availability_revision')
          IS DISTINCT FROM
           (to_jsonb(OLD) - 'availability' - 'prior_availability' - 'availability_revision')
          OR NEW.availability_revision <> OLD.availability_revision + 1 THEN
          RAISE EXCEPTION 'Only revision-checked catalog availability may change'
            USING ERRCODE='23514';
        END IF;
        IF NOT (
          (OLD.availability = 'active' AND NEW.availability = 'deprecated'
            AND NEW.prior_availability = 'deprecated')
          OR (OLD.availability = 'deprecated' AND NEW.availability = 'active'
            AND NEW.prior_availability = 'active')
          OR (OLD.availability IN ('active','deprecated') AND NEW.availability = 'archived'
            AND NEW.prior_availability = OLD.availability)
          OR (OLD.availability = 'archived' AND NEW.availability = OLD.prior_availability
            AND NEW.prior_availability = OLD.prior_availability)
        ) THEN
          RAISE EXCEPTION 'Invalid version availability transition' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER version_availability_guard
      BEFORE INSERT OR UPDATE OR DELETE ON dataset_versions
      FOR EACH ROW EXECUTE FUNCTION guard_version_availability();
    """)


def downgrade():
    op.execute("DROP TRIGGER version_availability_guard ON dataset_versions")
    op.execute("DROP FUNCTION guard_version_availability()")
    op.execute("""
      CREATE TRIGGER immutable_dataset_version BEFORE UPDATE OR DELETE ON dataset_versions
      FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation()
    """)
    op.drop_constraint("ck_dataset_version_availability", "dataset_versions", type_="check")
    for name in ("availability_revision", "prior_availability", "availability"):
        op.drop_column("dataset_versions", name)
