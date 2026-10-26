import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from verdant import telemetry
from verdant.contracts import DatasetCreate
from verdant.domain import Conflict


def test_use_case_spans_include_parent_and_safe_failure(registry, monkeypatch):
    service, actor, ns = registry
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(telemetry, "tracer", tracer)
    command = DatasetCreate(slug="signals", name="Private sample name", modality="image")
    with tracer.start_as_current_span("caller") as parent:
        service.create_dataset(actor, ns, command)
        with pytest.raises(Conflict):
            service.create_dataset(actor, ns, command)
        parent_id = parent.get_span_context().span_id
    spans = exporter.get_finished_spans()
    children = [s for s in spans if s.name == "registry.create_dataset"]
    assert len(children) == 2
    assert all(s.parent.span_id == parent_id for s in children)
    assert children[0].status.status_code == StatusCode.UNSET
    assert children[1].status.status_code == StatusCode.ERROR
    assert children[1].attributes["error.type"] == "Conflict"
    assert all(not s.events for s in children)
    assert "Private sample name" not in str([dict(s.attributes) for s in spans])
    assert not trace.get_current_span().get_span_context().is_valid
    provider.shutdown()


def test_optional_telemetry_failure_does_not_prevent_commit(registry, monkeypatch):
    service, actor, ns = registry

    class BrokenSignals:
        def start_span(self, *args, **kwargs):
            raise RuntimeError("collector unavailable")

        def record(self, *args, **kwargs):
            raise RuntimeError("collector unavailable")

    monkeypatch.setattr(telemetry, "tracer", BrokenSignals())
    monkeypatch.setattr(telemetry, "duration", BrokenSignals())
    created = service.create_dataset(
        actor,
        ns,
        DatasetCreate(
            slug="outage",
            name="Outage",
            modality="image",
        ),
    )
    assert service.get_dataset(actor, ns, created.id) == created
