import hashlib
import io
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from verdant import manifest
from verdant.contracts import InterpretationPins
from verdant.manifest import (
    ManifestArtifact,
    ManifestHeader,
    ManifestMember,
    write_manifest,
)

EXPECTED_TEXT = (
    '{"candidate_id":"00000000-0000-0000-0000-000000000003",'
    '"dataset_id":"00000000-0000-0000-0000-000000000002",'
    '"generation_id":"00000000-0000-0000-0000-000000000004",'
    '"members":[{"artifact":{"id":"00000000-0000-0000-0000-000000000007",'
    '"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"size_bytes":4},"interpretation":{"annotation_revision":null,"review_evidence":null,'
    '"schema":"verdant.membership.v1","task_schema_revision":null,"transformation":null},'
    '"metadata":{"a":1,"label":"café"},"sample_id":"00000000-0000-0000-0000-000000000005",'
    '"sample_revision_id":"00000000-0000-0000-0000-000000000006","split":"train"}],'
    '"namespace_id":"00000000-0000-0000-0000-000000000001",'
    '"policy":{"id":"policy","revision":1},"schema":"verdant.manifest.v1",'
    '"source_revision":9,"validation_report":{"id":"report","required_rules_pass":true}}'
)
EXPECTED_SHA256 = "f48286dc1085aeb0fcaaa7d472acb208e891cf2e5110e6c160f994b8ec6c95fa"


def header(**changes):
    return ManifestHeader(
        **(
            {
                "namespace_id": UUID(int=1),
                "dataset_id": UUID(int=2),
                "candidate_id": UUID(int=3),
                "generation_id": UUID(int=4),
                "source_revision": 9,
                "policy": {"id": "policy", "revision": 1},
                "validation_report": {"id": "report", "required_rules_pass": True},
            }
            | changes
        )
    )


def member(sample=5, **changes):
    return ManifestMember(
        **(
            {
                "sample_id": UUID(int=sample),
                "sample_revision_id": UUID(int=sample + 1),
                "split": "train",
                "metadata": {"label": "café", "a": 1},
                "interpretation": InterpretationPins(),
                "artifact": ManifestArtifact(id=UUID(int=7), sha256="a" * 64, size_bytes=4),
            }
            | changes
        )
    )


def encode(selected_header=None, members=None, *, count=1):
    destination = io.BytesIO()
    digest = write_manifest(
        destination,
        selected_header if selected_header is not None else header(),
        members if members is not None else [member()],
        expected_member_count=count,
    )
    return destination.getvalue(), digest


def test_fixed_literal_utf8_bytes_and_sha256_vector():
    data, digest = encode()
    assert data == EXPECTED_TEXT.encode("utf-8")
    assert digest.sha256 == EXPECTED_SHA256
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256
    assert digest.size_bytes == len(EXPECTED_TEXT.encode("utf-8"))
    assert b"caf\xc3\xa9" in data
    assert b"\\u00e9" not in data
    assert not data.endswith(b"\n")


def test_repeatable_across_nested_dictionary_insertion_order():
    first, digest = encode(
        header(policy={"z": {"two": 2, "one": 1}, "a": [True, None]}),
        [member(metadata={"z": {"two": 2, "one": 1}, "a": "日本語"})],
    )
    second, repeated = encode(
        header(policy={"a": [True, None], "z": {"one": 1, "two": 2}}),
        [member(metadata={"a": "日本語", "z": {"one": 1, "two": 2}})],
    )
    assert first == second
    assert digest == repeated


def test_schema_contains_only_logical_artifact_identity_not_storage_placement():
    data, _ = encode()
    parsed = json.loads(data)
    assert parsed["schema"] == "verdant.manifest.v1"
    assert parsed["members"][0]["artifact"] == {
        "id": str(UUID(int=7)),
        "sha256": "a" * 64,
        "size_bytes": 4,
    }
    for field in ("endpoint", "bucket", "object_key", "signed_url", "version_id", "label"):
        assert field not in parsed
        assert field not in parsed["members"][0]["artifact"]
    for placement in ("endpoint", "bucket", "object_key", "signed_url"):
        with pytest.raises(ValidationError):
            ManifestArtifact(
                id=UUID(int=7), sha256="a" * 64, size_bytes=4, **{placement: "private"}
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "A" * 64},
        {"sha256": "not-a-digest"},
        {"size_bytes": -1},
        {"size_bytes": True},
        {"size_bytes": 1.0},
        {"size_bytes": manifest.MAX_ARTIFACT_BYTES + 1},
    ],
)
def test_artifact_contract_rejects_invalid_identity_or_size(changes):
    with pytest.raises(ValidationError):
        ManifestArtifact(**({"id": UUID(int=7), "sha256": "a" * 64, "size_bytes": 4} | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_id": "not-a-uuid"},
        {"split": "Upper Case"},
        {"metadata": {"bad": float("nan")}},
        {"metadata": {"bad": "\x00"}},
        {"metadata": {"oversized": "é" * 32768}},
        {"interpretation": {"schema": "latest"}},
        {"object_key": "placement-is-not-a-member-field"},
    ],
)
def test_member_contract_rejects_invalid_or_unbounded_input(changes):
    with pytest.raises(ValidationError):
        member(**changes)


