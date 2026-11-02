"""Expand immutable process/profile storage and durable operation pause states.

Rollback refuses populated process tables or active new states; no data is discarded.
"""

from alembic import op
from sqlalchemy import text

revision = "0022_process"
down_revision = "0021_availability"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_operation_status", "operations", type_="check")
    op.create_check_constraint(
        "ck_operation_status",
        "operations",
        "status IN ('queued','running','cancellation_requested','cancelled',"
        "'failed','succeeded','reconciliation_needed','pause_requested','paused')",
    )
    op.execute("""
CREATE TABLE model_profiles (
	id UUID NOT NULL,
	namespace_id UUID NOT NULL,
	name VARCHAR(100) NOT NULL,
	revision BIGINT NOT NULL,
	definition JSONB NOT NULL,
	credential_ref VARCHAR(110) NOT NULL,
	config_digest VARCHAR(64) NOT NULL,
	creator_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_model_profile_namespace UNIQUE (namespace_id, id),
	CONSTRAINT uq_model_profile_revision UNIQUE (namespace_id, name, revision),
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id),
	FOREIGN KEY(creator_id) REFERENCES principals (id),
	CONSTRAINT ck_model_profile_revision CHECK (revision >= 1)
)
    """)
    op.execute("""
CREATE TABLE process_flows (
	id UUID NOT NULL,
	namespace_id UUID NOT NULL,
	dataset_id UUID NOT NULL,
	name VARCHAR(100) NOT NULL,
	revision BIGINT NOT NULL,
	definition JSONB NOT NULL,
	config_digest VARCHAR(64) NOT NULL,
	creator_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_process_flow_namespace UNIQUE (namespace_id, id),
	CONSTRAINT uq_process_flow_revision UNIQUE (namespace_id, dataset_id, name, revision),
	FOREIGN KEY(namespace_id, dataset_id) REFERENCES datasets (namespace_id, id),
	FOREIGN KEY(creator_id) REFERENCES principals (id),
	CONSTRAINT ck_process_flow_revision CHECK (revision >= 1)
)
    """)
    op.execute("""
CREATE TABLE process_runs (
	operation_id UUID NOT NULL,
	namespace_id UUID NOT NULL,
	dataset_id UUID NOT NULL,
	flow_id UUID NOT NULL,
	candidate_id UUID NOT NULL,
	version_id UUID,
	generation_id UUID NOT NULL,
	member_count INTEGER NOT NULL,
	completed_count INTEGER DEFAULT '0' NOT NULL,
	cursor UUID,
	trial_of UUID,
	PRIMARY KEY (operation_id),
	FOREIGN KEY(namespace_id, operation_id) REFERENCES operations (namespace_id, id),
	FOREIGN KEY(namespace_id, flow_id) REFERENCES process_flows (namespace_id, id),
	FOREIGN KEY(namespace_id, dataset_id) REFERENCES datasets (namespace_id, id),
	FOREIGN KEY(namespace_id, generation_id) REFERENCES publication_candidates
        (namespace_id, generation_id),
	FOREIGN KEY(version_id) REFERENCES dataset_versions (id),
	FOREIGN KEY(candidate_id) REFERENCES publication_candidates (id),
	CONSTRAINT ck_process_progress CHECK (member_count BETWEEN 1 AND 1000
        AND completed_count BETWEEN 0 AND member_count)
)
    """)
    op.execute("""
CREATE TABLE process_outputs (
	operation_id UUID NOT NULL,
	sample_id UUID NOT NULL,
	step_position INTEGER NOT NULL,
	namespace_id UUID NOT NULL,
	sample_revision_id UUID NOT NULL,
	receipt_id UUID NOT NULL,
	accepted_epoch BIGINT NOT NULL,
	input_digest VARCHAR(64) NOT NULL,
	output_digest VARCHAR(64) NOT NULL,
	result JSONB NOT NULL,
	profile_id UUID,
	profile_digest VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (operation_id, sample_id, step_position),
	FOREIGN KEY(namespace_id, operation_id) REFERENCES operations (namespace_id, id),
	FOREIGN KEY(receipt_id) REFERENCES effect_receipts (id),
	FOREIGN KEY(profile_id) REFERENCES model_profiles (id),
	CONSTRAINT ck_process_output_position CHECK (step_position BETWEEN 0 AND 7
        AND accepted_epoch >= 1)
)
    """)
    op.execute("""
CREATE TABLE process_attempts (
	operation_id UUID NOT NULL,
	epoch BIGINT NOT NULL,
	namespace_id UUID NOT NULL,
	status VARCHAR(30) NOT NULL,
	error_code VARCHAR(100),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (operation_id, epoch),
	FOREIGN KEY(namespace_id, operation_id) REFERENCES operations (namespace_id, id)
)
    """)
    for table in ("model_profiles", "process_flows", "process_outputs"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION registry_reject_mutation()"
        )
    op.execute("""
      CREATE FUNCTION guard_process_run() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Process pins cannot be deleted' USING ERRCODE='23514';
        END IF;
        IF (to_jsonb(NEW) - 'cursor' - 'completed_count') IS DISTINCT FROM
           (to_jsonb(OLD) - 'cursor' - 'completed_count')
           OR NEW.completed_count < OLD.completed_count
           OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor < OLD.cursor)) THEN
          RAISE EXCEPTION 'Process pins are immutable and progress is monotonic'
            USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER process_run_guard BEFORE UPDATE OR DELETE ON process_runs
      FOR EACH ROW EXECUTE FUNCTION guard_process_run();
    """)


def downgrade():
    connection = op.get_bind()
    for table in ("process_runs", "process_flows", "model_profiles"):
        if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {table})")):
            raise RuntimeError("Rollback refused: retain/export process data before contraction")
    if connection.scalar(
        text("SELECT EXISTS(SELECT 1 FROM operations WHERE status IN ('pause_requested','paused'))")
    ):
        raise RuntimeError("Rollback refused: pause states still exist")
    for table in (
        "process_attempts",
        "process_outputs",
        "process_runs",
        "process_flows",
        "model_profiles",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_process_run()")
    op.drop_constraint("ck_operation_status", "operations", type_="check")
    op.create_check_constraint(
        "ck_operation_status",
        "operations",
        "status IN ('queued','running','cancellation_requested','cancelled',"
        "'failed','succeeded','reconciliation_needed')",
    )
