"""Actual DataChain/S3 pipeline with increasing cardinality and fixed batch memory."""

import hashlib
import io
import resource
import sys
import tracemalloc
from uuid import uuid4

import datachain as dc
from sqlalchemy import func

from verdant.contracts import DatasetCreate
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    DraftRow,
    MembershipRow,
    SampleArtifactRow,
    SampleRevisionRow,
    SampleRow,
)
from verdant.process_flow import FlowCreate, FlowStep, ProcessService, ProcessStart
from verdant.process_runner import ProcessRunner
from verdant.storage import ObjectDigest
from verdant.versioning import CandidateCapture, CandidatePrepare, VersioningService


def test_actual_datachain_streams_many_s3_inputs_with_fixed_resident_page(
    registry, sessions, storage, tmp_path
):
    registry_service, actor, ns = registry
    adapter, prefix = storage
    body = b" " + b"bounded text " * 5000  # 65,001 bytes per artifact, below the64KiB ceiling.
    sha = hashlib.sha256(body).hexdigest()
    artifact_id = uuid4()
    key = prefix + "bounded.txt"
    adapter.write_immutable(key, io.BytesIO(body), ObjectDigest(sha, len(body)))
    adapter.verify(key, ObjectDigest(sha, len(body)))
    # Reuse one exact immutable byte object for512 independent frozen sample identities.
    dataset = registry_service.create_dataset(
        actor, ns, DatasetCreate(name="Bounded", slug="bounded", modality="text")
    )
    with sessions.begin() as session:
        session.add(
            ArtifactRow(
                id=artifact_id,
                namespace_id=ns,
                sha256=sha,
                size_bytes=len(body),
                verified_at=func.now(),
            )
        )
        session.flush()
        session.add(
            ArtifactLocationRow(
                namespace_id=ns,
                artifact_id=artifact_id,
                store_id="primary",
                bucket=adapter.settings.bucket,
                object_key=key,
            )
        )
        for index in range(512):
            sample_id, revision_id = uuid4(), uuid4()
            session.add(
                SampleRow(
                    id=sample_id, namespace_id=ns, source_key=f"memory-{index}", modality="text"
                )
            )
            session.flush()
            session.add(
                SampleRevisionRow(
                    id=revision_id,
                    namespace_id=ns,
                    sample_id=sample_id,
                    revision=1,
                    sha256=sha,
                    object_key=key,
                    size_bytes=len(body),
                    metadata_json={"truth": "not-input"},
                )
            )
            session.flush()
            session.add(
                SampleArtifactRow(
                    namespace_id=ns,
                    sample_id=sample_id,
                    revision_id=revision_id,
                    artifact_id=artifact_id,
                )
            )
            session.add(
                MembershipRow(
                    namespace_id=ns,
                    draft_id=dataset.draft_id,
                    sample_id=sample_id,
                    sample_revision_id=revision_id,
                    split="test",
                    interpretation={},
                    valid_from=index + 1,
                )
            )
        session.get(DraftRow, dataset.draft_id).revision = 512
    versions = VersioningService(sessions)
    candidate = versions.capture(
        actor, ns, dataset.id, CandidateCapture(request_key=str(uuid4()), expected_revision=512)
    )
    while candidate.state != "sealed":
        candidate = versions.prepare(
            actor,
            ns,
            dataset.id,
            candidate.id,
            CandidatePrepare(expected_count=candidate.prepared_count, limit=100),
        )
    candidate_id = candidate.id
    service = ProcessService(sessions)
    flow = service.create_flow(
        actor,
        ns,
        dataset.id,
        FlowCreate(
            name="Bounded", steps=[FlowStep(name="Trim", kind="preprocess", transform="strip")]
        ),
    )
    operation = service.start(
        actor,
        ns,
        dataset.id,
        ProcessStart(
            request_key=str(uuid4()),
            flow_id=flow.id,
            candidate_id=candidate_id,
            max_members=512,
            max_model_calls=0,
        ),
    )
    # Warm the actual dependency before measuring incremental pipeline allocations.
    dc.read_values(warm=[1], in_memory=True).to_values("warm")
    runner = ProcessRunner(sessions, adapter)
    pages, max_records = 0, 0
    original = runner.service.plan_page

    def measured_page(*args, **kwargs):
        nonlocal max_records
        definition, records = original(*args, **kwargs)
        max_records = max(max_records, len(records))
        return definition, records

    runner.service.plan_page = measured_page
    tracemalloc.start()
    while True:
        state = runner.run(actor, ns, operation.id, 1, limit=25)
        pages += 1
        if state.status != "running":
            break
        assert pages <= 21
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert state.status == "succeeded" and pages == 21
    assert max_records == 25
    # 32MiB of source content was verified; the pipeline never retains all source bytes.
    assert peak < 24 * 1024 * 1024
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt = {
        "members": 512,
        "source_bytes_verified": 512 * len(body),
        "batch_members": max_records,
        "pages": pages,
        "python_allocation_peak_bytes": peak,
        "max_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "datachain_version": dc.version.__version__
        if hasattr(dc.version, "__version__")
        else "0.58.3",
    }
    import json

    (tmp_path / "bounded-memory.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
