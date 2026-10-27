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
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    modality: Mapped[str] = mapped_column(String(50))
    revision: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
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


def make_sessions(database_url: str) -> sessionmaker[Session]:
    from sqlalchemy.engine import make_url

    if make_url(database_url).get_backend_name() != "postgresql":
        raise ValueError("The platform requires PostgreSQL")
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=0)
    if engine.dialect.name != "postgresql":
        raise ValueError("The platform requires PostgreSQL")
    return sessionmaker(engine, expire_on_commit=False)


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
