"""Actual verified artifacts and frozen interpretation pins compose after branch integration."""

from uuid import uuid4

import pytest

from verdant.application import RegistryService
from verdant.contracts import (
    DatasetCreate,
    InlineEvidence,
    InterpretationPins,
    MembershipWrite,
    SampleRevise,
)
from verdant.domain import Conflict
from verdant.ingestion_runner import IngestionRunner
from verdant.preview import PreviewService
from verdant.versioning import (
    CandidateCapture,
    CandidatePrepare,
    CandidateSelection,
    VersioningService,
)


def test_verified_replacement_and_metadata_revision_preserve_frozen_candidate_pins(
    ready_ingestion, sessions
):
    create, actor, ns, adapter = ready_ingestion
    _, uploaded = create([("source", b"original bytes")])
    runner = IngestionRunner(sessions, adapter)
    original = runner.run(actor, ns, uploaded.operation.id, 1).items[0]
    registry = RegistryService(sessions)
    dataset = registry.create_dataset(
        actor,
        ns,
        DatasetCreate(
            slug="integrated-" + uuid4().hex,
            name="Verified candidate",
            modality="text",
        ),
    )
    pins = InterpretationPins(
        task_schema_revision=InlineEvidence(
            format="integration.schema.v1", payload={"label": "text"}
        ),
        annotation_revision=InlineEvidence(
            format="integration.annotation.v1", payload={"label": "original"}
        ),
    )
    registry.set_membership(
        actor,
        ns,
        dataset.id,
        original.sample_id,
        MembershipWrite(
            expected_revision=0,
            sample_revision_id=original.accepted_revision_id,
            split="train",
            interpretation=pins,
        ),
    )
    versioning = VersioningService(sessions)
    candidate = versioning.capture(
        actor, ns, dataset.id, CandidateCapture(request_key="capture", expected_revision=1)
    )
    candidate = versioning.prepare(
        actor, ns, dataset.id, candidate.id, CandidatePrepare(expected_count=0)
    )
    assert candidate.state == "sealed"
    frozen = versioning.members(actor, ns, dataset.id, candidate.id).items
    _, replacement = create([("source", b"replacement bytes")], target=(original.sample_id, 1))
    replaced = runner.run(actor, ns, replacement.operation.id, 1).items[0]
    second = registry.get_sample_revision(
        actor, ns, replaced.sample_id, replaced.accepted_revision_id
    )
    third = registry.revise_sample(
        actor,
        ns,
        replaced.sample_id,
        SampleRevise(
            expected_revision=2,
            sha256=second.sha256,
            size_bytes=second.size_bytes,
            object_key=second.object_key,
            metadata={"title": "metadata only"},
        ),
    )
    with pytest.raises(Conflict, match="explicit replacement"):
        registry.set_membership(
            actor,
            ns,
            dataset.id,
            original.sample_id,
            MembershipWrite(
                expected_revision=1,
                sample_revision_id=third.id,
                split="validation",
            ),
        )
    registry.set_membership(
        actor,
        ns,
        dataset.id,
        original.sample_id,
        MembershipWrite(
            expected_revision=1,
            sample_revision_id=third.id,
            split="validation",
            interpretation=InterpretationPins(),
        ),
    )
    assert versioning.members(actor, ns, dataset.id, candidate.id).items == frozen
    assert frozen[0].sample_revision_id == original.accepted_revision_id
    assert frozen[0].interpretation == pins.model_dump(by_alias=True)
    preview = PreviewService(sessions, adapter)
    assert (
        preview.get(actor, ns, original.sample_id, original.accepted_revision_id).text
        == "original bytes"
    )
    assert preview.get(actor, ns, replaced.sample_id, third.id).text == "replacement bytes"
    with pytest.raises(Conflict):
        versioning.check_selection(
            actor, ns, dataset.id, candidate.id, CandidateSelection(mode="current_draft")
        )
    versioning.check_selection(
        actor, ns, dataset.id, candidate.id, CandidateSelection(mode="selected_candidate")
    )
