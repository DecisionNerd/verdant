"""Native provenance, immutable originals, categorical agreement and canonical review.

All public calls authorize the current namespace before reading rows/counts. Private
operator admission consumes independently verified native IDs, never email matches.
"""

import hashlib
import json
from itertools import combinations
from uuid import UUID, uuid4

from sqlalchemy import exists, select, text
from sqlalchemy.orm import aliased

from verdant import annotation_contracts as a
from verdant.application import RegistryService
from verdant.contracts import InlineEvidence
from verdant.domain import SYSTEM_ACTOR_ID, Conflict, Forbidden, NotFound
from verdant.identity import current_principal, require_current_access
from verdant.label_studio import LabelStudioConflict, LabelStudioSettings, LabelStudioUnavailable
from verdant.persistence import (
    AnnotationAssignmentRow,
    AnnotationCohortRow,
    AnnotationOriginalRow,
    AnnotationReportRow,
    AuditRow,
    CanonicalAnnotationRow,
    DatasetRow,
    ExternalIdentityRow,
    NativeContributorRow,
    PrincipalRow,
    RegistryRepository,
    SampleRevisionRow,
)
from verdant.telemetry import observed


def audit(session, actor, namespace, aggregate, action, payload):
    session.add(
        AuditRow(
            namespace_id=namespace,
            actor_id=actor.id if actor else SYSTEM_ACTOR_ID,
            aggregate_id=aggregate,
            action=action,
            schema_version=1,
            payload=payload | {"transport": actor.transport if actor else "operator"},
        )
    )


def authorize_dataset(session, actor, ns, dataset_id, *, write=False):
    current_principal(session, actor.id).require(ns, write=write)
    query = select(DatasetRow).where(DatasetRow.namespace_id == ns, DatasetRow.id == dataset_id)
    if write:
        query = query.with_for_update()
    dataset = session.scalar(query)
    if dataset is None:
        raise NotFound("Dataset not found")
    require_current_access(session, actor.id, ns, write=write)
    if write and dataset.availability != "active":
        raise Conflict("Archived datasets pause new annotation and truth changes")
    return dataset


def evidence(row):
    return a.Evidence(id=row.id, digest=row.digest, payload=row.payload, created_at=row.created_at)


def idempotent(session, cls, ns, actor_id, key, digest, dataset_id=None):
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"annotation-request:{cls.__tablename__}:{ns}:{actor_id}:{key}"},
    )
    principal_column = cls.reviewer_id if cls is CanonicalAnnotationRow else cls.creator_id
    row = session.scalar(
        select(cls).where(
            cls.namespace_id == ns,
            principal_column == actor_id,
            cls.request_key == key,
        )
    )
    if row and (row.request_digest != digest or (dataset_id and row.dataset_id != dataset_id)):
        raise Conflict("Request key was already used with different input")
    return row


def assignment_row(session, ns, dataset_id, assignment_id):
    row = session.scalar(
        select(AnnotationAssignmentRow).where(
            AnnotationAssignmentRow.id == assignment_id,
            AnnotationAssignmentRow.namespace_id == ns,
            AnnotationAssignmentRow.dataset_id == dataset_id,
        )
    )
    if row is None:
        raise NotFound("Annotation assignment not found")
    return row


def originals(session, assignment):
    rows = session.scalars(
        select(AnnotationOriginalRow)
        .where(
            AnnotationOriginalRow.assignment_id == assignment.id,
        )
        .order_by(AnnotationOriginalRow.sequence)
        .limit(1001)
    ).all()
    if len(rows) > 1000:
        raise Conflict("Assignment original history exceeds the supported 1000 revisions")
    return rows


def latest_originals(rows):
    latest = {}
    for row in rows:
        latest[row.native_annotation_id] = row
    return list(latest.values())


def original_view(row):
    return a.Original(**{name: getattr(row, name) for name in a.Original.model_fields})


