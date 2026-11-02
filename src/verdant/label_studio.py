"""Bounded native Label Studio transport; canonical ownership stays in Verdant.

A callback is a notification, never authoritative annotation or contributor data.
The consuming service fetches native records and resolves native contributor IDs
through trusted issuer/subject bindings before accepting immutable revisions.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import Field, JsonValue, ValidationError

from verdant.contracts import Contract

MAX_RESPONSE_BYTES = 1024 * 1024


class LabelStudioUnavailable(Exception):
    """Safe integration failure without credentials, object URLs or provider payloads."""


class LabelStudioConflict(Exception):
    """Provider state differs from the assignment's pinned task or schema."""


@dataclass(frozen=True)
class LabelStudioSettings:
    browser_origin: str
    machine_origin: str
    image: str

    def __post_init__(self):
        for value in (self.browser_origin, self.machine_origin):
            parsed = urlsplit(value)
            try:
                canonical = str(httpx.URL(value)).rstrip("/")
                parsed.port  # Validate the port before accepting an integration origin.
            except (httpx.InvalidURL, ValueError):
                raise ValueError("Label Studio requires canonical HTTPS origins") from None
            if (
                canonical != value
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "\\" in value
                or "%" in parsed.netloc
                or any(character.isspace() for character in value)
            ):
                raise ValueError("Label Studio requires canonical HTTPS origins")
        if self.browser_origin == self.machine_origin:
            raise ValueError("Label Studio requires separate browser and machine origins")
        if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("Label Studio requires a digest-pinned external image")

    def task_url(self, project_id: int, task_id: int):
        positive_id(project_id)
        positive_id(task_id)
        return f"{self.browser_origin}/projects/{project_id}/data?task={task_id}"


def positive_id(value):
    if type(value) is not int or value < 1:
        raise ValueError("Native Label Studio IDs must be positive integers")


class NativeAnnotation(Contract):
    id: int = Field(gt=0, le=9223372036854775807, strict=True)
    task: int = Field(gt=0, le=9223372036854775807, strict=True)
    completed_by: int = Field(gt=0, le=9223372036854775807, strict=True)
    updated_by: int | None = Field(default=None, gt=0, le=9223372036854775807, strict=True)
    result: list[dict[str, JsonValue]] = Field(max_length=1000)
    was_cancelled: bool = Field(strict=True)
    # Native exports contain other provider metadata; accept only fields selected
    # explicitly below, rather than allowing arbitrary extras into canonical state.


class LabelStudioClient:
    def __init__(self, settings, token, *, transport=None, verify=True):
        if not token or any(character.isspace() for character in token):
            raise ValueError("A private native service token is required")
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.machine_origin,
            headers={"Authorization": f"Token {token}", "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(10, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=verify,
        )

    def close(self):
        self.http.close()

    def _get(self, path):
        try:
            with self.http.stream("GET", path) as response:
                if response.status_code != 200:
                    raise LabelStudioUnavailable("Labeling service request unavailable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise LabelStudioUnavailable("Labeling service returned unsupported encoding")
                data = bytearray()
                for chunk in response.iter_raw(chunk_size=65536):
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise LabelStudioUnavailable("Labeling service response exceeds bounds")
                    data.extend(chunk)
            value = json.loads(data)
            if not isinstance(value, (dict, list)):
                raise ValueError
            return value
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
            raise LabelStudioUnavailable("Labeling service response unavailable") from None

    def project_schema(self, project_id):
        positive_id(project_id)
        project = self._get(f"/api/projects/{project_id}/")
        if (
            not isinstance(project, dict)
            or type(project.get("id")) is not int
            or project["id"] != project_id
        ):
            raise LabelStudioConflict("Native project identity changed")
        # Native TaskAPI.get can evaluate predictions. Keep this integration
        # model-call-free; absent flags are unknown, not permission to evaluate.
        if any(
            project.get(name) is not False
            for name in ("evaluate_predictions_automatically", "show_collab_predictions")
        ):
            raise LabelStudioConflict("Automatic native predictions must be explicitly disabled")
        schema = project.get("label_config")
        if not isinstance(schema, str) or not schema or len(schema.encode()) > 65536:
            raise LabelStudioConflict("Native project schema is invalid or exceeds bounds")
        return schema

    def task_annotations(self, project_id, task_id, schema_sha256):
        positive_id(project_id)
        positive_id(task_id)
        before = self.project_schema(project_id)
        if hashlib.sha256(before.encode()).hexdigest() != schema_sha256:
            raise LabelStudioConflict("Native project schema differs from the assignment")
        # Dedicated annotation listing has no TaskAPI.get prediction side effect.
        # The domain assignment owns the native task/project association. This read
        # validates annotation.task, not mutable native task data or project location.
        rows = self._get(f"/api/tasks/{task_id}/annotations/")
        if isinstance(rows, dict):
            if (
                "next" not in rows
                or rows["next"] is not None
                or rows.get("previous") is not None
                or type(rows.get("count")) is not int
                or not isinstance(rows.get("results"), list)
                or rows["count"] != len(rows["results"])
            ):
                raise LabelStudioConflict("Native annotations exceed a complete bounded page")
            rows = rows.get("results")
        if not isinstance(rows, list) or len(rows) > 100:
            raise LabelStudioConflict("Native annotation count is invalid or exceeds bounds")
        annotations = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                selected = {
                    name: row[name] for name in NativeAnnotation.model_fields if name in row
                }
                # Native TaskAPI expands annotations.completed_by. Only its native
                # integer ID is provenance input; email/display fields never bind identity.
                if isinstance(selected["completed_by"], dict):
                    selected["completed_by"] = selected["completed_by"].get("id")
                if isinstance(selected.get("updated_by"), dict):
                    selected["updated_by"] = selected["updated_by"]["id"]
                annotation = NativeAnnotation.model_validate(selected)
                if annotation.task != task_id:
                    raise ValueError
                annotations.append(annotation)
        except (KeyError, ValueError, ValidationError):
            raise LabelStudioConflict("Native annotation identity or result is invalid") from None
        if len({annotation.id for annotation in annotations}) != len(annotations):
            raise LabelStudioConflict("Native annotation IDs are duplicated")
        if self.project_schema(project_id) != before:
            raise LabelStudioConflict("Native project schema changed during inspection")
        return annotations

    def task_data(self, project_id, task_id):
        """Export is read-only; never use TaskAPI.get, which can evaluate predictions.

        Annotation provenance is fetched separately through the dedicated listing.
        This deliberately supports bounded projects (100 tasks / 1 MiB export).
        """
        positive_id(project_id)
        positive_id(task_id)
        rows = self._get(
            f"/api/projects/{project_id}/export?exportType=JSON&download_all_tasks=true"
        )
        if not isinstance(rows, list) or len(rows) > 100:
            raise LabelStudioConflict("Native project export exceeds 100 tasks")
        found = [row for row in rows if isinstance(row, dict) and row.get("id") == task_id]
        if len(found) != 1 or type(found[0].get("id")) is not int:
            raise LabelStudioConflict("Native task is absent from the pinned project")
        data = found[0].get("data")
        if not isinstance(data, dict):
            raise LabelStudioConflict("Native task data is invalid")
        return data
