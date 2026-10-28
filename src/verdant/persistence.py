"""PostgreSQL mapping and scoped repository operations. Repositories never commit."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    create_engine,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class NamespaceRow(Base):
    __tablename__ = "namespaces"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class PrincipalRow(Base):
    __tablename__ = "principals"
    __table_args__ = (CheckConstraint("kind IN ('human', 'service')", name="ck_principal_kind"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    session_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalIdentityRow(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_subject"),
        ForeignKeyConstraint(["principal_id"], ["principals.id"]),
        CheckConstraint("length(issuer) > 0 AND length(subject) > 0", name="ck_external_subject"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    issuer: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[UUID] = mapped_column(unique=True)
    email: Mapped[str | None] = mapped_column(String(320))


class NamespaceGrantRow(Base):
    __tablename__ = "namespace_grants"
    __table_args__ = (
        ForeignKeyConstraint(["principal_id"], ["principals.id"]),
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        CheckConstraint("access IN ('read', 'write')", name="ck_namespace_access"),
    )
    principal_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID] = mapped_column(primary_key=True)
    access: Mapped[str] = mapped_column(String(10))


class BrowserSessionRow(Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginAttemptRow(Base):
    __tablename__ = "login_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class DatasetRow(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_dataset_namespace_id"),
        UniqueConstraint("namespace_id", "slug", name="uq_dataset_slug"),
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        CheckConstraint("length(name) > 0 AND length(modality) > 0", name="ck_dataset_values"),
        CheckConstraint("revision >= 0", name="ck_dataset_revision"),
        CheckConstraint("availability IN ('active', 'archived')", name="ck_dataset_availability"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    modality: Mapped[str] = mapped_column(String(50))
    revision: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    availability: Mapped[str] = mapped_column(String(20), server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DraftRow(Base):
    __tablename__ = "dataset_drafts"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_draft_namespace_id"),
        UniqueConstraint("namespace_id", "dataset_id", name="uq_dataset_draft"),
        ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        CheckConstraint("revision >= 0", name="ck_draft_revision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    dataset_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger, default=0)


class CandidateRow(Base):
    __tablename__ = "publication_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "draft_id"], ["dataset_drafts.namespace_id", "dataset_drafts.id"]
        ),
        ForeignKeyConstraint(["creator_id"], ["principals.id"]),
        UniqueConstraint("namespace_id", "creator_id", "request_key", name="uq_candidate_request"),
        UniqueConstraint("namespace_id", "generation_id", name="uq_candidate_generation"),
        CheckConstraint(
            "source_revision >= 0 AND prepared_count >= 0", name="ck_candidate_progress"
        ),
        CheckConstraint("state IN ('preparing', 'sealed')", name="ck_candidate_state"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_candidate_request_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    draft_id: Mapped[UUID]
    source_revision: Mapped[int] = mapped_column(BigInteger)
    generation_id: Mapped[UUID]
    creator_id: Mapped[UUID]
    request_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), server_default="preparing")
    prepared_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cursor: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QualityPolicyRow(Base):
    __tablename__ = "quality_policies"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_quality_policy_namespace"),
        UniqueConstraint(
            "namespace_id", "dataset_id", "name", "revision", name="uq_quality_policy_revision"
        ),
        UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_quality_policy_request"
        ),
        ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        ForeignKeyConstraint(["creator_id"], ["principals.id"]),
        CheckConstraint("revision >= 1 AND length(name) > 0", name="ck_quality_policy_revision"),
        CheckConstraint("jsonb_typeof(definition) = 'object'", name="ck_quality_policy_definition"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    dataset_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(BigInteger)
    definition: Mapped[dict] = mapped_column(JSONB)
    creator_id: Mapped[UUID]
    request_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QualityReportRow(Base):
    __tablename__ = "quality_reports"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "creator_id", "request_key", name="uq_quality_report_request"
        ),
        ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        ForeignKeyConstraint(
            ["namespace_id", "policy_id"], ["quality_policies.namespace_id", "quality_policies.id"]
        ),
        ForeignKeyConstraint(["candidate_id"], ["publication_candidates.id"]),
        ForeignKeyConstraint(["creator_id"], ["principals.id"]),
        CheckConstraint("state IN ('running', 'complete')", name="ck_quality_report_state"),
        CheckConstraint(
            "0 <= processed_count AND processed_count <= expected_count",
            name="ck_quality_report_progress",
        ),
        CheckConstraint(
            "state <> 'complete' OR processed_count = expected_count",
            name="ck_quality_report_complete",
        ),
        CheckConstraint("jsonb_typeof(results) = 'array'", name="ck_quality_report_results"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    dataset_id: Mapped[UUID]
    candidate_id: Mapped[UUID]
    generation_id: Mapped[UUID]
    source_revision: Mapped[int] = mapped_column(BigInteger)
    policy_id: Mapped[UUID]
    policy_revision: Mapped[int] = mapped_column(BigInteger)
    evaluator_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), server_default="running")
    expected_count: Mapped[int] = mapped_column(BigInteger)
    processed_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cursor: Mapped[UUID | None]
    results: Mapped[list] = mapped_column(JSONB)
    creator_id: Mapped[UUID]
    request_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationMemberRow(Base):
    __tablename__ = "generation_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        CheckConstraint("split ~ '^[a-z][a-z0-9_-]*$'", name="ck_generation_split"),
        CheckConstraint(
            "jsonb_typeof(interpretation) = 'object'", name="ck_generation_interpretation"
        ),
    )
    generation_id: Mapped[UUID] = mapped_column(primary_key=True)
    sample_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    sample_revision_id: Mapped[UUID]
    split: Mapped[str] = mapped_column(String(50))
    interpretation: Mapped[dict] = mapped_column(JSONB)


class SampleRow(Base):
    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_sample_namespace_id"),
        UniqueConstraint("namespace_id", "source_key", name="uq_sample_source"),
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        CheckConstraint("revision >= 1", name="ck_sample_revision"),
        CheckConstraint("length(source_key) > 0 AND length(modality) > 0", name="ck_sample_values"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    source_key: Mapped[str] = mapped_column(String(500))
    modality: Mapped[str] = mapped_column(String(50))
    revision: Mapped[int] = mapped_column(BigInteger, default=1)


class SampleRevisionRow(Base):
    __tablename__ = "sample_revisions"
    __table_args__ = (
        UniqueConstraint("namespace_id", "sample_id", "id", name="uq_sample_revision_identity"),
        UniqueConstraint("namespace_id", "sample_id", "revision", name="uq_sample_revision_number"),
        ForeignKeyConstraint(["namespace_id", "sample_id"], ["samples.namespace_id", "samples.id"]),
        CheckConstraint("revision >= 1 AND size_bytes >= 0", name="ck_revision_values"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_digest"),
        CheckConstraint("length(object_key) > 0", name="ck_revision_object"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_revision_metadata"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    sample_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MembershipRow(Base):
    """Half-open draft revision interval [valid_from, valid_to); NULL end means current."""

    __tablename__ = "draft_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "draft_id"], ["dataset_drafts.namespace_id", "dataset_drafts.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        CheckConstraint(
            "valid_from >= 1 AND (valid_to IS NULL OR valid_to > valid_from)",
            name="ck_membership_interval",
        ),
        CheckConstraint("split ~ '^[a-z][a-z0-9_-]*$'", name="ck_membership_split"),
        CheckConstraint(
            "jsonb_typeof(interpretation) = 'object'", name="ck_membership_interpretation"
        ),
        ExcludeConstraint(
            ("draft_id", "="),
            ("sample_id", "="),
            (text("int8range(valid_from, valid_to, '[)')"), "&&"),
            name="ex_membership_intervals",
            using="gist",
        ),
        Index(
            "uq_current_membership",
            "draft_id",
            "sample_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
    draft_id: Mapped[UUID] = mapped_column(primary_key=True)
    sample_id: Mapped[UUID] = mapped_column(primary_key=True)
    valid_from: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace_id: Mapped[UUID]
    sample_revision_id: Mapped[UUID]
    split: Mapped[str] = mapped_column(String(50))
    valid_to: Mapped[int | None] = mapped_column(BigInteger)
    interpretation: Mapped[dict] = mapped_column(
        JSONB,
        server_default=text(
            """jsonb_build_object('schema', 'verdant.membership.v1',
            'annotation_revision', NULL, 'task_schema_revision', NULL,
            'review_evidence', NULL, 'transformation', NULL)"""
        ),
    )


class AuditRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        CheckConstraint("schema_version = 1", name="ck_audit_version"),
        CheckConstraint(
            "namespace_id IS NOT NULL OR action LIKE 'identity.%'", name="ck_audit_scope"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID | None]
    actor_id: Mapped[UUID]
    aggregate_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OperationRow(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_operation_namespace_id"),
        UniqueConstraint("namespace_id", "creator_id", "request_key", name="uq_operation_request"),
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        ForeignKeyConstraint(["creator_id"], ["principals.id"]),
        CheckConstraint(
            "status IN ('queued','running','cancellation_requested','cancelled',"
            "'failed','succeeded','reconciliation_needed')",
            name="ck_operation_status",
        ),
        CheckConstraint("epoch >= 1 AND revision >= 0", name="ck_operation_revision"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_operation_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    creator_id: Mapped[UUID]
    request_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(100))
    inputs: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), server_default="queued")
    epoch: Mapped[int] = mapped_column(BigInteger, server_default="1")
    revision: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationEventRow(Base):
    __tablename__ = "operation_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
        UniqueConstraint("operation_id", "revision", name="uq_operation_event_revision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    operation_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxRow(Base):
    __tablename__ = "operation_outbox"
    __table_args__ = (ForeignKeyConstraint(["event_id"], ["operation_events.id"]),)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    dispatched: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    claim_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EffectReceiptRow(Base):
    __tablename__ = "effect_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
        UniqueConstraint("operation_id", "effect_key", name="uq_logical_effect"),
        CheckConstraint("status IN ('prepared','unknown','succeeded')", name="ck_effect_status"),
        CheckConstraint("revision >= 0", name="ck_effect_revision"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="ck_effect_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    operation_id: Mapped[UUID]
    effect_key: Mapped[str] = mapped_column(String(64))
    identity: Mapped[dict] = mapped_column(JSONB)
    request_digest: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default="prepared")
    revision: Mapped[int] = mapped_column(BigInteger, server_default="0")
    dispatch_id: Mapped[UUID | None]
    dispatch_epoch: Mapped[int | None] = mapped_column(BigInteger)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    provider_reference: Mapped[str | None] = mapped_column(String(1000))
    reconciliation_evidence: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerificationResultRow(Base):
    __tablename__ = "operation_verification_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "sample_id", "sample_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
    )
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    sample_id: Mapped[UUID]
    sample_revision_id: Mapped[UUID]
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    epoch: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsumerInboxRow(Base):
    __tablename__ = "consumer_inbox"
    __table_args__ = (
        ForeignKeyConstraint(["event_id"], ["operation_events.id"]),
        CheckConstraint(
            "outcome IN ('applied','stale','ignored','quarantined')", name="ck_inbox_outcome"
        ),
    )
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    outcome: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsumerCheckpointRow(Base):
    __tablename__ = "consumer_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
    )
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)


class WorkflowDispatchRow(Base):
    __tablename__ = "workflow_dispatches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
        ForeignKeyConstraint(["event_id"], ["operation_events.id"]),
        UniqueConstraint("invocation_key", name="uq_workflow_invocation"),
        UniqueConstraint("event_id", name="uq_workflow_dispatch_event"),
        CheckConstraint(
            "status IN ('pending','unknown','submitted')", name="ck_workflow_dispatch_status"
        ),
    )
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace_id: Mapped[UUID]
    event_id: Mapped[UUID]
    invocation_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    deployment_id: Mapped[UUID | None]
    provider_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def make_sessions(database_url: str) -> sessionmaker[Session]:
    from sqlalchemy.engine import make_url

    if make_url(database_url).get_backend_name() != "postgresql":
        raise ValueError("The platform requires PostgreSQL")
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=0)
    if engine.dialect.name != "postgresql":
        raise ValueError("The platform requires PostgreSQL")
    return sessionmaker(engine, expire_on_commit=False)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_artifact_namespace_id"),
        ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifact_digest"),
        CheckConstraint("size_bytes BETWEEN 0 AND 268435456", name="ck_artifact_size"),
        CheckConstraint(
            "collection_state IN ('active', 'deleting', 'deleted')", name="ck_artifact_collection"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_state: Mapped[str] = mapped_column(String(20), server_default="active")


class ArtifactLocationRow(Base):
    __tablename__ = "artifact_locations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        UniqueConstraint("store_id", "bucket", "object_key", name="uq_artifact_placement"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    artifact_id: Mapped[UUID]
    store_id: Mapped[str] = mapped_column(String(100))
    bucket: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(1024))


class IngestionItemRow(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "operation_id"], ["operations.namespace_id", "operations.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "target_sample_id"], ["samples.namespace_id", "samples.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "sample_id", "accepted_revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        UniqueConstraint("operation_id", "position", name="uq_ingestion_position"),
        UniqueConstraint("staging_key", name="uq_ingestion_staging"),
        Index("ix_ingestion_artifact_protection", "namespace_id", "artifact_id", "state"),
        CheckConstraint("position BETWEEN 0 AND 99", name="ck_ingestion_position"),
        CheckConstraint(
            "state IN ('awaiting_upload','uploading','ready','accepted','failed','cancelled')",
            name="ck_ingestion_state",
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_ingestion_metadata"),
        CheckConstraint(
            "(target_sample_id IS NULL AND expected_revision IS NULL) OR "
            "(target_sample_id IS NOT NULL AND expected_revision IS NOT NULL "
            "AND expected_revision >= 1)",
            name="ck_ingestion_target",
        ),
        CheckConstraint(
            "(state = 'accepted' AND sample_id IS NOT NULL "
            "AND accepted_revision_id IS NOT NULL) OR "
            "(state <> 'accepted' AND sample_id IS NULL AND accepted_revision_id IS NULL)",
            name="ck_ingestion_acceptance",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    operation_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[UUID]
    source_key: Mapped[str] = mapped_column(String(500))
    modality: Mapped[str] = mapped_column(String(50))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB)
    target_sample_id: Mapped[UUID | None]
    sample_id: Mapped[UUID | None]
    expected_revision: Mapped[int | None] = mapped_column(BigInteger)
    staging_key: Mapped[str] = mapped_column(String(1024))
    upload_id: Mapped[str | None] = mapped_column(String(2048))
    state: Mapped[str] = mapped_column(String(30), server_default="awaiting_upload")
    error: Mapped[str | None] = mapped_column(String(100))
    accepted_revision_id: Mapped[UUID | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SampleArtifactRow(Base):
    __tablename__ = "sample_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "sample_id", "revision_id"],
            ["sample_revisions.namespace_id", "sample_revisions.sample_id", "sample_revisions.id"],
        ),
        ForeignKeyConstraint(
            ["namespace_id", "artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        Index("ix_sample_artifact_protection", "namespace_id", "artifact_id"),
    )
    revision_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    sample_id: Mapped[UUID]
    artifact_id: Mapped[UUID]


class PublicationGroupRow(Base):
    __tablename__ = "publication_groups"
    __table_args__ = (
        UniqueConstraint("namespace_id", "id", name="uq_publication_group_namespace"),
        ForeignKeyConstraint(["namespace_id", "id"], ["operations.namespace_id", "operations.id"]),
        ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        ForeignKeyConstraint(["candidate_id"], ["publication_candidates.id"]),
        ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        ForeignKeyConstraint(["report_id"], ["quality_reports.id"]),
        ForeignKeyConstraint(
            ["namespace_id", "manifest_artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        CheckConstraint(
            "state IN ('building','prepared','published','releasing','released')",
            name="ck_publication_group_state",
        ),
        CheckConstraint(
            "0 <= prepared_count AND prepared_count <= expected_count AND epoch >= 0",
            name="ck_publication_group_progress",
        ),
        CheckConstraint(
            "(manifest_artifact_id IS NULL AND manifest_digest IS NULL AND manifest_size IS NULL)"
            " OR (manifest_artifact_id IS NOT NULL AND manifest_digest IS NOT NULL"
            " AND manifest_size IS NOT NULL AND manifest_digest ~ '^[0-9a-f]{64}$'"
            " AND manifest_size BETWEEN 0 AND 268435456)",
            name="ck_publication_group_manifest",
        ),
        Index("ix_publication_group_target", "namespace_id", "dataset_id", "candidate_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    dataset_id: Mapped[UUID]
    candidate_id: Mapped[UUID]
    generation_id: Mapped[UUID]
    report_id: Mapped[UUID]
    expected_count: Mapped[int] = mapped_column(BigInteger)
    prepared_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cursor: Mapped[UUID | None]
    state: Mapped[str] = mapped_column(String(20), server_default="building")
    epoch: Mapped[int] = mapped_column(BigInteger, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp() + interval '24 hours'")
    )
    manifest_artifact_id: Mapped[UUID | None]
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    manifest_size: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetentionReferenceRow(Base):
    __tablename__ = "retention_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace_id", "group_id"],
            ["publication_groups.namespace_id", "publication_groups.id"],
        ),
        ForeignKeyConstraint(
            ["namespace_id", "artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        Index("ix_retention_artifact_group", "artifact_id", "group_id"),
    )
    group_id: Mapped[UUID] = mapped_column(primary_key=True)
    artifact_id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("namespace_id", "dataset_id", "label", name="uq_dataset_version_label"),
        UniqueConstraint("group_id", name="uq_dataset_version_group"),
        ForeignKeyConstraint(
            ["namespace_id", "dataset_id"], ["datasets.namespace_id", "datasets.id"]
        ),
        ForeignKeyConstraint(
            ["namespace_id", "group_id"],
            ["publication_groups.namespace_id", "publication_groups.id"],
        ),
        ForeignKeyConstraint(["candidate_id"], ["publication_candidates.id"]),
        ForeignKeyConstraint(["report_id"], ["quality_reports.id"]),
        ForeignKeyConstraint(
            ["namespace_id", "generation_id"],
            ["publication_candidates.namespace_id", "publication_candidates.generation_id"],
        ),
        ForeignKeyConstraint(
            ["namespace_id", "manifest_artifact_id"], ["artifacts.namespace_id", "artifacts.id"]
        ),
        CheckConstraint("length(btrim(label)) > 0", name="ck_dataset_version_label"),
        CheckConstraint("manifest_digest ~ '^[0-9a-f]{64}$'", name="ck_dataset_version_digest"),
        CheckConstraint("manifest_size BETWEEN 0 AND 268435456", name="ck_dataset_version_size"),
        CheckConstraint(
            "availability IN ('active','deprecated','archived')"
            " AND prior_availability IN ('active','deprecated')"
            " AND (availability = 'archived' OR availability = prior_availability)"
            " AND availability_revision >= 0",
            name="ck_dataset_version_availability",
        ),
        Index("ix_dataset_version_page", "namespace_id", "dataset_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    dataset_id: Mapped[UUID]
    label: Mapped[str] = mapped_column(String(100))
    generation_id: Mapped[UUID]
    candidate_id: Mapped[UUID]
    report_id: Mapped[UUID]
    group_id: Mapped[UUID]
    manifest_artifact_id: Mapped[UUID]
    manifest_digest: Mapped[str] = mapped_column(String(64))
    manifest_size: Mapped[int] = mapped_column(BigInteger)
    availability: Mapped[str] = mapped_column(String(20), server_default="active")
    prior_availability: Mapped[str] = mapped_column(String(20), server_default="active")
    availability_revision: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RegistryRepository:
    def __init__(self, session: Session):
        self.session = session

    def dataset(self, namespace_id: UUID, dataset_id: UUID) -> DatasetRow | None:
        return self.session.scalar(
            select(DatasetRow).where(
                DatasetRow.namespace_id == namespace_id, DatasetRow.id == dataset_id
            )
        )

    def draft(self, namespace_id: UUID, dataset_id: UUID, *, lock: bool = False) -> DraftRow | None:
        statement = select(DraftRow).where(
            DraftRow.namespace_id == namespace_id, DraftRow.dataset_id == dataset_id
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def membership_page(
        self, draft: DraftRow, revision: int, after: UUID | None, limit: int
    ) -> list[MembershipRow]:
        statement = (
            select(MembershipRow)
            .where(
                MembershipRow.namespace_id == draft.namespace_id,
                MembershipRow.draft_id == draft.id,
                MembershipRow.valid_from <= revision,
                or_(MembershipRow.valid_to.is_(None), MembershipRow.valid_to > revision),
            )
            .order_by(MembershipRow.sample_id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(MembershipRow.sample_id > after)
        return list(self.session.scalars(statement))