def normalize(annotation, schema):
    label = None
    assistance = "unknown"
    seen = set()
    for result in annotation.result:
        name = result.get("from_name")
        value = result.get("value")
        if (
            not isinstance(name, str)
            or name not in {"label", "assistance"}
            or name in seen
            or result.get("type") != "choices"
            or result.get("to_name") != "text"
            or not isinstance(value, dict)
            or set(value) != {"choices"}
            or not isinstance(value["choices"], list)
            or len(value["choices"]) != 1
        ):
            raise Conflict("Only the pinned single categorical text result is supported")
        choice = value["choices"][0]
        if not isinstance(choice, str):
            raise Conflict("Native categorical choices must be strings")
        if name == "label":
            if choice not in schema.labels:
                raise Conflict("Native category is outside the pinned schema")
            label = choice
        else:
            if choice not in {"independent", "assisted", "unknown"}:
                raise Conflict("Unknown assistance provenance")
            assistance = choice
        seen.add(name)
    if len(json.dumps(annotation.model_dump(mode="json"), ensure_ascii=False).encode()) > 65536:
        raise Conflict("Native categorical annotation exceeds the supported 64 KiB")
    return {
        "native": annotation.model_dump(mode="json"),
        "label": label,
        "assistance": assistance,
        "state": "cancelled"
        if annotation.was_cancelled
        else "abstained"
        if label is None
        else "submitted",
    }


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    scale = 1 + z * z / total
    center = (p + z * z / (2 * total)) / scale
    radius = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / scale
    return [max(0, center - radius), min(1, center + radius)]


def agreement_snapshot(assignments, snapshot):
    labels = assignments[0].task_schema["labels"]
    participants = sorted({str(p) for row in assignments for p in row.contributors})
    pairs = []
    for left, right in combinations(participants, 2):
        confusion = {x: {y: 0 for y in labels} for x in labels}
        overlap = agreed = independent_overlap = 0
        left_counts = dict.fromkeys(labels, 0)
        right_counts = dict.fromkeys(labels, 0)
        for item in snapshot:
            left_value = item["contributors"].get(left)
            right_value = item["contributors"].get(right)
            if (
                not left_value
                or not right_value
                or left_value["state"] != "submitted"
                or right_value["state"] != "submitted"
            ):
                continue
            overlap += 1
            agreed += left_value["label"] == right_value["label"]
            independent_overlap += (
                left_value["assistance"] == right_value["assistance"] == "independent"
            )
            confusion[left_value["label"]][right_value["label"]] += 1
            left_counts[left_value["label"]] += 1
            right_counts[right_value["label"]] += 1
        observed = agreed / overlap if overlap else None
        expected = (
            sum(left_counts[x] * right_counts[x] for x in labels) / overlap**2 if overlap else None
        )
        undefined = (
            "no overlap"
            if not overlap
            else "constant identical marginals"
            if expected == 1
            else None
        )
        pairs.append(
            {
                "left": left,
                "right": right,
                "overlap": overlap,
                "agreed": agreed,
                "declared_independent_overlap": independent_overlap,
                "observed_agreement": observed,
                "agreement_95_wilson": wilson(agreed, overlap),
                "cohen_kappa": None if undefined else (observed - expected) / (1 - expected),
                "kappa_undefined_reason": undefined,
                "confusion": confusion,
                "prevalence_left": left_counts,
                "prevalence_right": right_counts,
            }
        )
    return pairs


