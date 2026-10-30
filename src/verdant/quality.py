"""Deterministic, bounded core checks over an already-authorized immutable generation.

This module does not accept evidence or authorize publication. The application service
must read sealed membership and persist the exact policy/report identities before its
results can become gate evidence. Iterator errors propagate; partial scans never pass.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from verdant.contracts import Contract, SampleRevision

EVALUATOR_VERSION = "verdant.core/1"
Split = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")]


class Rule(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    required: bool = True
    split: Split | None = None


class MinimumSamples(Rule):
    kind: Literal["minimum_samples"] = "minimum_samples"
    minimum: int = Field(ge=1, le=9223372036854775807, strict=True)


class RequiredMetadata(Rule):
    kind: Literal["required_metadata"] = "required_metadata"
    key: str = Field(min_length=1, max_length=200)
    max_missing_fraction: float = Field(default=0, ge=0, le=1, strict=True)

    def allows_missing(self, failures: int, examined: int) -> bool:
        numerator, denominator = Decimal(str(self.max_missing_fraction)).as_integer_ratio()
        return failures * denominator <= numerator * examined


QualityRule = Annotated[MinimumSamples | RequiredMetadata, Field(discriminator="kind")]


class QualityDefinition(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    schema_version: Literal[1] = 1
    rules: tuple[QualityRule, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_rule_ids(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Quality rule identifiers must be unique")
        if not any(rule.required for rule in self.rules):
            raise ValueError("At least one required rule is needed")
        return self


@dataclass(frozen=True)
class QualityMember:
    revision: SampleRevision
    split: str


@dataclass(frozen=True)
class OffendingSample:
    sample_id: UUID
    sample_revision_id: UUID


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    required: bool
    split: str | None
    status: Literal["passed", "failed", "unknown"]
    examined: int
    failures: int
    offending_samples: tuple[OffendingSample, ...]
    offending_samples_truncated: bool


@dataclass(frozen=True)
class Evaluation:
    evaluator_version: str
    member_count: int
    results: tuple[RuleResult, ...]

    @property
    def required_rules_pass(self) -> bool:
        return all(result.status == "passed" for result in self.results if result.required)


def evaluate(
    definition: QualityDefinition,
    members: Iterable[QualityMember],
    *,
    expected_member_count: int,
    offending_limit: int = 25,
) -> Evaluation:
    """Scan a generation once in ascending sample UUID order, retaining bounded evidence.

    The sealed-generation query owns unique membership. Requiring its existing keyset
    ordering detects duplicate/repeated pages without an unbounded set of sample IDs.
    Metadata rules check top-level presence; JSON null is missing. An empty selection is
    unknown for a metadata rule and fails a minimum-count rule. Neither passes a required gate.
    """
    if (
        type(expected_member_count) is not int
        or not 0 <= expected_member_count <= 9223372036854775807
    ):
        raise ValueError("Expected member count must be a nonnegative PostgreSQL bigint")
    if type(offending_limit) is not int or not 0 <= offending_limit <= 100:
        raise ValueError("Offending sample limit must be between 0 and 100")
    counts = [0] * len(definition.rules)
    failures = [0] * len(definition.rules)
    offending: list[list[OffendingSample]] = [[] for _ in definition.rules]
    previous_id: UUID | None = None
    member_count = 0
    for member in members:
        revision = member.revision
        if previous_id is not None and revision.sample_id <= previous_id:
            raise ValueError("Generation members must have unique ascending sample IDs")
        previous_id = revision.sample_id
        member_count += 1
        if member_count > expected_member_count:
            raise ValueError("Generation member count differs from the sealed generation")
        for index, rule in enumerate(definition.rules):
            if rule.split is not None and rule.split != member.split:
                continue
            counts[index] += 1
            if isinstance(rule, RequiredMetadata) and revision.metadata.get(rule.key) is None:
                failures[index] += 1
                if len(offending[index]) < offending_limit:
                    offending[index].append(OffendingSample(revision.sample_id, revision.id))

    if member_count != expected_member_count:
        raise ValueError("Generation member count differs from the sealed generation")

    return summarize(definition, member_count, counts, failures, offending)


def summarize(
    definition: QualityDefinition,
    member_count: int,
    counts: list[int],
    failures: list[int],
    offending: list[list[OffendingSample]],
) -> Evaluation:
    """Reduce trusted exact counters from the scan or durable evaluation checkpoints."""
    results = []
    for index, rule in enumerate(definition.rules):
        count = counts[index]
        failed = failures[index]
        if isinstance(rule, MinimumSamples):
            status = "passed" if count >= rule.minimum else "failed"
        elif count == 0:
            status = "unknown"
        else:
            # Compare the authored decimal threshold exactly, including boundary cases;
            # integer counts must not round up into a passing result at large cardinalities.
            status = "passed" if rule.allows_missing(failed, count) else "failed"
        results.append(
            RuleResult(
                rule.id,
                rule.required,
                rule.split,
                status,
                count,
                failed,
                tuple(offending[index]),
                failed > len(offending[index]),
            )
        )
    return Evaluation(EVALUATOR_VERSION, member_count, tuple(results))
