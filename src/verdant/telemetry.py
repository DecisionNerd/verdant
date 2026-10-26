"""Shared OTel signals; durable audit remains in the use-case transaction."""

from contextlib import contextmanager
from time import monotonic

from opentelemetry import context, metrics, trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("verdant.registry")
meter = metrics.get_meter("verdant.registry")
duration = meter.create_histogram("verdant.registry.duration", unit="s")


@contextmanager
def registry_operation(operation: str):
    # Optional instrumentation must never block a domain command or expose exception payloads.
    started = monotonic()
    span = None
    token = None
    outcome = "ok"
    try:
        span = tracer.start_span("registry." + operation)
        token = context.attach(trace.set_span_in_context(span))
    except Exception:
        pass
    try:
        yield span
    except Exception as exc:
        outcome = type(exc).__name__
        if span is not None:
            try:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", outcome)
            except Exception:
                pass
        raise
    finally:
        try:
            if token is not None:
                context.detach(token)
            if span is not None:
                span.end()
        except Exception:
            pass
        try:
            duration.record(monotonic() - started, {"operation": operation, "outcome": outcome})
        except Exception:
            pass
