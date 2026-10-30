from datetime import UTC, datetime
from decimal import localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from verdant.contracts import SampleRevision
from verdant.quality import QualityDefinition, QualityMember, RequiredMetadata, evaluate


def member(number, metadata=None, split="train"):
    return QualityMember(
        SampleRevision(
            id=UUID(int=100000 + number),
            namespace_id=UUID(int=1),
            sample_id=UUID(int=number),
            revision=1,
            sha256="0" * 64,
            object_key="private/object",
            size_bytes=10,
            metadata=metadata or {},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        split,
    )


def policy(*rules):
    return QualityDefinition.model_validate({"rules": rules})


def minimum(**fields):
    return {"id": "count", "kind": "minimum_samples", "minimum": 1, **fields}


def metadata(**fields):
    return {"id": "label", "kind": "required_metadata", "key": "label", **fields}


def test_split_thresholds_and_offenders_pin_exact_revision_without_private_data():
    definition = policy(minimum(minimum=2, split="train"), metadata(split="train"))
    report = evaluate(
        definition,
        [member(1, {"label": "cat"}), member(2), member(3, split="test")],
        expected_member_count=3,
    )
    assert report.member_count == 3
    assert report.results[0].status == "passed"
    result = report.results[1]
    assert (result.status, result.examined, result.failures) == ("failed", 2, 1)
    assert result.offending_samples[0].sample_id == UUID(int=2)
    assert result.offending_samples[0].sample_revision_id == UUID(int=100002)
    assert not report.required_rules_pass
    assert "private/object" not in repr(report)


@pytest.mark.parametrize("missing", [None, {}])
def test_missing_or_null_fails_but_false_zero_and_empty_string_are_present(missing):
    report = evaluate(
        policy(metadata()),
        [
            member(1, {"label": None} if missing is None else missing),
            member(2, {"label": False}),
            member(3, {"label": 0}),
            member(4, {"label": ""}),
        ],
        expected_member_count=4,
    )
    assert report.results[0].failures == 1
    assert report.results[0].examined == 4


def test_threshold_equality_passes_and_reevaluation_does_not_mutate_old_report():
    definition = policy(metadata(max_missing_fraction=0.1))
    first = evaluate(definition, (member(n) for n in range(1, 11)), expected_member_count=10)
    second = evaluate(
        definition,
        (member(n, {"label": "cat"} if n > 1 else {}) for n in range(1, 11)),
        expected_member_count=10,
    )
    assert not first.required_rules_pass
    assert second.required_rules_pass
    assert first.results[0].failures == 10
    assert second.results[0].failures == 1


@pytest.mark.parametrize("precision", [2, 28, 50])
def test_threshold_cannot_round_large_count_up_to_pass_or_depend_on_decimal_context(precision):
    rule = RequiredMetadata(id="label", key="label", max_missing_fraction=0.3333333333333333)
    with localcontext() as context:
        context.prec = precision
        assert not rule.allows_missing(333333333333333301, 1000000000000000003)
        assert rule.allows_missing(333333333333333300, 1000000000000000003)


def test_empty_generation_and_empty_split_never_vacuously_pass_metadata_gate():
    definition = policy(minimum(), metadata(split="test"))
    empty = evaluate(definition, [], expected_member_count=0)
    assert [result.status for result in empty.results] == ["failed", "unknown"]
    nonempty = evaluate(definition, [member(1, {"label": "cat"})], expected_member_count=1)
    assert [result.status for result in nonempty.results] == ["passed", "unknown"]
    assert not nonempty.required_rules_pass


def test_optional_failure_does_not_override_required_pass():
    report = evaluate(
        policy(minimum(), metadata(required=False)), [member(1)], expected_member_count=1
    )
    assert report.required_rules_pass
    assert report.results[1].status == "failed"


def test_streaming_evidence_is_bounded_and_counts_are_complete():
    report = evaluate(
        policy(metadata()),
        (member(n) for n in range(1, 10001)),
        expected_member_count=10000,
        offending_limit=3,
    )
    result = report.results[0]
    assert (report.member_count, result.examined, result.failures) == (10000, 10000, 10000)
    assert [item.sample_id.int for item in result.offending_samples] == [1, 2, 3]
    assert result.offending_samples_truncated
    no_details = evaluate(
        policy(metadata()), [member(1)], expected_member_count=1, offending_limit=0
    )
    assert no_details.results[0].offending_samples == ()
    assert no_details.results[0].offending_samples_truncated


def test_incomplete_scan_raises_instead_of_producing_passing_report():
    def interrupted():
        yield member(1, {"label": "cat"})
        raise OSError("dependency failed")

    with pytest.raises(OSError, match="dependency failed"):
        evaluate(policy(metadata()), interrupted(), expected_member_count=2)


@pytest.mark.parametrize("numbers", [[1, 1], [2, 1]])
def test_duplicate_or_repeated_pages_cannot_inflate_sample_count(numbers):
    with pytest.raises(ValueError, match="unique ascending"):
        evaluate(
            policy(minimum(minimum=2)),
            (member(n) for n in numbers),
            expected_member_count=2,
        )


@pytest.mark.parametrize("expected", [0, 2])
def test_count_must_match_sealed_generation_even_if_passing_prefix_ends_cleanly(expected):
    with pytest.raises(ValueError, match="sealed generation"):
        evaluate(policy(minimum()), [member(1)], expected_member_count=expected)


@pytest.mark.parametrize("expected", [-1, True, 1.5, 9223372036854775808])
def test_invalid_generation_counts_fail(expected):
    with pytest.raises(ValueError):
        evaluate(policy(minimum()), [], expected_member_count=expected)


@pytest.mark.parametrize(
    "rules",
    [
        [],
        [minimum(), minimum()],
        [metadata(required=False)],
        [minimum(minimum=0)],
        [minimum(minimum=True)],
        [metadata(max_missing_fraction=-0.1)],
        [metadata(max_missing_fraction=1.1)],
        [metadata(max_missing_fraction=float("nan"))],
        [metadata(key="secret\x00field")],
        [metadata(kind="unsupported")],
        [minimum(split="invalid split")],
        [minimum(unknown=True)],
    ],
)
def test_invalid_policy_cannot_be_silently_relaxed(rules):
    with pytest.raises(ValidationError):
        policy(*rules)


@pytest.mark.parametrize("limit", [-1, 101, True, 1.5])
def test_invalid_detail_limits_fail(limit):
    with pytest.raises(ValueError):
        evaluate(policy(minimum()), [], expected_member_count=0, offending_limit=limit)


def test_policy_revisions_are_immutable():
    definition = policy(metadata())
    with pytest.raises(ValidationError, match="frozen"):
        definition.rules[0].key = "different"
    restored = QualityDefinition.model_validate_json(definition.model_dump_json())
    assert restored == definition