class AnnotationService:
    def __init__(self, sessions, client=None):
        self.sessions = sessions
        self.client = client

    @observed("annotations")
    def admit_cohort(self, ns, *, name, settings, issuer, reason):
        """Private trusted operator boundary; never expose as a workspace API."""
        if not name.strip() or not issuer.startswith("https://") or not reason.strip():
            raise ValueError("Cohort admission requires name, HTTPS issuer and operator reason")
        with self.sessions.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "annotation-cohort:" + settings.browser_origin},
            )
            row = session.scalar(
                select(AnnotationCohortRow).where(
                    AnnotationCohortRow.browser_origin == settings.browser_origin,
                )
            )
            values = dict(
                namespace_id=ns,
                name=name,
                browser_origin=settings.browser_origin,
                machine_origin=settings.machine_origin,
                image=settings.image,
                issuer=issuer,
            )
            if row:
                if any(getattr(row, key) != value for key, value in values.items()):
                    raise Conflict("A native instance cannot be shared across namespace cohorts")
                return row.id
            row = AnnotationCohortRow(**values, reason=reason)
            session.add(row)
            session.flush()
            audit(
                session,
                None,
                ns,
                row.id,
                "annotation.cohort_admitted",
                {"image": settings.image, "reason": reason},
            )
            return row.id

    @observed("annotations")
    def bind_contributor(
        self, cohort_id, *, issuer, subject, native_user_id, reviewer=False, reason
    ):
        from verdant.label_studio import positive_id

        positive_id(native_user_id)
        if not reason.strip():
            raise ValueError("Verified native mapping requires an operator reason")
        with self.sessions.begin() as session:
            cohort = session.scalar(
                select(AnnotationCohortRow)
                .where(
                    AnnotationCohortRow.id == cohort_id,
                )
                .with_for_update()
            )
            if not cohort or issuer != cohort.issuer:
                raise Conflict("Contributor issuer differs from the admitted cohort")
            identity = session.scalar(
                select(ExternalIdentityRow).where(
                    ExternalIdentityRow.issuer == issuer,
                    ExternalIdentityRow.subject == subject,
                )
            )
            if not identity or session.get(PrincipalRow, identity.principal_id).kind != "human":
                raise Conflict("Provision the exact human issuer/subject before native admission")
            current_principal(session, identity.principal_id).require(cohort.namespace_id)
            existing = session.scalars(
                select(NativeContributorRow)
                .where(
                    NativeContributorRow.cohort_id == cohort_id,
                )
                .limit(101)
            ).all()
            for row in existing:
                if (
                    row.native_user_id == native_user_id
                    or row.principal_id == identity.principal_id
                ):
                    if (
                        row.native_user_id,
                        row.principal_id,
                        row.issuer,
                        row.subject,
                        row.reviewer,
                    ) != (
                        native_user_id,
                        identity.principal_id,
                        issuer,
                        subject,
                        reviewer,
                    ):
                        raise Conflict("Native contributor provenance is immutable")
                    return row.id
            if len(existing) >= 100:
                raise Conflict("Cohort admission supports at most 100 contributors")
            row = NativeContributorRow(
                cohort_id=cohort_id,
                principal_id=identity.principal_id,
                native_user_id=native_user_id,
                issuer=issuer,
                subject=subject,
                reviewer=reviewer,
                reason=reason,
            )
            session.add(row)
            session.flush()
            audit(
                session,
                None,
                cohort.namespace_id,
                row.id,
                "annotation.contributor_bound",
                {
                    "principal_id": str(identity.principal_id),
                    "native_user_id": native_user_id,
                    "reviewer": reviewer,
                    "reason": reason,
                },
            )
            return row.id

    def _cohort_client(self, cohort):
        if not self.client:
            raise LabelStudioUnavailable("External labeling integration is not configured")
        settings = self.client.settings
        if (cohort.browser_origin, cohort.machine_origin, cohort.image) != (
            settings.browser_origin,
            settings.machine_origin,
            settings.image,
        ):
            raise LabelStudioUnavailable(
                "The configured native instance is not this admitted cohort"
            )
        return self.client

    @observed("annotations")
    def status(self, actor, ns):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(ns)
            rows = session.scalars(
                select(AnnotationCohortRow)
                .where(
                    AnnotationCohortRow.namespace_id == ns,
                )
                .order_by(AnnotationCohortRow.id)
                .limit(51)
            ).all()
            if len(rows) > 50:
                raise Conflict("Cohort listing exceeds the supported 50 instances")
            cohorts = []
            for row in rows:
                contributors = session.scalars(
                    select(NativeContributorRow)
                    .where(
                        NativeContributorRow.cohort_id == row.id,
                    )
                    .order_by(NativeContributorRow.id)
                    .limit(101)
                ).all()
                if len(contributors) > 100:
                    raise Conflict("Cohort contributors exceed the supported 100 members")
                cohorts.append(
                    {
                        "id": str(row.id),
                        "name": row.name,
                        "origin": row.browser_origin,
                        "image": row.image,
                        "contributors": [
                            {
                                "principal_id": str(c.principal_id),
                                "native_user_id": c.native_user_id,
                                "reviewer": c.reviewer,
                            }
                            for c in contributors
                        ],
                    }
                )
            configured = bool(
                self.client
                and any(
                    (row.browser_origin, row.machine_origin, row.image)
                    == (
                        self.client.settings.browser_origin,
                        self.client.settings.machine_origin,
                        self.client.settings.image,
                    )
                    for row in rows
                )
            )
            return a.IntegrationStatus(
                available=configured,
                reason="Admitted shared-trust integration"
                if configured
                else (
                    "External labeling unavailable: configure the pinned instance "
                    "and operator cohort admission."
                ),
                cohorts=cohorts,
            )

    def _view(self, row, cohort):
        return a.Assignment(
            **{
                name: getattr(row, name)
                for name in a.Assignment.model_fields
                if name != "editor_url"
            },
            editor_url=LabelStudioSettings(
                cohort.browser_origin, cohort.machine_origin, cohort.image
            ).task_url(row.native_project_id, row.native_task_id),
        )

    @observed("annotations")
    def create(self, actor, ns, dataset_id, command):
        request_digest = a.digest(command.model_dump(mode="json"))
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id, write=True)
            prior = idempotent(
                session,
                AnnotationAssignmentRow,
                ns,
                actor.id,
                command.request_key,
                request_digest,
                dataset_id,
            )
            if prior:
                return self._view(prior, session.get(AnnotationCohortRow, prior.cohort_id))
            cohort = session.scalar(
                select(AnnotationCohortRow).where(
                    AnnotationCohortRow.id == command.cohort_id,
                    AnnotationCohortRow.namespace_id == ns,
                )
            )
            if cohort is None:
                raise NotFound("Admitted annotation cohort not found")
            client = self._cohort_client(cohort)
            repo = RegistryRepository(session)
            draft = RegistryService._locked_draft(
                repo, ns, dataset_id, command.expected_revision, actor
            )
            members = repo.membership_page(draft, draft.revision, None, 501)
            if len(members) > 500:
                raise Conflict("Annotation assignment supports datasets up to 500 members")
            if not any(
                m.sample_id == command.sample_id
                and m.sample_revision_id == command.sample_revision_id
                for m in members
            ):
                raise Conflict("Assignment must pin a current exact dataset member")
            source = session.get(SampleRevisionRow, command.sample_revision_id)
            admitted = session.scalars(
                select(NativeContributorRow).where(
                    NativeContributorRow.cohort_id == cohort.id,
                    NativeContributorRow.principal_id.in_(command.contributors),
                )
            ).all()
            if len(admitted) != len(command.contributors):
                raise Conflict(
                    "All assigned humans need immutable operator-admitted native provenance"
                )
            for person in admitted:
                require_current_access(session, person.principal_id, ns)
            native_schema = client.project_schema(command.native_project_id)
            if native_schema != command.task_schema.native_xml():
                raise LabelStudioConflict(
                    "Native schema/instructions differ from the exact categorical task"
                )
            self._verify_data(
                client, command.native_project_id, command.native_task_id, source.id, source.sha256
            )
            if client.project_schema(command.native_project_id) != native_schema:
                raise LabelStudioConflict("Native schema changed during assignment")
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"native-task:{cohort.id}:{command.native_task_id}"},
            )
            existing = session.scalar(
                select(AnnotationAssignmentRow).where(
                    AnnotationAssignmentRow.cohort_id == cohort.id,
                    AnnotationAssignmentRow.native_task_id == command.native_task_id,
                )
            )
            if existing:
                raise Conflict("Native task already has an immutable assignment")
            row = AnnotationAssignmentRow(
                namespace_id=ns,
                dataset_id=dataset_id,
                cohort_id=cohort.id,
                sample_id=command.sample_id,
                sample_revision_id=source.id,
                source_revision=draft.revision,
                native_project_id=command.native_project_id,
                native_task_id=command.native_task_id,
                task_schema=command.task_schema.model_dump(mode="json"),
                schema_digest=a.digest(command.task_schema.model_dump(mode="json")),
                native_schema_digest=hashlib.sha256(native_schema.encode()).hexdigest(),
                content_digest=source.sha256,
                contributors=[str(p) for p in command.contributors],
                creator_id=actor.id,
                request_key=command.request_key,
                request_digest=request_digest,
            )
            session.add(row)
            session.flush()
            audit(
                session,
                actor,
                ns,
                row.id,
                "annotation.assignment_created",
                {
                    "dataset_id": str(dataset_id),
                    "source_revision": draft.revision,
                    "schema_digest": row.schema_digest,
                },
            )
            return self._view(row, cohort)

    @staticmethod
    def _verify_data(client, project, task, revision, digest):
        data = client.task_data(project, task)
        content = data.get("text")
        if (
            set(data) != {"text", "verdant_revision_id"}
            or not isinstance(content, str)
            or data["verdant_revision_id"] != str(revision)
            or len(content.encode()) > 65536
            or hashlib.sha256(content.encode()).hexdigest() != digest
        ):
            raise LabelStudioConflict(
                "Native task content/source revision differs from the assignment"
            )

    @observed("annotations")
    def list(self, actor, ns, dataset_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid annotation page size")
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            query = (
                select(AnnotationAssignmentRow)
                .where(
                    AnnotationAssignmentRow.namespace_id == ns,
                    AnnotationAssignmentRow.dataset_id == dataset_id,
                )
                .order_by(AnnotationAssignmentRow.id)
                .limit(limit + 1)
            )
            if after:
                query = query.where(AnnotationAssignmentRow.id > after)
            rows = session.scalars(query).all()
            return {
                "items": [
                    self._view(row, session.get(AnnotationCohortRow, row.cohort_id))
                    for row in rows[:limit]
                ],
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
            }

    def _detail(self, session, assignment):
        rows = originals(session, assignment)
        latest = latest_originals(rows)
        canonical = self._canonical(session, assignment)
        return a.AssignmentDetail(
            assignment=self._view(
                assignment, session.get(AnnotationCohortRow, assignment.cohort_id)
            ),
            originals=[original_view(row) for row in rows],
            latest=[original_view(row) for row in latest],
            missing_contributors=[
                UUID(p)
                for p in assignment.contributors
                if not any(
                    str(row.principal_id) == p and row.payload["state"] != "cancelled"
                    for row in latest
                )
            ],
            canonical_id=canonical.id if canonical else None,
        )

    @observed("annotations")
    def detail(self, actor, ns, dataset_id, assignment_id):
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            return self._detail(session, assignment_row(session, ns, dataset_id, assignment_id))

    @observed("annotations")
    def import_native(self, actor, ns, dataset_id, assignment_id, command):
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id, write=True)
            assignment = assignment_row(session, ns, dataset_id, assignment_id)
            cohort = session.get(AnnotationCohortRow, assignment.cohort_id)
            client = self._cohort_client(cohort)
            # Serialize all imports of this immutable task; row locks do not mutate it.
            session.refresh(assignment, with_for_update=True)
            self._verify_data(
                client,
                assignment.native_project_id,
                assignment.native_task_id,
                assignment.sample_revision_id,
                assignment.content_digest,
            )
            native = client.task_annotations(
                assignment.native_project_id,
                assignment.native_task_id,
                assignment.native_schema_digest,
            )
            self._verify_data(
                client,
                assignment.native_project_id,
                assignment.native_task_id,
                assignment.sample_revision_id,
                assignment.content_digest,
            )
            stored = originals(session, assignment)
            latest = {row.native_annotation_id: row for row in latest_originals(stored)}
            bindings = session.scalars(
                select(NativeContributorRow)
                .where(
                    NativeContributorRow.cohort_id == cohort.id,
                )
                .limit(101)
            ).all()
            pending = []
            active_authors = set()
            for annotation in native:
                binding = next(
                    (b for b in bindings if b.native_user_id == annotation.completed_by), None
                )
                if binding is None or str(binding.principal_id) not in assignment.contributors:
                    raise Conflict(
                        "Native annotation contributor is not assigned with verified provenance"
                    )
                require_current_access(session, binding.principal_id, ns)
                old = latest.get(annotation.id)
                if old and old.principal_id != binding.principal_id:
                    raise Conflict("Native annotation contributor changed")
                payload = normalize(
                    annotation, a.CategoricalSchema.model_validate(assignment.task_schema)
                )
                modifier = next(
                    (b for b in bindings if b.native_user_id == annotation.updated_by), None
                )
                if annotation.updated_by is not None and (
                    modifier is None or str(modifier.principal_id) not in assignment.contributors
                ):
                    raise Conflict("Native modifier lacks verified assigned contributor provenance")
                payload["declared_assistance"] = payload["assistance"]
                payload["independence"] = (
                    "self_declared"
                    if annotation.updated_by == annotation.completed_by
                    else "modifier_unknown"
                    if annotation.updated_by is None
                    else "cross_author_edit"
                )
                if payload["independence"] != "self_declared":
                    payload["assistance"] = "unknown"
                payload["modifier"] = (
                    {
                        "principal_id": str(modifier.principal_id),
                        "issuer": modifier.issuer,
                        "subject": modifier.subject,
                        "native_user_id": modifier.native_user_id,
                    }
                    if modifier
                    else None
                )
                payload |= {
                    "issuer": binding.issuer,
                    "subject": binding.subject,
                    "schema_digest": assignment.schema_digest,
                }
                if payload["state"] != "cancelled":
                    if binding.principal_id in active_authors:
                        raise Conflict(
                            "Multiple active native originals from one contributor are ambiguous"
                        )
                    active_authors.add(binding.principal_id)
                digest = a.digest(payload)
                if old is None or old.digest != digest:
                    pending.append(
                        (
                            annotation.id,
                            binding.principal_id,
                            annotation.completed_by,
                            payload,
                            digest,
                        )
                    )
            native_ids = {row.id for row in native}
            for native_id, old in latest.items():
                if native_id not in native_ids and old.payload["state"] != "cancelled":
                    payload = old.payload | {"state": "cancelled", "native_deleted": True}
                    pending.append(
                        (
                            native_id,
                            old.principal_id,
                            old.native_user_id,
                            payload,
                            a.digest(payload),
                        )
                    )
            if pending and len(stored) != command.expected_original_count:
                raise Conflict(
                    "Original history changed; inspect current revisions before importing"
                )
            if len(stored) + len(pending) > 1000:
                raise Conflict("Original history exceeds 1000 revisions")
            for index, (native_id, principal, native_user, payload, digest) in enumerate(
                pending, len(stored) + 1
            ):
                session.add(
                    AnnotationOriginalRow(
                        assignment_id=assignment.id,
                        principal_id=principal,
                        native_annotation_id=native_id,
                        native_user_id=native_user,
                        sequence=index,
                        payload=payload,
                        digest=digest,
                    )
                )
            if pending:
                audit(
                    session,
                    actor,
                    ns,
                    assignment.id,
                    "annotation.native_imported",
                    {
                        "dataset_id": str(dataset_id),
                        "new_original_count": len(pending),
                        "source_revision": assignment.source_revision,
                    },
                )
            session.flush()
            return self._detail(session, assignment)

    @staticmethod
    def _canonical(session, assignment):
        successor = aliased(CanonicalAnnotationRow)
        return session.scalar(
            select(CanonicalAnnotationRow).where(
                CanonicalAnnotationRow.namespace_id == assignment.namespace_id,
                CanonicalAnnotationRow.dataset_id == assignment.dataset_id,
                CanonicalAnnotationRow.sample_revision_id == assignment.sample_revision_id,
                ~exists(
                    select(successor.id).where(successor.prior_id == CanonicalAnnotationRow.id)
                ),
            )
        )

    @staticmethod
    def _snapshot(session, assignments):
        snapshot = []
        for assignment in assignments:
            latest = latest_originals(originals(session, assignment))
            contributors = {}
            for principal in assignment.contributors:
                active = [
                    row
                    for row in latest
                    if str(row.principal_id) == principal and row.payload["state"] != "cancelled"
                ]
                if len(active) > 1:
                    raise Conflict("Contributor originals are ambiguous")
                if active:
                    row = active[0]
                    contributors[principal] = {
                        "original_id": str(row.id),
                        "digest": row.digest,
                        "native_annotation_id": row.native_annotation_id,
                        "native_user_id": row.native_user_id,
                        "issuer": row.payload["issuer"],
                        "subject": row.payload["subject"],
                        "label": row.payload["label"],
                        "state": row.payload["state"],
                        "assistance": row.payload["assistance"],
                    }
                else:
                    contributors[principal] = {
                        "state": "missing",
                        "label": None,
                        "assistance": "unknown",
                    }
            snapshot.append(
                {
                    "assignment_id": str(assignment.id),
                    "sample_id": str(assignment.sample_id),
                    "sample_revision_id": str(assignment.sample_revision_id),
                    "source_revision": assignment.source_revision,
                    "schema_digest": assignment.schema_digest,
                    "contributors": contributors,
                    "original_history": [
                        {"id": str(row.id), "digest": row.digest}
                        for row in originals(session, assignment)
                    ],
                }
            )
        return snapshot

    @observed("annotations")
    def report(self, actor, ns, dataset_id, command):
        digest = a.digest(command.model_dump(mode="json"))
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id, write=True)
            prior = idempotent(
                session, AnnotationReportRow, ns, actor.id, command.request_key, digest, dataset_id
            )
            if prior:
                return evidence(prior)
            assignments = [
                assignment_row(session, ns, dataset_id, id) for id in sorted(command.assignment_ids)
            ]
            if (
                len({row.schema_digest for row in assignments}) != 1
                or len({row.cohort_id for row in assignments}) != 1
            ):
                raise Conflict(
                    "Agreement requires one exact task/schema/instruction and trust cohort"
                )
            if len({row.sample_revision_id for row in assignments}) != len(assignments):
                raise Conflict("Agreement units must be distinct sample revisions")
            if len({p for row in assignments for p in row.contributors}) > 10:
                raise Conflict("Agreement supports at most 10 distinct contributors per report")
            snapshot = self._snapshot(session, assignments)
            disagreements = []
            counts = {
                state: 0 for state in ("submitted", "abstained", "missing", "assisted", "unknown")
            }
            for item in snapshot:
                values = item["contributors"].values()
                for value in values:
                    counts[value["state"]] += 1
                    if value["assistance"] in {"assisted", "unknown"}:
                        counts[value["assistance"]] += 1
                submitted = [value["label"] for value in values if value["state"] == "submitted"]
                if len(set(submitted)) > 1 or len(submitted) != len(item["contributors"]):
                    disagreements.append(item["assignment_id"])
            payload = {
                "format": "verdant.agreement.v1",
                "task_schema": assignments[0].task_schema,
                "schema_digest": assignments[0].schema_digest,
                "cohort_id": str(assignments[0].cohort_id),
                "units": snapshot,
                "counts": counts,
                "pairs": agreement_snapshot(assignments, snapshot),
                "disagreement_queue": disagreements,
                "limits": (
                    "Agreement is not correctness. Shared-trust users can see each other's work; "
                    "independence is self-declared, blindness unverified. Wilson intervals "
                    "treat task pairs as independent; correlated tasks require grouped "
                    "inference. Kappa uncertainty is not estimated."
                ),
            }
            row = AnnotationReportRow(
                namespace_id=ns,
                dataset_id=dataset_id,
                creator_id=actor.id,
                request_key=command.request_key,
                request_digest=digest,
                payload=payload,
                digest=a.digest(payload),
            )
            session.add(row)
            session.flush()
            audit(
                session,
                actor,
                ns,
                row.id,
                "annotation.agreement_created",
                {"digest": row.digest, "unit_count": len(snapshot)},
            )
            return evidence(row)

    @observed("annotations")
    def get_evidence(self, actor, ns, dataset_id, evidence_id, *, canonical=False):
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            cls = CanonicalAnnotationRow if canonical else AnnotationReportRow
            row = session.scalar(
                select(cls).where(
                    cls.namespace_id == ns, cls.dataset_id == dataset_id, cls.id == evidence_id
                )
            )
            if row is None:
                raise NotFound("Annotation evidence not found")
            return evidence(row)

    @observed("annotations")
    def adjudicate(self, actor, ns, dataset_id, command):
        digest = a.digest(command.model_dump(mode="json"))
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id, write=True)
            assignment = assignment_row(session, ns, dataset_id, command.assignment_id)
            reviewer = session.scalar(
                select(NativeContributorRow).where(
                    NativeContributorRow.cohort_id == assignment.cohort_id,
                    NativeContributorRow.principal_id == actor.id,
                    NativeContributorRow.reviewer.is_(True),
                )
            )
            if reviewer is None:
                raise Forbidden(
                    "Canonical adjudication requires operator-admitted reviewer authority"
                )
            prior = idempotent(
                session,
                CanonicalAnnotationRow,
                ns,
                actor.id,
                command.request_key,
                digest,
                dataset_id,
            )
            if prior:
                return evidence(prior)
            report = session.scalar(
                select(AnnotationReportRow).where(
                    AnnotationReportRow.id == command.report_id,
                    AnnotationReportRow.namespace_id == ns,
                    AnnotationReportRow.dataset_id == dataset_id,
                )
            )
            if report is None:
                raise NotFound("Agreement report not found")
            unit = next(
                (
                    unit
                    for unit in report.payload["units"]
                    if unit["assignment_id"] == str(assignment.id)
                ),
                None,
            )
            if unit is None or unit != self._snapshot(session, [assignment])[0]:
                raise Conflict(
                    "Report originals are stale; create a new report before adjudication"
                )
            current = self._canonical(session, assignment)
            if (current.id if current else None) != command.expected_canonical_id:
                raise Conflict("Canonical truth changed; inspect the current review")
            if command.label is not None and command.label not in assignment.task_schema["labels"]:
                raise Conflict("Canonical category is outside the exact task schema")
            canonical_id = uuid4()
            annotation = InlineEvidence(
                format="verdant.canonical-annotation.v1",
                payload={
                    "id": str(canonical_id),
                    "sample_revision_id": str(assignment.sample_revision_id),
                    "schema_digest": assignment.schema_digest,
                    "label": command.label,
                    "decision": "accepted" if command.label is not None else "abstained",
                    "reviewer_id": str(actor.id),
                    "rationale": command.rationale,
                    "report_id": str(report.id),
                    "report_digest": report.digest,
                    "originals": [
                        {"id": v["original_id"], "digest": v["digest"]}
                        for v in unit["contributors"].values()
                        if "original_id" in v
                    ],
                    "prior_id": str(current.id) if current else None,
                },
            )
            payload = {
                "annotation": annotation.model_dump(mode="json"),
                "task_schema": InlineEvidence(
                    format="verdant.categorical-schema.v1", payload=assignment.task_schema
                ).model_dump(mode="json"),
                "review": {
                    "annotation_digest": annotation.sha256,
                    "decision": annotation.payload["decision"],
                    "rationale": command.rationale,
                },
                "unresolved": command.label is None,
            }
            row = CanonicalAnnotationRow(
                id=canonical_id,
                namespace_id=ns,
                dataset_id=dataset_id,
                sample_id=assignment.sample_id,
                sample_revision_id=assignment.sample_revision_id,
                report_id=report.id,
                prior_id=current.id if current else None,
                reviewer_id=actor.id,
                request_key=command.request_key,
                request_digest=digest,
                payload=payload,
                digest=a.digest(payload),
            )
            session.add(row)
            session.flush()
            audit(
                session,
                actor,
                ns,
                row.id,
                "annotation.adjudicated",
                {
                    "digest": row.digest,
                    "report_id": str(report.id),
                    "unresolved": command.label is None,
                },
            )
            return evidence(row)
