import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from verdant.contracts import SampleContent, SampleRevise
from verdant.persistence import make_sessions


@pytest.mark.parametrize(
    "changes",
    [
        {"size_bytes": -1},
        {"size_bytes": True},
        {"sha256": "not-sha256"},
        {"metadata": {"bad": float("nan")}},
        {"metadata": {"bad": "\x00"}},
        {"metadata": {"large": "x" * 65536}},
        {"extra": "field"},
        {"size_bytes": 2**63},
    ],
)
def test_reject_invalid_or_unbounded_sample_content(changes):
    values = {"sha256": "a" * 64, "object_key": "objects/a", "size_bytes": 1}
    with pytest.raises(ValidationError):
        SampleContent(**(values | changes))


def test_expected_revision_requires_integer_and_postgres_only():
    with pytest.raises(ValidationError):
        SampleRevise(sha256="a" * 64, object_key="a", size_bytes=1, expected_revision="1")
    with pytest.raises(ValueError, match="PostgreSQL"):
        make_sessions("sqlite://")


def test_domain_imports_only_standard_library():
    tree = ast.parse(Path("src/verdant/domain.py").read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module.split(".")[0])
    assert modules <= {"dataclasses", "uuid"}
    for name in ["application.py", "persistence.py", "contracts.py"]:
        source = Path("src/verdant", name).read_text()
        assert "from verdant.api" not in source
        assert "import fastapi" not in source


def test_mcp_operation_bounds_match_http_contract():
    import pytest
    from pydantic import TypeAdapter, ValidationError

    from verdant.mcp_server import OperationEpoch, OperationLimit

    epoch = TypeAdapter(OperationEpoch)
    limit = TypeAdapter(OperationLimit)
    for invalid in (0, -1, 9223372036854775808, True, "1", 1.0):
        with pytest.raises(ValidationError):
            epoch.validate_python(invalid)
    for invalid in (0, -1, 101):
        with pytest.raises(ValidationError):
            limit.validate_python(invalid)
    assert epoch.validate_python(1) == limit.validate_python(1) == 1


def test_interpretation_requires_real_content_and_matching_review_binding():
    from verdant.contracts import InlineEvidence, InterpretationPins, ReviewPin, TransformationPin

    annotation = InlineEvidence(format="test.annotation.v1", payload={"text": "original"})
    assert annotation.sha256 and len(annotation.sha256) == 64
    with pytest.raises(ValidationError, match="digest does not match"):
        InlineEvidence(format="test.annotation.v1", payload={"text": "original"}, sha256="f" * 64)
    with pytest.raises(ValidationError, match="exact task schema"):
        InterpretationPins(annotation_revision=annotation)
    with pytest.raises(ValidationError, match="bind the pinned annotation"):
        InterpretationPins(
            review_evidence=ReviewPin(
                annotation_digest="f" * 64, decision="accepted", rationale="input record"
            )
        )
    with pytest.raises(ValidationError, match="code, configuration and environment"):
        TransformationPin(payload={"code_digest": "f" * 64})
