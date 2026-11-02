"""Reviewed, immutable truth snapshots over the retained registry membership seam.

This owns partition admission and pins only; it does not run experiments. Answer
metadata remains in interpretation, never in the content-only model input API.
"""

from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from verdant import annotation_contracts as a
from verdant.annotations import audit, authorize_dataset, evidence, idempotent
from verdant.application import RegistryService
from verdant.contracts import InterpretationPins
from verdant.domain import Conflict, NotFound
from verdant.persistence import (
    CanonicalAnnotationRow,
    MembershipRow,
    RegistryRepository,
    SampleRevisionRow,
    TruthVersionRow,
)
from verdant.telemetry import observed


def leakage_check(members):
    groups, content = {}, {}
    for member in members:
        partition = member["partition"]
        for index, key in ((groups, member["group"]), (content, member["content_digest"])):
            if key in index and index[key] != partition:
                raise Conflict("Group or content-duplicate leakage crosses truth partitions")
            index[key] = partition


def derive_validation(members):
    if any(member["partition"] == "validation" for member in members):
        return False
    train_groups = {member["group"] for member in members if member["partition"] == "train"}
    if not train_groups:
        return False
    if len(train_groups) < 2:
        raise Conflict("Validation derivation requires at least two independent train groups")
    # Keep duplicate content together even when source group metadata differs.
    components = [{group} for group in sorted(train_groups)]
    by_content = {}
    for member in members:
        if member["partition"] == "train":
            by_content.setdefault(member["content_digest"], set()).add(member["group"])
    for linked in by_content.values():
        intersecting = [component for component in components if component & linked]
        combined = set().union(*intersecting)
        components = [component for component in components if not component & linked] + [combined]
    if len(components) < 2:
        raise Conflict("Validation derivation cannot separate duplicate-connected train groups")
    components.sort(key=lambda component: a.digest(sorted(component)))
    selected = set().union(*components[: max(1, len(components) // 5)])
    for member in members:
        if member["partition"] == "train" and member["group"] in selected:
            member["partition"] = "validation"
    return True


class TruthService:
    def __init__(self, sessions):
        self.sessions = sessions

    @observed("truth")
    def workspace(self, actor, ns, dataset_id):
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            repo = RegistryRepository(session)
            draft = repo.draft(ns, dataset_id)
            rows = repo.membership_page(draft, draft.revision, None, 501)
            if len(rows) > 500:
                raise Conflict("Truth workspace supports at most 500 exact members")
            latest = session.scalar(
                select(TruthVersionRow)
                .where(
                    TruthVersionRow.namespace_id == ns,
                    TruthVersionRow.dataset_id == dataset_id,
                )
                .order_by(TruthVersionRow.pinned_revision.desc())
                .limit(1)
            )
            items = []
            successor = aliased(CanonicalAnnotationRow)
            for member in rows:
                canonical = session.scalar(
                    select(CanonicalAnnotationRow).where(
                        CanonicalAnnotationRow.namespace_id == ns,
                        CanonicalAnnotationRow.dataset_id == dataset_id,
                        CanonicalAnnotationRow.sample_revision_id == member.sample_revision_id,
                        ~exists(
                            select(successor.id).where(
                                successor.prior_id == CanonicalAnnotationRow.id
                            )
                        ),
                    )
                )
                revision = session.get(SampleRevisionRow, member.sample_revision_id)
                items.append(
                    {
                        "sample_id": str(member.sample_id),
                        "sample_revision_id": str(member.sample_revision_id),
                        "partition": member.split,
                        "content_digest": revision.sha256,
                        "group_id": revision.metadata_json.get("group_id"),
                        "canonical_id": str(canonical.id)
                        if canonical and not canonical.payload["unresolved"]
                        else None,
                        "canonical_label": canonical.payload["annotation"]["payload"]["label"]
                        if canonical
                        else None,
                        "unresolved": canonical.payload["unresolved"] if canonical else True,
                    }
                )
            return {
                "draft_revision": draft.revision,
                "latest_truth_id": str(latest.id) if latest else None,
                "members": items,
            }

    @observed("truth")
    def list(self, actor, ns, dataset_id, after=None, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid truth page size")
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            query = (
                select(TruthVersionRow)
                .where(
                    TruthVersionRow.namespace_id == ns,
                    TruthVersionRow.dataset_id == dataset_id,
                )
                .order_by(TruthVersionRow.id)
                .limit(limit + 1)
            )
            if after:
                query = query.where(TruthVersionRow.id > after)
            rows = session.scalars(query).all()
            return {
                "items": [evidence(row) for row in rows[:limit]],
                "next_after": rows[limit - 1].id if len(rows) > limit else None,
            }

    @observed("truth")
    def get(self, actor, ns, dataset_id, truth_id):
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id)
            row = self._row(session, ns, dataset_id, truth_id)
            return evidence(row)

    @staticmethod
    def _row(session, ns, dataset_id, truth_id):
        row = session.scalar(
            select(TruthVersionRow).where(
                TruthVersionRow.id == truth_id,
                TruthVersionRow.namespace_id == ns,
                TruthVersionRow.dataset_id == dataset_id,
            )
        )
        if row is None:
            raise NotFound("Truth version not found")
        return row

    @observed("truth")
    def publish(self, actor, ns, dataset_id, command):
        request_digest = a.digest(command.model_dump(mode="json"))
        with self.sessions.begin() as session:
            authorize_dataset(session, actor, ns, dataset_id, write=True)
            prior = idempotent(
                session,
                TruthVersionRow,
                ns,
                actor.id,
                command.request_key,
                request_digest,
                dataset_id,
            )
            if prior:
                return evidence(prior)
            repo = RegistryRepository(session)
            draft = RegistryService._locked_draft(
                repo, ns, dataset_id, command.expected_revision, actor
            )
            history = session.scalars(
                select(TruthVersionRow)
                .where(
                    TruthVersionRow.namespace_id == ns,
                    TruthVersionRow.dataset_id == dataset_id,
                )
                .order_by(TruthVersionRow.pinned_revision)
                .limit(1001)
            ).all()
            if len(history) > 1000:
                raise Conflict("Truth lineage exceeds the supported 1000 versions")
            latest = history[-1] if history else None
            if (latest.id if latest else None) != command.expected_truth_id:
                raise Conflict("Truth version changed; inspect the current snapshot")
            current = repo.membership_page(draft, draft.revision, None, 501)
            if len(current) > 500 or {m.sample_id for m in current} != {
                m.sample_id for m in command.members
            }:
                raise Conflict(
                    "Truth publication must assign every exact current member, at most 500"
                )
            current = {member.sample_id: member for member in current}
            truth_id = uuid4()
            members = []
            for requested in sorted(command.members, key=lambda member: member.sample_id):
                source = current[requested.sample_id]
                revision = session.get(SampleRevisionRow, source.sample_revision_id)
                raw_group = revision.metadata_json.get("group_id")
                if raw_group is not None and (
                    not isinstance(raw_group, str) or not raw_group.strip() or len(raw_group) > 200
                ):
                    raise Conflict("Source group_id must be nonempty text up to 200 characters")
                group = "group:" + raw_group if raw_group else "sample:" + str(source.sample_id)
                transform = InterpretationPins.model_validate(source.interpretation).transformation
                pins = InterpretationPins(transformation=transform)
                canonical = None
                if requested.canonical_id:
                    canonical = session.scalar(
                        select(CanonicalAnnotationRow).where(
                            CanonicalAnnotationRow.id == requested.canonical_id,
                            CanonicalAnnotationRow.namespace_id == ns,
                            CanonicalAnnotationRow.dataset_id == dataset_id,
                            CanonicalAnnotationRow.sample_id == source.sample_id,
                            CanonicalAnnotationRow.sample_revision_id == source.sample_revision_id,
                        )
                    )
                    if canonical is None or canonical.payload["unresolved"]:
                        raise Conflict(
                            "Truth requires exact reviewed canonical evidence; "
                            "unresolved cases are excluded"
                        )
                    pins = InterpretationPins(
                        annotation_revision=canonical.payload["annotation"],
                        task_schema_revision=canonical.payload["task_schema"],
                        review_evidence=canonical.payload["review"],
                        transformation=transform,
                    )
                if requested.partition != "production" and canonical is None:
                    raise Conflict(
                        "Development and held-out partitions require reviewed canonical labels"
                    )
                if requested.partition == "production" and canonical and requested.audit is None:
                    raise Conflict(
                        "Labeled production requires explicit sample-selection audit provenance"
                    )
                audit_pin = None
                if requested.audit:
                    if requested.partition != "production" or canonical is None:
                        raise Conflict(
                            "Audited production remains production and requires "
                            "reviewed canonical truth"
                        )
                    audit_source = self._row(
                        session, ns, dataset_id, requested.audit.source_truth_id
                    )
                    selected = next(
                        (
                            m
                            for m in audit_source.payload["members"]
                            if m["sample_id"] == str(source.sample_id)
                            and m["sample_revision_id"] == str(source.sample_revision_id)
                        ),
                        None,
                    )
                    if selected is None or selected["partition"] != "production":
                        raise Conflict(
                            "Audit selection must refer to exact prior production membership"
                        )
                    audit_pin = requested.audit.model_dump(mode="json") | {
                        "source_truth_digest": audit_source.digest,
                        "canonical_id": str(canonical.id),
                        "canonical_digest": canonical.digest,
                        "reviewer_id": str(canonical.reviewer_id),
                    }
                members.append(
                    {
                        "sample_id": str(source.sample_id),
                        "sample_revision_id": str(source.sample_revision_id),
                        "content_digest": revision.sha256,
                        "group": group,
                        "partition": requested.partition,
                        "canonical_id": str(canonical.id) if canonical else None,
                        "canonical_digest": canonical.digest if canonical else None,
                        "interpretation": pins.model_dump(mode="json", by_alias=True),
                        "audit": audit_pin,
                    }
                )
            derived = derive_validation(members)
            leakage_check(members)
            heldout_content, heldout_groups = set(), set()
            known_content, known_groups = set(), set()
            for old in history:
                for member in old.payload["members"]:
                    known_content.add(member["content_digest"])
                    known_groups.add(member["group"])
                    if member["partition"] == "test":
                        heldout_content.add(member["content_digest"])
                        heldout_groups.add(member["group"])
            reused = [
                member["sample_id"]
                for member in members
                if member["partition"] in {"train", "validation"}
                and (
                    member["content_digest"] in heldout_content or member["group"] in heldout_groups
                )
            ]
            if reused and not command.exploratory_test_reuse:
                raise Conflict(
                    "Prior test data entered train/validation; explicitly disclose "
                    "exploratory test reuse"
                )
            if command.confirmation_of:
                confirmation = self._row(session, ns, dataset_id, command.confirmation_of)
                test_members = [member for member in members if member["partition"] == "test"]
                if (
                    not test_members
                    or reused
                    or command.exploratory_test_reuse
                    or command.evaluation_use_declaration != "not_declared"
                    or any(
                        m["content_digest"] in known_content or m["group"] in known_groups
                        for m in test_members
                    )
                ):
                    raise Conflict(
                        "Fresh confirmation requires new held-out groups/content "
                        "and no exploratory reuse"
                    )
                confirmation_pin = {"id": str(confirmation.id), "digest": confirmation.digest}
            else:
                confirmation_pin = None
            source_revision = draft.revision
            draft.revision += 1
            for member in current.values():
                member.valid_to = draft.revision
            session.flush()
            for member in members:
                source = current[UUID(member["sample_id"])]
                session.add(
                    MembershipRow(
                        namespace_id=ns,
                        draft_id=draft.id,
                        sample_id=source.sample_id,
                        sample_revision_id=source.sample_revision_id,
                        split=member["partition"],
                        interpretation=member["interpretation"],
                        valid_from=draft.revision,
                    )
                )
            payload = {
                "format": "verdant.truth.v1",
                "id": str(truth_id),
                "source_revision": source_revision,
                "pinned_revision": draft.revision,
                "members": members,
                "validation_derived": derived,
                "validation_derivation": (
                    "sha256-sorted connected train groups; first floor(20%), "
                    "at least one group, before tuning"
                )
                if derived
                else None,
                "exploratory_test_reuse": command.exploratory_test_reuse,
                "evaluation_use_declaration": command.evaluation_use_declaration,
                "reused_test_samples": reused,
                "confirmation_of": confirmation_pin,
                "limits": (
                    "Partition admission is pre-tuning. Test access/use must be tracked by the "
                    "experiment authority; this snapshot does not certify unseen labels. "
                    "Production audit strata/probabilities are operator-declared selection "
                    "facts, not population-quality estimates. Missing group IDs fall back "
                    "to sample identity; undisclosed real-world groups cannot be detected."
                ),
            }
            row = TruthVersionRow(
                id=truth_id,
                namespace_id=ns,
                dataset_id=dataset_id,
                prior_id=latest.id if latest else None,
                source_revision=source_revision,
                pinned_revision=draft.revision,
                creator_id=actor.id,
                request_key=command.request_key,
                request_digest=request_digest,
                payload=payload,
                digest=a.digest(payload),
            )
            session.add(row)
            session.flush()
            audit(
                session,
                actor,
                ns,
                truth_id,
                "truth.published",
                {
                    "digest": row.digest,
                    "dataset_id": str(dataset_id),
                    "pinned_revision": draft.revision,
                    "exploratory_test_reuse": command.exploratory_test_reuse,
                    "evaluation_use_declaration": command.evaluation_use_declaration,
                    "audited_count": sum(m["audit"] is not None for m in members),
                },
            )
            return evidence(row)
