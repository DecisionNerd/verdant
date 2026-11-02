"""Public contracts keep SDK-visible shapes and immutable digest payloads coherent."""

import pytest
from fastapi import FastAPI

from verdant import annotation_contracts as a
from verdant.annotation_routes import install_annotation_routes
from verdant.annotations import normalize
from verdant.label_studio import NativeAnnotation


def test_every_json_route_exposes_a_concrete_shared_response_contract(monkeypatch):
    for name in ("BROWSER_ORIGIN", "MACHINE_ORIGIN", "IMAGE", "TOKEN"):
        monkeypatch.delenv("VERDANT_LS_" + name, raising=False)
    app = FastAPI()
    annotations, truth = install_annotation_routes(app, object(), lambda: None)
    assert annotations.sessions is truth.sessions
    routes = [route for route in app.routes if route.path.startswith("/api/")]
    assert len(routes) == 13
    assert len({route.name for route in routes}) == len(routes)
    for route in routes:
        assert route.response_model is not None
        assert route.response_model is not a.Evidence
        assert route.response_model_exclude_unset
    assert (
        app.openapi()["components"]["schemas"]["AssignmentDetailResponse"]["properties"]["latest"][
            "maxItems"
        ]
        == 1000
    )


@pytest.mark.parametrize("field,value", [("unexpected", "x"), ("id", 10), ("origin", [])])
def test_native_result_must_match_public_original_contract_before_persistence(field, value):
    result = {
        "from_name": "label",
        "to_name": "text",
        "type": "choices",
        "value": {"choices": ["positive"]},
        field: value,
    }
    native = NativeAnnotation(id=1, task=1, completed_by=1, result=[result], was_cancelled=False)
    schema = a.CategoricalSchema(
        labels=["positive", "negative"], instructions="Choose", instruction_revision="v1"
    )
    from verdant.domain import Conflict

    with pytest.raises(Conflict, match="supported contract"):
        normalize(native, schema)
