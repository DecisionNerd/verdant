"""Shared registry use cases. Each command owns one PostgreSQL transaction."""

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from verdant import contracts as c
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal, require_current_access
from verdant.persistence import (
    AuditRow,
    DatasetRow,
    DraftRow,
    MembershipRow,
    NamespaceGrantRow,
    NamespaceRow,
    RegistryRepository,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.telemetry import observed


class RegistryService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @observed("registry")
    def get_namespace(self, principal: Principal, namespace_id: UUID) -> c.Namespace:
        with self.sessions.begin() as session:
            actor = current_principal(session, principal.id)
            actor.require(namespace_id)
            row = session.get(NamespaceRow, namespace_id)
            if row is None:
                raise NotFound("Namespace not found")
            return c.Namespace(
                id=row.id,
                name=row.name,
                access="write" if namespace_id in actor.writable_namespaces else "read",
            )

    @observed("registry")
    def list_namespaces(
        self, principal: Principal, after: UUID | None = None, limit: int = 50
    ) -> c.NamespacePage:
        if not 1 <= limit <= 100:
            raise ValueError("Page size must be between 1 and 100")
        with self.sessions.begin() as session:
            current_principal(session, principal.id)
            query = (
                select(NamespaceRow, NamespaceGrantRow.access)
                .join(NamespaceGrantRow, NamespaceGrantRow.namespace_id == NamespaceRow.id)
                .where(NamespaceGrantRow.principal_id == principal.id)
                .order_by(NamespaceRow.id)
                .limit(limit + 1)
            )
            if after is not None:
                query = query.where(NamespaceRow.id > after)
            rows = session.execute(query).all()
            return c.NamespacePage(
                items=[
                    c.Namespace(id=row.id, name=row.name, access=access)
                    for row, access in rows[:limit]
                ],
                next_after=rows[limit - 1][0].id if len(rows) > limit else None,
            )

    @contextmanager
    def _transaction(self, principal: Principal, namespace_id: UUID, *, write: bool = False):
        try:
            with self.sessions.begin() as session:
                current_principal(session, principal.id).require(namespace_id, write=write)
                if session.get(NamespaceRow, namespace_id) is None:
                    raise NotFound("Namespace not found")
                yield RegistryRepository(session)
        except IntegrityError as exc:
            # Only expected user-facing uniqueness failures become conflicts. Other defects surface.
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in {"uq_dataset_slug", "uq_sample_source"}:
                raise Conflict("Identity already exists in this namespace") from None
            raise

    @staticmethod
    def _audit(
        repo: RegistryRepository,
        principal: Principal,
        namespace_id: UUID,
        aggregate_id: UUID,
        action: str,
        payload: dict,
    ) -> None:
        repo.session.add(
            AuditRow(
                namespace_id=namespace_id,
                actor_id=principal.id,
                aggregate_id=aggregate_id,
                action=action,
                schema_version=c.EVENT_VERSION,
                payload=payload | {"transport": principal.transport},
            )
        )

    @staticmethod
    def _revision(row: SampleRevisionRow) -> c.SampleRevision:
        return c.SampleRevision(
            id=row.id,
            namespace_id=row.namespace_id,
            sample_id=row.sample_id,
            revision=row.revision,
            sha256=row.sha256,
            object_key=row.object_key,
            size_bytes=row.size_bytes,
            metadata=row.metadata_json,
            created_at=row.created_at,
        )

    @staticmethod
    def _dataset(row: DatasetRow, draft_id: UUID) -> c.Dataset:
        return c.Dataset(
            id=row.id,
            namespace_id=row.namespace_id,
            owner_id=row.owner_id,
            revision=row.revision,
            availability=row.availability,
            slug=row.slug,
            name=row.name,
            modality=row.modality,
            draft_id=draft_id,
            created_at=row.created_at,
        )

    @observed("registry")
    def list_datasets(
        self,
        principal: Principal,
        namespace_id: UUID,
        search: str = "",
        modality: str | None = None,
        owner_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> c.DatasetPage:
        if not 1 <= limit <= 100 or len(search) > 200:
            raise ValueError("Invalid catalog page size or search length")
        with self._transaction(principal, namespace_id) as repo:
            filters = [DatasetRow.namespace_id == namespace_id]
            if not include_archived:
                filters.append(DatasetRow.availability == "active")
            if search:
                filters.append(
                    or_(
                        DatasetRow.name.icontains(search, autoescape=True),
                        DatasetRow.slug.icontains(search, autoescape=True),
                    )
                )
            if modality is not None:
                filters.append(DatasetRow.modality == modality)
            if owner_id is not None:
                filters.append(DatasetRow.owner_id == owner_id)
            # Authorization precedes both aggregate and row disclosure.
            total = repo.session.scalar(
                select(func.count()).select_from(DatasetRow).where(*filters)
            )
            query = (
                select(DatasetRow, DraftRow.id)
                .join(
                    DraftRow,
                    (DraftRow.dataset_id == DatasetRow.id)
                    & (DraftRow.namespace_id == DatasetRow.namespace_id),
                )
                .where(*filters)
                .order_by(DatasetRow.id)
                .limit(limit + 1)
            )
            if after is not None:
                query = query.where(DatasetRow.id > after)
            rows = repo.session.execute(query).all()
            return c.DatasetPage(
                items=[self._dataset(row, draft_id) for row, draft_id in rows[:limit]],
                total=total,
                next_after=rows[limit - 1][0].id if len(rows) > limit else None,
            )

    @observed("registry")
    def create_dataset(
        self, principal: Principal, namespace_id: UUID, command: c.DatasetCreate
    ) -> c.Dataset:
        with self._transaction(principal, namespace_id, write=True) as repo:
            row = DatasetRow(
                namespace_id=namespace_id, owner_id=principal.id, **command.model_dump()
            )
            repo.session.add(row)
            repo.session.flush()
            draft = DraftRow(namespace_id=namespace_id, dataset_id=row.id)
            repo.session.add(draft)
            repo.session.flush()
            self._audit(repo, principal, namespace_id, row.id, "dataset.created", {})
            result = self._dataset(row, draft.id)
        return result

    @observed("registry")
    def update_dataset(
        self,
        principal: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        command: c.DatasetUpdate,
    ) -> c.Dataset:
        with self._transaction(principal, namespace_id, write=True) as repo:
            row = repo.session.scalar(
                select(DatasetRow)
                .where(DatasetRow.namespace_id == namespace_id, DatasetRow.id == dataset_id)
                .with_for_update()
            )
            if row is None:
                raise NotFound("Dataset not found")
            require_current_access(repo.session, principal.id, namespace_id, write=True)
            if row.revision != command.expected_revision:
                raise Conflict("Dataset metadata changed; reload the latest revision before saving")
            if row.availability != "active":
                raise Conflict("Archived datasets pause metadata edits; unarchive first")
            if row.revision == 9223372036854775807:
                raise Conflict("Dataset metadata revision limit reached")
            for field in ("slug", "name", "modality"):
                setattr(row, field, getattr(command, field))
            row.revision += 1
            repo.session.flush()
            self._audit(
                repo, principal, namespace_id, row.id, "dataset.updated", {"revision": row.revision}
            )
            return self._dataset(row, repo.draft(namespace_id, dataset_id).id)

    @observed("registry")
    def get_dataset(self, principal: Principal, namespace_id: UUID, dataset_id: UUID) -> c.Dataset:
        with self._transaction(principal, namespace_id) as repo:
            row = repo.dataset(namespace_id, dataset_id)
            if row is None:
                raise NotFound("Dataset not found")
            draft = repo.draft(namespace_id, dataset_id)
            return self._dataset(row, draft.id)

    @observed("registry")
    def list_samples(
        self, principal: Principal, namespace_id: UUID, after: UUID | None = None, limit: int = 50
    ) -> c.SamplePage:
        if not 1 <= limit <= 100:
            raise ValueError("Page size must be between 1 and 100")
        with self._transaction(principal, namespace_id) as repo:
            query = (
                select(SampleRow, SampleRevisionRow)
                .join(
                    SampleRevisionRow,
                    (SampleRevisionRow.sample_id == SampleRow.id)
                    & (SampleRevisionRow.namespace_id == SampleRow.namespace_id)
                    & (SampleRevisionRow.revision == SampleRow.revision),
                )
                .where(SampleRow.namespace_id == namespace_id)
                .order_by(SampleRow.id)
                .limit(limit + 1)
            )
            if after is not None:
                query = query.where(SampleRow.id > after)
            rows = repo.session.execute(query).all()
            return c.SamplePage(
                items=[
                    c.SampleSummary(
                        sample_id=sample.id,
                        revision_id=revision.id,
                        revision=revision.revision,
                        modality=sample.modality,
                        sha256=revision.sha256,
                        size_bytes=revision.size_bytes,
                    )
                    for sample, revision in rows[:limit]
                ],
                next_after=rows[limit - 1][0].id if len(rows) > limit else None,
            )

    @observed("registry")
    def create_sample(
        self, principal: Principal, namespace_id: UUID, command: c.SampleCreate
    ) -> c.SampleRevision:
        with self._transaction(principal, namespace_id, write=True) as repo:
            sample = SampleRow(
                namespace_id=namespace_id, source_key=command.source_key, modality=command.modality
            )
            repo.session.add(sample)
            repo.session.flush()
            row = self._add_revision(repo, sample, command)
            self._audit(
                repo,
                principal,
                namespace_id,
                sample.id,
                "sample.created",
                {"revision_id": str(row.id), "revision": row.revision},
            )
            result = self._revision(row)
        return result

    @staticmethod
    def _add_revision(
        repo: RegistryRepository, sample: SampleRow, content: c.SampleContent
    ) -> SampleRevisionRow:
        row = SampleRevisionRow(
            namespace_id=sample.namespace_id,
            sample_id=sample.id,
            revision=sample.revision,
            sha256=content.sha256,
            object_key=content.object_key,
            size_bytes=content.size_bytes,
            metadata_json=content.metadata,
        )
        repo.session.add(row)
        repo.session.flush()
        return row

    @observed("registry")
    def revise_sample(
        self, principal: Principal, namespace_id: UUID, sample_id: UUID, command: c.SampleRevise
    ) -> c.SampleRevision:
        with self._transaction(principal, namespace_id, write=True) as repo:
            sample = repo.session.scalar(
                select(SampleRow)
                .where(SampleRow.namespace_id == namespace_id, SampleRow.id == sample_id)
                .with_for_update()
            )
            if sample is None:
                raise NotFound("Sample not found")
            if sample.revision != command.expected_revision:
                raise Conflict("Sample revision changed")
            sample.revision += 1
            row = self._add_revision(repo, sample, command)
            # Metadata-only edits retain existing verified ownership. Matching arbitrary keys
            # elsewhere never establish a binding for an unverified sample.
            prior_artifact = repo.session.scalar(
                select(SampleArtifactRow.artifact_id)
                .join(SampleRevisionRow, SampleRevisionRow.id == SampleArtifactRow.revision_id)
                .where(
                    SampleArtifactRow.namespace_id == namespace_id,
                    SampleArtifactRow.sample_id == sample_id,
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.revision == command.expected_revision,
                    SampleRevisionRow.object_key == command.object_key,
                    SampleRevisionRow.sha256 == command.sha256,
                    SampleRevisionRow.size_bytes == command.size_bytes,
                )
            )
            if prior_artifact is not None:
                repo.session.add(
                    SampleArtifactRow(
                        namespace_id=namespace_id,
                        sample_id=sample_id,
                        revision_id=row.id,
                        artifact_id=prior_artifact,
                    )
                )
            self._audit(
                repo,
                principal,
                namespace_id,
                sample.id,
                "sample.revised",
                {"revision_id": str(row.id), "revision": row.revision},
            )
            result = self._revision(row)
        return result

    @observed("registry")
    def inspect_sample_revision(
        self, principal: Principal, namespace_id: UUID, sample_id: UUID, revision_id: UUID
    ) -> c.SampleSummary:
        with self._transaction(principal, namespace_id) as repo:
            row = repo.session.execute(
                select(SampleRow, SampleRevisionRow)
                .join(
                    SampleRevisionRow,
                    (SampleRevisionRow.sample_id == SampleRow.id)
                    & (SampleRevisionRow.namespace_id == SampleRow.namespace_id),
                )
                .where(
                    SampleRow.namespace_id == namespace_id,
                    SampleRow.id == sample_id,
                    SampleRevisionRow.id == revision_id,
                )
            ).first()
            if row is None:
                raise NotFound("Sample revision not found")
            sample, revision = row
            return c.SampleSummary(
                sample_id=sample.id,
                revision_id=revision.id,
                revision=revision.revision,
                modality=sample.modality,
                sha256=revision.sha256,
                size_bytes=revision.size_bytes,
            )

    @observed("registry")
    def get_sample_revision(
        self, principal: Principal, namespace_id: UUID, sample_id: UUID, revision_id: UUID
    ) -> c.SampleRevision:
        with self._transaction(principal, namespace_id) as repo:
            row = repo.session.scalar(
                select(SampleRevisionRow).where(
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.id == revision_id,
                )
            )
            if row is None:
                raise NotFound("Sample revision not found")
            return self._revision(row)

    @observed("registry")
    def set_membership(
        self,
        principal: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        sample_id: UUID,
        command: c.MembershipWrite,
    ) -> c.DraftChange:
        with self._transaction(principal, namespace_id, write=True) as repo:
            draft = self._locked_draft(
                repo, namespace_id, dataset_id, command.expected_revision, principal
            )
            revision = repo.session.scalar(
                select(SampleRevisionRow).where(
                    SampleRevisionRow.namespace_id == namespace_id,
                    SampleRevisionRow.sample_id == sample_id,
                    SampleRevisionRow.id == command.sample_revision_id,
                )
            )
            if revision is None:
                raise NotFound("Sample revision not found")
            current = repo.session.scalar(
                select(MembershipRow).where(
                    MembershipRow.draft_id == draft.id,
                    MembershipRow.sample_id == sample_id,
                    MembershipRow.valid_to.is_(None),
                )
            )
            if command.interpretation is not None:
                interpretation = command.interpretation.model_dump(by_alias=True)
            elif current is not None:
                if current.sample_revision_id != revision.id and any(
                    current.interpretation.get(name) is not None
                    for name in (
                        "annotation_revision",
                        "task_schema_revision",
                        "review_evidence",
                        "transformation",
                    )
                ):
                    raise Conflict(
                        "Changing the sample revision requires explicit replacement "
                        "or clearing of interpretation evidence"
                    )
                interpretation = current.interpretation
            else:
                interpretation = c.InterpretationPins().model_dump(by_alias=True)
            if current is not None:
                if (current.sample_revision_id, current.split, current.interpretation) == (
                    revision.id,
                    command.split,
                    interpretation,
                ):
                    return c.DraftChange(draft_id=draft.id, revision=draft.revision)
                current.valid_to = draft.revision + 1
                # Close the current interval before inserting a new current row.
                repo.session.flush()
            draft.revision += 1
            repo.session.add(
                MembershipRow(
                    namespace_id=namespace_id,
                    draft_id=draft.id,
                    sample_id=sample_id,
                    sample_revision_id=revision.id,
                    split=command.split,
                    interpretation=interpretation,
                    valid_from=draft.revision,
                )
            )
            self._audit(
                repo,
                principal,
                namespace_id,
                dataset_id,
                "draft.membership_set",
                {
                    "sample_id": str(sample_id),
                    "revision": draft.revision,
                    "interpretation_schema": interpretation["schema"],
                },
            )
            result = c.DraftChange(draft_id=draft.id, revision=draft.revision)
        return result

    @staticmethod
    def _locked_draft(
        repo: RegistryRepository,
        namespace_id: UUID,
        dataset_id: UUID,
        expected: int,
        principal: Principal,
    ) -> DraftRow:
        dataset = repo.session.scalar(
            select(DatasetRow)
            .where(DatasetRow.namespace_id == namespace_id, DatasetRow.id == dataset_id)
            .with_for_update()
        )
        if dataset is None:
            raise NotFound("Dataset not found")
        draft = repo.draft(namespace_id, dataset_id, lock=True)
        if draft is None:
            raise NotFound("Dataset not found")
        require_current_access(repo.session, principal.id, namespace_id, write=True)
        if dataset.availability != "active":
            raise Conflict("Archived datasets pause draft edits; checkpoints are retained")
        if draft.revision != expected:
            raise Conflict("Draft revision changed")
        return draft

    @observed("registry")
    def remove_membership(
        self,
        principal: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        sample_id: UUID,
        expected_revision: int,
    ) -> c.DraftChange:
        with self._transaction(principal, namespace_id, write=True) as repo:
            draft = self._locked_draft(repo, namespace_id, dataset_id, expected_revision, principal)
            current = repo.session.scalar(
                select(MembershipRow).where(
                    MembershipRow.draft_id == draft.id,
                    MembershipRow.sample_id == sample_id,
                    MembershipRow.valid_to.is_(None),
                )
            )
            if current is None:
                raise NotFound("Draft member not found")
            draft.revision += 1
            current.valid_to = draft.revision
            self._audit(
                repo,
                principal,
                namespace_id,
                dataset_id,
                "draft.membership_removed",
                {"sample_id": str(sample_id), "revision": draft.revision},
            )
            result = c.DraftChange(draft_id=draft.id, revision=draft.revision)
        return result

    @observed("registry")
    def list_memberships(
        self,
        principal: Principal,
        namespace_id: UUID,
        dataset_id: UUID,
        *,
        revision: int | None = None,
        after: UUID | None = None,
        limit: int = 100,
    ) -> c.DraftPage:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._transaction(principal, namespace_id) as repo:
            draft = repo.draft(namespace_id, dataset_id)
            if draft is None:
                raise NotFound("Dataset not found")
            revision = draft.revision if revision is None else revision
            if not 0 <= revision <= draft.revision:
                raise NotFound("Draft revision not found")
            rows = repo.membership_page(draft, revision, after, limit + 1)
            members = [
                c.Membership(
                    sample_id=row.sample_id,
                    sample_revision_id=row.sample_revision_id,
                    split=row.split,
                    interpretation=c.InterpretationPins.model_validate(row.interpretation),
                )
                for row in rows[:limit]
            ]
            return c.DraftPage(
                draft_id=draft.id,
                revision=revision,
                members=members,
                next_after=members[-1].sample_id if len(rows) > limit else None,
            )
