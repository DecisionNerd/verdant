"""Shared registry use cases. Each command owns one PostgreSQL transaction."""

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from verdant import contracts as c
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal
from verdant.persistence import (
    AuditRow,
    DatasetRow,
    DraftRow,
    MembershipRow,
    NamespaceRow,
    RegistryRepository,
    SampleRevisionRow,
    SampleRow,
)
from verdant.telemetry import observed


class RegistryService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

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
            result = c.Dataset(
                id=row.id,
                namespace_id=namespace_id,
                owner_id=row.owner_id,
                slug=row.slug,
                name=row.name,
                modality=row.modality,
                draft_id=draft.id,
                created_at=row.created_at,
            )
        return result

    @observed("registry")
    def get_dataset(self, principal: Principal, namespace_id: UUID, dataset_id: UUID) -> c.Dataset:
        with self._transaction(principal, namespace_id) as repo:
            row = repo.dataset(namespace_id, dataset_id)
            if row is None:
                raise NotFound("Dataset not found")
            draft = repo.draft(namespace_id, dataset_id)
            return c.Dataset(
                id=row.id,
                namespace_id=namespace_id,
                owner_id=row.owner_id,
                slug=row.slug,
                name=row.name,
                modality=row.modality,
                draft_id=draft.id,
                created_at=row.created_at,
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
            draft = self._locked_draft(repo, namespace_id, dataset_id, command.expected_revision)
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
            if current is not None:
                if (current.sample_revision_id, current.split) == (revision.id, command.split):
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
                    valid_from=draft.revision,
                )
            )
            self._audit(
                repo,
                principal,
                namespace_id,
                dataset_id,
                "draft.membership_set",
                {"sample_id": str(sample_id), "revision": draft.revision},
            )
            result = c.DraftChange(draft_id=draft.id, revision=draft.revision)
        return result

    @staticmethod
    def _locked_draft(
        repo: RegistryRepository, namespace_id: UUID, dataset_id: UUID, expected: int
    ) -> DraftRow:
        draft = repo.draft(namespace_id, dataset_id, lock=True)
        if draft is None:
            raise NotFound("Dataset not found")
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
            draft = self._locked_draft(repo, namespace_id, dataset_id, expected_revision)
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
                )
                for row in rows[:limit]
            ]
            return c.DraftPage(
                draft_id=draft.id,
                revision=revision,
                members=members,
                next_after=members[-1].sample_id if len(rows) > limit else None,
            )
