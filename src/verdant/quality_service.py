"""Authorized policy revisions and resumable checks of sealed candidate membership."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from verdant.application import RegistryService
from verdant.contracts import Contract, Revision
from verdant.domain import Conflict, NotFound, Principal
from verdant.identity import current_principal, require_current_access
from verdant.persistence import (
    CandidateRow,
    DatasetRow,
    DraftRow,
    GenerationMemberRow,
    QualityPolicyRow,
    QualityReportRow,
    RegistryRepository,
    SampleRevisionRow,
)
from verdant.quality import (
    EVALUATOR_VERSION,
    OffendingSample,
    QualityDefinition,
    QualityMember,
    evaluate,
    summarize,
)
from verdant.telemetry import observed


class PolicyCreate(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    expected_revision: Revision
    definition: QualityDefinition


class Policy(Contract):
    id: UUID
    name: str
    revision: int
    definition: QualityDefinition
    created_at: datetime


class PolicyPage(Contract):
    items: list[Policy]
    next_after: UUID | None = None


class ValidationStart(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    policy_id: UUID


class ValidationBatch(Contract):
    expected_count: Revision
    limit: int = Field(default=100, ge=1, le=500, strict=True)


class SampleEvidence(Contract):
    sample_id: UUID
    sample_revision_id: UUID


class RuleEvidence(Contract):
    rule_id: str
    required: bool
    split: str | None
    status: Literal["pending", "passed", "failed", "unknown"]
    examined: int
    failures: int
    offending_samples: list[SampleEvidence]
    offending_samples_truncated: bool


class Report(Contract):
    id: UUID
    candidate_id: UUID
    generation_id: UUID
    source_revision: int
    policy_id: UUID
    policy_revision: int
    evaluator_version: str
    state: Literal["running", "complete"]
    expected_count: int
    processed_count: int
    required_rules_pass: bool | None
    results: list[RuleEvidence]
    created_at: datetime


class ReportPage(Contract):
    items: list[Report]
    next_after: UUID | None = None


def request_digest(dataset_id, command, candidate_id=None):
    payload = command.model_dump(mode="json", exclude={"request_key"}) | {
        "dataset_id": str(dataset_id),
        "candidate_id": str(candidate_id) if candidate_id else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def results_json(evaluation):
    # dataclasses contain UUID identities; Pydantic owns their JSON representation.
    return [
        RuleEvidence.model_validate(asdict(result)).model_dump(mode="json")
        for result in evaluation.results
    ]


class QualityService:
    def __init__(self, sessions):
        self.sessions = sessions

    @contextmanager
    def _transaction(self, actor, namespace_id, dataset_id, *, write=False):
        try:
            with self.sessions.begin() as session:
                current_principal(session, actor.id).require(namespace_id, write=write)
                dataset = session.scalar(
                    select(DatasetRow)
                    .where(DatasetRow.id == dataset_id, DatasetRow.namespace_id == namespace_id)
                    .with_for_update(read=not write)
                )
                if dataset is None:
                    raise NotFound("Dataset not found")
                if write:
                    require_current_access(session, actor.id, namespace_id, write=True)
                yield session
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in {"uq_quality_policy_request", "uq_quality_report_request"}:
                raise Conflict("Request identity has different inputs") from None
            raise

    @staticmethod
    def _policy(row):
        return Policy.model_validate({name: getattr(row, name) for name in Policy.model_fields})

    @staticmethod
    def _report(row):
        results = [RuleEvidence.model_validate(item) for item in row.results]
        if row.state == "running":
            results = [item.model_copy(update={"status": "pending"}) for item in results]
        required_pass = (
            None
            if row.state != "complete"
            else all(item.status == "passed" for item in results if item.required)
        )
        return Report.model_validate(
            {
                name: getattr(row, name)
                for name in Report.model_fields
                if name not in {"results", "required_rules_pass"}
            }
            | {"results": results, "required_rules_pass": required_pass}
        )

    @staticmethod
    def _audit(session, actor, namespace_id, aggregate_id, action, payload):
        RegistryService._audit(
            RegistryRepository(session), actor, namespace_id, aggregate_id, action, payload
        )

    @observed("quality")
    def create_policy(
        self, actor: Principal, namespace_id: UUID, dataset_id: UUID, command: PolicyCreate
    ) -> Policy:
        digest = request_digest(dataset_id, command)
        with self._transaction(actor, namespace_id, dataset_id, write=True) as session:
            prior = session.scalar(
                select(QualityPolicyRow).where(
                    QualityPolicyRow.namespace_id == namespace_id,
                    QualityPolicyRow.creator_id == actor.id,
                    QualityPolicyRow.request_key == command.request_key,
                )
            )
            if prior is not None:
                if prior.request_digest != digest:
                    raise Conflict("Policy request identity has different inputs")
                return self._policy(prior)
            if session.get(DatasetRow, dataset_id).availability != "active":
                raise Conflict("Archived datasets pause new quality policies")
            latest = (
                session.scalar(
                    select(func.max(QualityPolicyRow.revision)).where(
                        QualityPolicyRow.namespace_id == namespace_id,
                        QualityPolicyRow.dataset_id == dataset_id,
                        QualityPolicyRow.name == command.name,
                    )
                )
                or 0
            )
            if latest != command.expected_revision:
                raise Conflict(
                    f"Policy {command.name} is at revision {latest}; "
                    "reload before authoring a new revision"
                )
            row = QualityPolicyRow(
                namespace_id=namespace_id,
                dataset_id=dataset_id,
                creator_id=actor.id,
                request_key=command.request_key,
                request_digest=digest,
                name=command.name,
                revision=latest + 1,
                definition=command.definition.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            self._audit(
                session,
                actor,
                namespace_id,
                row.id,
                "quality.policy_created",
                {"dataset_id": str(dataset_id), "revision": row.revision},
            )
            return self._policy(row)

    @observed("quality")
    def list_policies(self, actor, namespace_id, dataset_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Policy page must be between 1 and 100")
        with self._transaction(actor, namespace_id, dataset_id) as session:
            query = select(QualityPolicyRow).where(
                QualityPolicyRow.namespace_id == namespace_id,
                QualityPolicyRow.dataset_id == dataset_id,
            )
            if after is not None:
                query = query.where(QualityPolicyRow.id > after)
            rows = session.scalars(query.order_by(QualityPolicyRow.id).limit(limit + 1)).all()
            return PolicyPage(
                items=[self._policy(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @observed("quality")
    def get_policy(self, actor, namespace_id, dataset_id, policy_id):
        with self._transaction(actor, namespace_id, dataset_id) as session:
            return self._policy(self._policy_row(session, namespace_id, dataset_id, policy_id))

    @staticmethod
    def _policy_row(session, namespace_id, dataset_id, policy_id):
        row = session.scalar(
            select(QualityPolicyRow).where(
                QualityPolicyRow.id == policy_id,
                QualityPolicyRow.namespace_id == namespace_id,
                QualityPolicyRow.dataset_id == dataset_id,
            )
        )
        if row is None:
            raise NotFound("Policy revision not found")
        return row

    @observed("quality")
    def start(self, actor, namespace_id, dataset_id, candidate_id, command: ValidationStart):
        digest = request_digest(dataset_id, command, candidate_id)
        with self._transaction(actor, namespace_id, dataset_id, write=True) as session:
            prior = session.scalar(
                select(QualityReportRow).where(
                    QualityReportRow.namespace_id == namespace_id,
                    QualityReportRow.creator_id == actor.id,
                    QualityReportRow.request_key == command.request_key,
                )
            )
            if prior is not None:
                if prior.request_digest != digest:
                    raise Conflict("Validation request identity has different inputs")
                return self._report(prior)
            if session.get(DatasetRow, dataset_id).availability != "active":
                raise Conflict("Archived datasets pause new validation admission")
            candidate = self._candidate(session, namespace_id, dataset_id, candidate_id)
            if candidate.state != "sealed":
                raise Conflict("Finish candidate preparation before validation")
            policy = self._policy_row(session, namespace_id, dataset_id, command.policy_id)
            definition = QualityDefinition.model_validate(policy.definition)
            initial = evaluate(definition, [], expected_member_count=0)
            row = QualityReportRow(
                namespace_id=namespace_id,
                dataset_id=dataset_id,
                candidate_id=candidate.id,
                generation_id=candidate.generation_id,
                source_revision=candidate.source_revision,
                policy_id=policy.id,
                policy_revision=policy.revision,
                evaluator_version=EVALUATOR_VERSION,
                expected_count=candidate.prepared_count,
                results=results_json(initial),
                creator_id=actor.id,
                request_key=command.request_key,
                request_digest=digest,
            )
            session.add(row)
            session.flush()
            self._audit(
                session,
                actor,
                namespace_id,
                row.id,
                "quality.validation_started",
                {
                    "dataset_id": str(dataset_id),
                    "candidate_id": str(candidate.id),
                    "policy_id": str(policy.id),
                    "generation_id": str(candidate.generation_id),
                },
            )
            return self._report(row)

    @staticmethod
    def _candidate(session, namespace_id, dataset_id, candidate_id):
        row = session.scalar(
            select(CandidateRow)
            .join(DraftRow, DraftRow.id == CandidateRow.draft_id)
            .where(
                CandidateRow.namespace_id == namespace_id,
                CandidateRow.id == candidate_id,
                DraftRow.dataset_id == dataset_id,
            )
            .with_for_update(read=True, of=CandidateRow)
        )
        if row is None:
            raise NotFound("Candidate not found")
        return row

    @staticmethod
    def _report_row(session, namespace_id, dataset_id, report_id, *, lock=False):
        query = select(QualityReportRow).where(
            QualityReportRow.id == report_id,
            QualityReportRow.namespace_id == namespace_id,
            QualityReportRow.dataset_id == dataset_id,
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise NotFound("Validation report not found")
        return row

    @observed("quality")
    def get_report(self, actor, namespace_id, dataset_id, report_id):
        with self._transaction(actor, namespace_id, dataset_id) as session:
            return self._report(self._report_row(session, namespace_id, dataset_id, report_id))

    @observed("quality")
    def list_reports(self, actor, namespace_id, dataset_id, candidate_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Report page must be between 1 and 100")
        with self._transaction(actor, namespace_id, dataset_id) as session:
            self._candidate(session, namespace_id, dataset_id, candidate_id)
            query = select(QualityReportRow).where(
                QualityReportRow.namespace_id == namespace_id,
                QualityReportRow.dataset_id == dataset_id,
                QualityReportRow.candidate_id == candidate_id,
            )
            if after is not None:
                query = query.where(QualityReportRow.id > after)
            rows = session.scalars(query.order_by(QualityReportRow.id).limit(limit + 1)).all()
            return ReportPage(
                items=[self._report(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @observed("quality")
    def advance(self, actor, namespace_id, dataset_id, report_id, command: ValidationBatch):
        with self._transaction(actor, namespace_id, dataset_id, write=True) as session:
            row = self._report_row(session, namespace_id, dataset_id, report_id, lock=True)
            if command.expected_count > row.processed_count:
                raise Conflict("Validation checkpoint is ahead of committed progress")
            if command.expected_count < row.processed_count or row.state == "complete":
                return self._report(row)
            if row.evaluator_version != EVALUATOR_VERSION:
                raise Conflict(
                    "This report requires its original evaluator version; start a new evaluation"
                )
            policy = self._policy_row(session, namespace_id, dataset_id, row.policy_id)
            definition = QualityDefinition.model_validate(policy.definition)
            query = (
                select(GenerationMemberRow, SampleRevisionRow)
                .join(
                    SampleRevisionRow,
                    (SampleRevisionRow.namespace_id == GenerationMemberRow.namespace_id)
                    & (SampleRevisionRow.id == GenerationMemberRow.sample_revision_id),
                )
                .where(
                    GenerationMemberRow.namespace_id == namespace_id,
                    GenerationMemberRow.generation_id == row.generation_id,
                )
            )
            if row.cursor is not None:
                query = query.where(GenerationMemberRow.sample_id > row.cursor)
            batch = session.execute(
                query.order_by(GenerationMemberRow.sample_id).limit(command.limit)
            ).all()
            processed = row.processed_count + len(batch)
            if processed > row.expected_count or (
                len(batch) < command.limit and processed != row.expected_count
            ):
                raise Conflict("Sealed generation count does not match validation progress")
            scanned = evaluate(
                definition,
                (
                    QualityMember(RegistryService._revision(revision), member.split)
                    for member, revision in batch
                ),
                expected_member_count=len(batch),
            )
            previous = [RuleEvidence.model_validate(item) for item in row.results]
            counts, failures, offending = [], [], []
            for old, new in zip(previous, scanned.results, strict=True):
                if old.rule_id != new.rule_id:
                    raise Conflict("Stored validation evidence does not match its policy")
                counts.append(old.examined + new.examined)
                failures.append(old.failures + new.failures)
                details = [
                    OffendingSample(item.sample_id, item.sample_revision_id)
                    for item in old.offending_samples
                ]
                offending.append((details + list(new.offending_samples))[:25])
            evaluation = summarize(definition, processed, counts, failures, offending)
            row.processed_count = processed
            if batch:
                row.cursor = batch[-1][0].sample_id
            row.results = results_json(evaluation)
            if processed == row.expected_count:
                row.state = "complete"
            self._audit(
                session,
                actor,
                namespace_id,
                row.id,
                "quality.validation_advanced",
                {
                    "dataset_id": str(dataset_id),
                    "candidate_id": str(row.candidate_id),
                    "processed_count": processed,
                    "state": row.state,
                },
            )
            session.flush()
            return self._report(row)
