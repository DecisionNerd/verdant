"""Typed lifecycle helpers over generated operations, without a second domain layer."""

import io
import time
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from verdant.manifest import ManifestHeader, ManifestMember
from verdant.operations import OperationDetail
from verdant.sdk.generated import GeneratedClient
from verdant.sdk.transport import SDKError


class VerifiedManifest(ManifestHeader):
    schema_name: Literal["verdant.manifest.v1"] = Field(alias="schema")
    members: list[ManifestMember]


class Client(GeneratedClient):
    def wait_operation(
        self, *, namespace_id: UUID, operation_id: UUID, polls: int = 30, interval: float = 1
    ) -> OperationDetail:
        """Return a committed terminal/paused/ambiguous state, never infer success.

        Exhaustion is explicit and does not cancel or retry a command. Callers
        inspect the returned operation status before consuming an outcome.
        """
        if type(polls) is not int or not 1 <= polls <= 1000 or not 0 <= interval <= 10:
            raise ValueError("Use a finite polling budget and interval of at most ten seconds")
        for index in range(polls):
            state = self.operation_detail(namespace_id=namespace_id, operation_id=operation_id)
            if state.needs_reconciliation or state.operation.status in {
                "succeeded",
                "failed",
                "cancelled",
                "paused",
            }:
                return state
            if index + 1 < polls:
                time.sleep(interval)
        raise SDKError("Operation is still pending; polling budget exhausted without mutation")

    def verified_manifest(
        self, *, namespace_id: UUID, dataset_id: UUID, version_id: UUID
    ) -> VerifiedManifest:
        """Read a fully typed manifest up to 16 MiB; larger artifacts can stream.

        Compare both the descriptor's bytes and the exact version's logical IDs.
        No provider URL, server-local path or storage endpoint enters this model.
        """
        version = self.get_version(
            namespace_id=namespace_id, dataset_id=dataset_id, version_id=version_id
        )
        descriptor = self.version_manifest(
            namespace_id=namespace_id, dataset_id=dataset_id, version_id=version_id
        )
        if (
            descriptor.artifact_id != version.manifest_artifact_id
            or descriptor.sha256 != version.manifest_digest
            or descriptor.size_bytes != version.manifest_size
        ):
            raise SDKError("Manifest descriptor differs from the immutable version")
        if descriptor.size_bytes > 16 * 1024 * 1024:
            raise SDKError("Typed manifest reader is limited to 16 MiB; use download_verified")
        output = io.BytesIO()
        self.download_verified(descriptor, output)
        try:
            result = VerifiedManifest.model_validate_json(output.getvalue())
        except (ValidationError, ValueError):
            raise SDKError("Verified bytes do not match the supported manifest schema") from None
        if (
            result.namespace_id != namespace_id
            or result.dataset_id != dataset_id
            or result.candidate_id != version.candidate_id
            or result.generation_id != version.generation_id
        ):
            raise SDKError("Manifest identity differs from the requested immutable version")
        ids = [member.sample_id for member in result.members]
        if any(left >= right for left, right in zip(ids, ids[1:])):
            raise SDKError("Manifest membership is duplicated or out of order")
        # Canonical manifest encoding contains no endpoint or signed capability;
        # contracts retain logical artifact IDs/digests and bounded user metadata.
        return result
