"""Bounded single categorical text task; unsupported modalities fail explicitly."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID
from xml.sax.saxutils import quoteattr

from pydantic import Field, model_validator

from verdant.contracts import Contract, Revision, TransformationPin

MAX_ASSIGNMENTS = 100


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


class CategoricalSchema(Contract):
    task: Literal["single_categorical_text.v1"] = "single_categorical_text.v1"
    labels: list[str] = Field(min_length=2, max_length=30)
    instruction_revision: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def labels_unique(self):
        if len(set(self.labels)) != len(self.labels) or any(
            not label.strip() or len(label) > 100 for label in self.labels
        ):
            raise ValueError("Labels must be unique nonempty strings up to 100 characters")
        if not self.instructions.strip():
            raise ValueError("Instructions must be nonempty")
        return self

    def native_xml(self):
        choices = "".join(f"<Choice value={quoteattr(label)}/>" for label in self.labels)
        return (
            f"<View><Header value={quoteattr(self.instructions)}/>"
            '<Text name="text" value="$text"/>'
            f'<Choices name="label" toName="text" choice="single" required="true">{choices}'
            '</Choices><Choices name="assistance" toName="text" choice="single">'
            '<Choice value="independent"/><Choice value="assisted"/>'
            '<Choice value="unknown"/></Choices></View>'
        )


class AssignmentCreate(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    expected_revision: Revision
    cohort_id: UUID
    sample_id: UUID
    sample_revision_id: UUID
    native_project_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    native_task_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    task_schema: CategoricalSchema
    contributors: list[UUID] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.contributors)) != len(self.contributors):
            raise ValueError("Contributors must be distinct")
        return self


class Assignment(Contract):
    id: UUID
    dataset_id: UUID
    cohort_id: UUID
    sample_id: UUID
    sample_revision_id: UUID
    source_revision: int
    native_project_id: int
    native_task_id: int
    task_schema: CategoricalSchema
    schema_digest: str
    contributors: list[UUID]
    editor_url: str
    created_at: datetime


class Original(Contract):
    id: UUID
    principal_id: UUID
    native_annotation_id: int
    native_user_id: int
    sequence: int
    digest: str
    payload: dict


class AssignmentDetail(Contract):
    assignment: Assignment
    originals: list[Original]
    latest: list[Original]
    missing_contributors: list[UUID]
    canonical_id: UUID | None


class IntegrationStatus(Contract):
    available: bool
    reason: str
    cohorts: list[dict]
    shared_trust: str = (
        "Each Community instance is one shared-trust cohort. Projects do not isolate users. "
        "Independent authorship is recorded; blindness and correctness are not guaranteed. "
        "Use separate instances, data and credentials for separate trust cohorts."
    )


class ImportRequest(Contract):
    expected_original_count: Revision


class ReportCreate(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    assignment_ids: list[UUID] = Field(min_length=1, max_length=MAX_ASSIGNMENTS)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.assignment_ids)) != len(self.assignment_ids):
            raise ValueError("Assignment IDs must be distinct")
        return self


class Evidence(Contract):
    id: UUID
    digest: str
    payload: dict
    created_at: datetime


class Adjudicate(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    report_id: UUID
    assignment_id: UUID
    expected_canonical_id: UUID | None
    label: str | None = Field(default=None, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def rationale_present(self):
        if not self.rationale.strip():
            raise ValueError("Adjudication requires a rationale")
        return self


class AuditSelection(Contract):
    source_truth_id: UUID
    stratum: str = Field(min_length=1, max_length=200)
    inclusion_probability: float = Field(gt=0, le=1)
    selection_method: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def meaningful_selection(self):
        if not self.stratum.strip() or not self.selection_method.strip():
            raise ValueError("Audit stratum and selection method must be nonempty")
        return self


class TruthMember(Contract):
    sample_id: UUID
    partition: Literal["train", "validation", "test", "production"]
    canonical_id: UUID | None = None
    audit: AuditSelection | None = None


class TruthPublish(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    expected_revision: Revision
    expected_truth_id: UUID | None
    members: list[TruthMember] = Field(min_length=1, max_length=500)
    exploratory_test_reuse: bool = Field(default=False, strict=True)
    evaluation_use_declaration: Literal["not_declared", "exploratory_test_reuse"] = "not_declared"
    confirmation_of: UUID | None = None

    @model_validator(mode="after")
    def distinct(self):
        if len({member.sample_id for member in self.members}) != len(self.members):
            raise ValueError("Truth samples must be distinct")
        return self


# Public transport contracts retain the concrete evidence shape. The internal
# Evidence wrapper above is used only by shared persistence helpers.
class ContributorAdmission(Contract):
    principal_id: UUID
    native_user_id: int
    reviewer: bool


class CohortView(Contract):
    id: UUID
    name: str
    origin: str
    image: str
    contributors: list[ContributorAdmission] = Field(max_length=100)


class IntegrationResponse(Contract):
    available: bool
    reason: str
    cohorts: list[CohortView] = Field(max_length=50)
    shared_trust: str


class AssignmentPage(Contract):
    items: list[Assignment] = Field(max_length=100)
    next_after: UUID | None = None


class NativeChoiceValue(Contract):
    choices: list[str] = Field(min_length=1, max_length=1)


class NativeChoice(Contract):
    from_name: Literal["label", "assistance"]
    to_name: Literal["text"]
    type: Literal["choices"]
    value: NativeChoiceValue
    id: str | None = None
    origin: str | None = None


class NativeResult(Contract):
    id: int
    task: int
    completed_by: int
    updated_by: int | None = None
    result: list[NativeChoice] = Field(max_length=2)
    was_cancelled: bool


class Modifier(Contract):
    principal_id: UUID
    issuer: str
    subject: str
    native_user_id: int


class OriginalPayload(Contract):
    native: NativeResult
    label: str | None
    assistance: Literal["independent", "assisted", "unknown"]
    state: Literal["cancelled", "abstained", "submitted"]
    issuer: str
    subject: str
    schema_digest: str
    declared_assistance: Literal["independent", "assisted", "unknown"]
    independence: Literal["self_declared", "modifier_unknown", "cross_author_edit"]
    modifier: Modifier | None
    native_deleted: bool = False


class OriginalResponse(Contract):
    id: UUID
    principal_id: UUID
    native_annotation_id: int
    native_user_id: int
    sequence: int
    digest: str
    payload: OriginalPayload


class AssignmentDetailResponse(Contract):
    assignment: Assignment
    originals: list[OriginalResponse] = Field(max_length=1000)
    latest: list[OriginalResponse] = Field(max_length=1000)
    missing_contributors: list[UUID] = Field(max_length=10)
    canonical_id: UUID | None


class OriginalReference(Contract):
    id: UUID
    digest: str


class ContributorResult(Contract):
    original_id: UUID | None = None
    digest: str | None = None
    native_annotation_id: int | None = None
    native_user_id: int | None = None
    issuer: str | None = None
    subject: str | None = None
    label: str | None
    state: Literal["missing", "submitted", "abstained"]
    assistance: Literal["independent", "assisted", "unknown"]


class AgreementUnit(Contract):
    assignment_id: UUID
    sample_id: UUID
    sample_revision_id: UUID
    source_revision: int
    schema_digest: str
    contributors: dict[str, ContributorResult]
    original_history: list[OriginalReference] = Field(max_length=1000)


class AgreementCounts(Contract):
    submitted: int
    abstained: int
    missing: int
    assisted: int
    unknown: int


class PairAgreement(Contract):
    left: UUID
    right: UUID
    overlap: int
    agreed: int
    declared_independent_overlap: int
    observed_agreement: float | None
    agreement_95_wilson: list[float] | None
    cohen_kappa: float | None
    kappa_undefined_reason: Literal["no overlap", "constant identical marginals"] | None
    confusion: dict[str, dict[str, int]]
    prevalence_left: dict[str, int]
    prevalence_right: dict[str, int]


class AgreementPayload(Contract):
    format: Literal["verdant.agreement.v1"]
    task_schema: CategoricalSchema
    schema_digest: str
    cohort_id: UUID
    units: list[AgreementUnit] = Field(max_length=100)
    counts: AgreementCounts
    pairs: list[PairAgreement] = Field(max_length=45)
    disagreement_queue: list[UUID] = Field(max_length=100)
    limits: str


class ReportEvidence(Contract):
    id: UUID
    digest: str
    payload: AgreementPayload
    created_at: datetime


class CanonicalLabel(Contract):
    id: UUID
    sample_revision_id: UUID
    schema_digest: str
    label: str | None
    decision: Literal["accepted", "abstained"]
    reviewer_id: UUID
    rationale: str
    report_id: UUID
    report_digest: str
    originals: list[OriginalReference] = Field(max_length=10)
    prior_id: UUID | None


class CanonicalPin(Contract):
    format: Literal["verdant.canonical-annotation.v1"]
    payload: CanonicalLabel
    sha256: str


class CategoricalSchemaPin(Contract):
    format: Literal["verdant.categorical-schema.v1"]
    payload: CategoricalSchema
    sha256: str


class CanonicalReview(Contract):
    annotation_digest: str
    decision: Literal["accepted", "abstained"]
    rationale: str


class CanonicalPayload(Contract):
    annotation: CanonicalPin
    task_schema: CategoricalSchemaPin
    review: CanonicalReview
    unresolved: bool


class CanonicalEvidence(Contract):
    id: UUID
    digest: str
    payload: CanonicalPayload
    created_at: datetime


class TruthWorkspaceMember(Contract):
    sample_id: UUID
    sample_revision_id: UUID
    partition: str
    content_digest: str
    group_id: str | None
    canonical_id: UUID | None
    canonical_label: str | None
    unresolved: bool


class TruthWorkspace(Contract):
    draft_revision: int
    latest_truth_id: UUID | None
    members: list[TruthWorkspaceMember] = Field(max_length=500)


class TruthAuditPin(AuditSelection):
    source_truth_digest: str
    canonical_id: UUID
    canonical_digest: str
    reviewer_id: UUID


class TruthInterpretation(Contract):
    schema_name: Literal["verdant.membership.v1"] = Field(alias="schema")
    annotation_revision: CanonicalPin | None
    task_schema_revision: CategoricalSchemaPin | None
    review_evidence: CanonicalReview | None
    transformation: TransformationPin | None = None


class TruthPinnedMember(Contract):
    sample_id: UUID
    sample_revision_id: UUID
    content_digest: str
    group: str
    partition: Literal["train", "validation", "test", "production"]
    canonical_id: UUID | None
    canonical_digest: str | None
    interpretation: TruthInterpretation
    audit: TruthAuditPin | None


class TruthReference(Contract):
    id: UUID
    digest: str


class TruthPayload(Contract):
    format: Literal["verdant.truth.v1"]
    id: UUID
    source_revision: int
    pinned_revision: int
    members: list[TruthPinnedMember] = Field(max_length=500)
    validation_derived: bool
    validation_derivation: str | None
    exploratory_test_reuse: bool
    evaluation_use_declaration: Literal["not_declared", "exploratory_test_reuse"]
    reused_test_samples: list[UUID] = Field(max_length=500)
    confirmation_of: TruthReference | None
    limits: str


class TruthEvidence(Contract):
    id: UUID
    digest: str
    payload: TruthPayload
    created_at: datetime


class TruthPage(Contract):
    items: list[TruthEvidence] = Field(max_length=100)
    next_after: UUID | None = None
