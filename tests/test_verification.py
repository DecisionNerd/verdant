import hashlib
from io import BytesIO
from uuid import uuid4

import pytest

from verdant.contracts import SampleCreate
from verdant.operations import OperationRequest, OperationService
from verdant.verification import VerificationFailed, VerificationRunner


class Storage:
    def __init__(self, payload):
        self.payload, self.reads = payload, 0

    def get_object(self, **_):
        self.reads += 1
        return {"Body": BytesIO(self.payload), "ContentLength": len(self.payload)}


def setup(registry, sessions, payload=b"verified bytes"):
    registry_service, actor, ns = registry
    revision = registry_service.create_sample(
        actor,
        ns,
        SampleCreate(
            source_key=str(uuid4()),
            modality="text",
            object_key="private/fixture",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        ),
    )
    service = OperationService(sessions)
    operation = service.admit(
        actor,
        ns,
        OperationRequest(
            request_key=str(uuid4()),
            kind="verify_sample_artifact",
            inputs={"sample_id": str(revision.sample_id), "revision_id": str(revision.id)},
        ),
    )
    return service, actor, ns, operation


def test_verification_streams_bytes_and_reuses_committed_success(registry, sessions):
    payload = b"a" * (2 * 1024 * 1024 + 17)
    service, actor, ns, operation = setup(registry, sessions, payload)
    storage = Storage(payload)
    runner = VerificationRunner(sessions, storage, "test-artifacts")
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded"
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded"
    assert storage.reads == 1
    assert service.get(actor, ns, operation.id).status == "succeeded"


def test_corruption_fails_and_explicit_retry_reconciles_read(registry, sessions):
    service, actor, ns, operation = setup(registry, sessions)
    storage = Storage(b"wrong bytes")
    runner = VerificationRunner(sessions, storage, "test-artifacts")
    with pytest.raises(VerificationFailed):
        runner.run(actor, ns, operation.id, 1)
    assert service.get(actor, ns, operation.id).status == "failed"
    service.retry(actor, ns, operation.id, 1)
    storage.payload = b"verified bytes"
    assert runner.run(actor, ns, operation.id, 2).status == "succeeded"
    assert storage.reads == 2


def test_process_death_after_read_reconciles_instead_of_reissuing_a_write(
    registry, sessions, monkeypatch
):
    _, actor, ns, operation = setup(registry, sessions)
    storage = Storage(b"verified bytes")
    runner = VerificationRunner(sessions, storage, "test-artifacts")

    class ProcessDeath(BaseException):
        pass

    def die(*_, **__):
        raise ProcessDeath()

    with monkeypatch.context() as patch:
        patch.setattr(runner.operations, "complete_effect", die)
        with pytest.raises(ProcessDeath):
            runner.run(actor, ns, operation.id, 1)
    assert runner.run(actor, ns, operation.id, 1).status == "succeeded"
    assert storage.reads == 2  # Read-only replay is the declared reconciliation capability.
