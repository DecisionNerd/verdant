"""Shared OTel signals; durable audit remains in the use-case transaction."""

from contextlib import contextmanager
from functools import wraps
from inspect import iscoroutinefunction
from time import monotonic

from opentelemetry import context, metrics, trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("verdant.application")
meter = metrics.get_meter("verdant.application")
duration = meter.create_histogram("verdant.application.duration", unit="s")
auth_failures = meter.create_counter("verdant.auth.failures")


@contextmanager
def application_operation(operation: str):
    # Optional instrumentation must never block a domain command or expose exception payloads.
    started = monotonic()
    span = None
    token = None
    outcome = "ok"
    try:
        span = tracer.start_span(operation)
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


def observed(component: str):
    """Use the same bounded, payload-free instrumentation across sync and async boundaries."""

    def decorate(method):
        name = component + "." + method.__name__
        if iscoroutinefunction(method):

            @wraps(method)
            async def asynchronous(*args, **kwargs):
                with application_operation(name):
                    return await method(*args, **kwargs)

            return asynchronous

        @wraps(method)
        def synchronous(*args, **kwargs):
            with application_operation(name):
                return method(*args, **kwargs)

        return synchronous

    return decorate


def auth_failure(reason: str):
    try:
        auth_failures.add(1, {"reason": reason})
    except Exception:
        pass
