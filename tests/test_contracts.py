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
