"""Pin inline immutable interpretation evidence in temporal membership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_interpretation"
down_revision = "0013_generations"
branch_labels = None
depends_on = None

DEFAULT = """jsonb_build_object('schema', 'verdant.membership.v1',
    'annotation_revision', NULL, 'task_schema_revision', NULL,
    'review_evidence', NULL, 'transformation', NULL)"""


def closure_function(include_interpretation):
    new_pin = ", NEW.interpretation" if include_interpretation else ""
    old_pin = ", OLD.interpretation" if include_interpretation else ""
    op.execute(f"""
      CREATE OR REPLACE FUNCTION registry_close_membership() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Membership history cannot be deleted' USING ERRCODE='23514';
        END IF;
        IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
           OR ROW(NEW.namespace_id, NEW.draft_id, NEW.sample_id, NEW.sample_revision_id,
                  NEW.valid_from, NEW.split{new_pin}) IS DISTINCT FROM
              ROW(OLD.namespace_id, OLD.draft_id, OLD.sample_id, OLD.sample_revision_id,
                  OLD.valid_from, OLD.split{old_pin}) THEN
          RAISE EXCEPTION 'Only an open membership interval can be closed' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
    """)


def upgrade():
    op.add_column(
        "draft_memberships",
        sa.Column(
            "interpretation", postgresql.JSONB(), server_default=sa.text(DEFAULT), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_membership_interpretation",
        "draft_memberships",
        "jsonb_typeof(interpretation) = 'object'",
    )
    closure_function(True)


def downgrade():
    # Evidence is discarded by an explicit downgrade; back up before choosing this rollback.
    closure_function(False)
    op.drop_constraint("ck_membership_interpretation", "draft_memberships", type_="check")
    op.drop_column("draft_memberships", "interpretation")