@pytest.mark.parametrize(
    "changes", [{"source_revision": True}, {"policy": {"bad": float("inf")}}, {"bucket": "private"}]
)
def test_header_contract_rejects_invalid_or_extra_input(changes):
    with pytest.raises(ValidationError):
        header(**changes)


@pytest.mark.parametrize("samples", [[5, 5], [6, 5]])
def test_rejects_duplicate_or_out_of_order_sample_uuid(samples):
    destination = io.BytesIO()
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        write_manifest(
            destination, header(), (member(sample) for sample in samples), expected_member_count=2
        )
    with pytest.raises(json.JSONDecodeError):
        json.loads(destination.getvalue())


@pytest.mark.parametrize("actual,expected", [(0, 1), (1, 2), (1, 0)])
def test_rejects_incomplete_or_overfull_membership(actual, expected):
    with pytest.raises(ValueError, match="incomplete|more members"):
        encode(members=(member(5 + n) for n in range(actual)), count=expected)


@pytest.mark.parametrize("count", [-1, True, 1.0, "1", 2**63])
def test_invalid_expected_count_rejected_before_any_write(count):
    destination = io.BytesIO()
    with pytest.raises(ValueError, match="nonnegative 64-bit integer"):
        write_manifest(destination, header(), [], expected_member_count=count)
    assert destination.getvalue() == b""


def test_empty_manifest_is_complete_and_has_a_digest():
    data, digest = encode(members=[], count=0)
    assert json.loads(data)["members"] == []
    assert digest.sha256 == hashlib.sha256(data).hexdigest()


def test_streams_one_member_before_requesting_the_next_and_bounds_destination_writes():
    destination = io.BytesIO()
    generated = 0

    class BoundedDestination:
        def write(self, data):
            assert len(data) <= manifest.WRITE_CHUNK_BYTES
            return destination.write(data)

    def members():
        nonlocal generated
        for number in range(5, 25):
            if generated:
                assert f'"sample_id":"{UUID(int=number - 1)}"'.encode() in destination.getvalue()
            generated += 1
            yield member(number, metadata={"label": "x" * 30000})

    digest = write_manifest(BoundedDestination(), header(), members(), expected_member_count=20)
    assert generated == 20
    assert len(json.loads(destination.getvalue())["members"]) == 20
    assert digest.size_bytes == len(destination.getvalue())


def test_checks_output_budget_before_write_without_large_allocations(monkeypatch):
    monkeypatch.setattr(manifest, "MAX_ARTIFACT_BYTES", 400)
    destination = io.BytesIO()
    with pytest.raises(ValueError, match="exceeds supported artifact size"):
        write_manifest(destination, header(), [member()], expected_member_count=1)
    assert len(destination.getvalue()) <= 400


def test_exact_output_budget_succeeds_but_one_byte_less_never_overruns(monkeypatch):
    expected = EXPECTED_TEXT.encode("utf-8")
    monkeypatch.setattr(manifest, "MAX_ARTIFACT_BYTES", len(expected))
    data, digest = encode()
    assert data == expected
    assert digest.size_bytes == len(expected)
    monkeypatch.setattr(manifest, "MAX_ARTIFACT_BYTES", len(expected) - 1)
    destination = io.BytesIO()
    with pytest.raises(ValueError, match="exceeds supported artifact size"):
        write_manifest(destination, header(), [member()], expected_member_count=1)
    assert len(destination.getvalue()) == len(expected) - 1


def test_accepts_short_positive_writes_and_hashes_only_actual_output():
    destination = io.BytesIO()

    class ShortDestination:
        def write(self, data):
            return destination.write(data[:3])

    digest = write_manifest(ShortDestination(), header(), [member()], expected_member_count=1)
    assert destination.getvalue() == EXPECTED_TEXT.encode("utf-8")
    assert digest.sha256 == hashlib.sha256(destination.getvalue()).hexdigest()


@pytest.mark.parametrize("accepted", [0, None, -1, True, 100000])
def test_invalid_destination_write_never_returns_a_receipt(accepted):
    class BrokenDestination:
        def write(self, data):
            return accepted

    with pytest.raises(OSError, match="did not accept"):
        write_manifest(BrokenDestination(), header(), [member()], expected_member_count=1)


def test_propagates_iterator_and_destination_failure_without_a_receipt():
    def failed_members():
        yield member()
        raise RuntimeError("Source page failed")

    with pytest.raises(RuntimeError, match="Source page failed"):
        encode(members=failed_members(), count=2)

    class BrokenDestination:
        def write(self, data):
            raise OSError("Disk is full")

    with pytest.raises(OSError, match="Disk is full"):
        write_manifest(BrokenDestination(), header(), [member()], expected_member_count=1)


def test_revalidates_mutated_models_before_serializing_them():
    selected = header()
    selected.policy["bad"] = float("nan")
    with pytest.raises(ValidationError):
        encode(selected)
    changed = member()
    changed.artifact.size_bytes = True
    with pytest.raises(ValidationError):
        encode(members=[changed])
