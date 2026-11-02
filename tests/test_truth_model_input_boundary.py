"""Integration gate for the sibling-owned content-only evaluated-input projector.

This test runs once epic 4 is integrated. The standalone annotation branch does
not copy the sibling implementation or expose a fallback projector.
"""

import inspect

import pytest

from verdant.application import RegistryService
from verdant.contracts import InlineEvidence, InterpretationPins
from verdant.ingestion_runner import IngestionRunner
from verdant.storage import ObjectDigest

model_inputs = pytest.importorskip(
    "verdant.model_inputs", reason="Epic 4 projector is integrated by parent"
)


def test_verified_bytes_exclude_all_answer_interpretation_review_and_audit_metadata(
    ready_ingestion,
    sessions,
):
    create, actor, ns, adapter = ready_ingestion
    content = b"Only the original UTF-8 source enters evaluation.\n"
    poison = "ANSWER_METADATA_MUST_NEVER_ENTER_MODEL_INPUT"
    _, batch = create(
        entries=[("strict-boundary", content)],
        metadata={
            "answer": poison,
            "labels": [poison],
            "review": {"rationale": poison},
            "group_id": poison,
            "audit": {"stratum": poison, "inclusion_probability": 0.2},
        },
    )
    accepted = IngestionRunner(sessions, adapter).run(actor, ns, batch.operation.id, 1)
    item = accepted.items[0]
    revision = RegistryService(sessions).get_sample_revision(
        actor, ns, item.sample_id, item.accepted_revision_id
    )
    assert revision.metadata["answer"] == poison
    pins = InterpretationPins(
        annotation_revision=InlineEvidence(format="truth-test", payload={"label": poison}),
        task_schema_revision=InlineEvidence(format="truth-schema-test", payload={"answer": poison}),
    )
    adapter.verify(revision.object_key, ObjectDigest(revision.sha256, revision.size_bytes))
    verified = adapter.read_range(revision.object_key, 0, revision.size_bytes - 1)
    assert verified == content
    projected = model_inputs.project_model_input(verified, modality="text")
    assert projected.model_dump() == {"text": content.decode()}
    assert poison not in projected.model_dump_json()
    assert set(inspect.signature(model_inputs.project_model_input).parameters) == {
        "content",
        "modality",
    }
    for forbidden in ("metadata", "interpretation", "truth", "review", "manifest", "audit"):
        with pytest.raises(TypeError):
            model_inputs.project_model_input(verified, **{forbidden: pins.model_dump()})
