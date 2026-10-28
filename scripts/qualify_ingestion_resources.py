"""Measure actual maximum-size promotion RSS and scratch on the local S3 fixture.

Transport-only proof: random owned keys have no registry references. No platform
service, credential, bucket, shared worker or preexisting object is modified.
"""

import hashlib
import json
import os
import resource
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import verdant.storage as module
from verdant.storage import CHUNK_BYTES, MAX_ARTIFACT_BYTES, ObjectDigest, S3Settings, S3Storage


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main():
    if os.environ.get("VERDANT_STORAGE_TEST_ENDPOINT") != "http://127.0.0.1:18333":
        raise RuntimeError("Resource qualifier requires the fixed isolated local S3 fixture")
    if os.environ.get("VERDANT_STORAGE_TEST_BUCKET") != "verdant-artifacts":
        raise RuntimeError("Resource qualifier requires its explicit fixture bucket")
    settings = S3Settings(
        bucket="verdant-artifacts",
        endpoint="http://127.0.0.1:18333",
        browser_endpoint="http://127.0.0.1:18333",
        allow_insecure_http=True,
    )
    adapter = S3Storage(settings)
    prefix = "qualification/issue14/resources/" + uuid4().hex + "/"
    keys = [prefix + "staging", prefix + "final"]
    records = []
    original = module.SpooledTemporaryFile

    class MeasuredSpool(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.peak_bytes = 0
            records.append(self)

        def write(self, data):
            result = super().write(data)
            self.peak_bytes = max(self.peak_bytes, self.tell())
            return result

    try:
        with tempfile.TemporaryDirectory(prefix="verdant-issue14-resources-") as directory:
            source = Path(directory) / "source"
            digest = hashlib.sha256()
            chunk = b"r" * CHUNK_BYTES
            with source.open("wb") as stream:
                for _ in range(MAX_ARTIFACT_BYTES // len(chunk)):
                    stream.write(chunk)
                    digest.update(chunk)
            expected = ObjectDigest(digest.hexdigest(), MAX_ARTIFACT_BYTES)
            with source.open("rb") as stream:
                adapter.client.put_object(
                    Bucket=settings.bucket,
                    Key=keys[0],
                    Body=stream,
                    ContentLength=expected.size_bytes,
                )
            source.unlink()
            baseline = rss_bytes()
            previous_tempdir = tempfile.tempdir
            tempfile.tempdir = directory
            module.SpooledTemporaryFile = MeasuredSpool
            try:
                adapter.promote(keys[0], keys[1], expected)
            finally:
                module.SpooledTemporaryFile = original
                tempfile.tempdir = previous_tempdir
            peak = rss_bytes()
            assert len(records) == 1
            spool = records[0]
            assert spool.peak_bytes == MAX_ARTIFACT_BYTES and spool._rolled and spool.closed
            assert not list(Path(directory).iterdir())
            assert peak < 192 * 1024 * 1024, "Promotion process exceeded qualified RSS bound"
            adapter.verify(keys[1], expected)
            print(
                json.dumps(
                    {
                        "artifact_size_bytes": expected.size_bytes,
                        "artifact_sha256": expected.sha256,
                        "platform": sys.platform,
                        "process_baseline_high_water_rss_bytes": baseline,
                        "process_peak_rss_bytes": peak,
                        "rss_bound_bytes": 192 * 1024 * 1024,
                        "spool_count": len(records),
                        "scratch_peak_logical_bytes": spool.peak_bytes,
                        "spool_rolled_to_disk": True,
                        "spool_closed": True,
                        "scratch_directory_empty": True,
                        "final_digest_verified": True,
                        "limits": [
                            "Single serial adapter promotion, not aggregate worker concurrency",
                            "RSS high-water includes interpreter, SDK and fixture streaming",
                            "Logical scratch size, not filesystem physical reclamation",
                            "Not browser memory, API batch memory or disk-exhaustion recovery",
                        ],
                    }
                )
            )
    finally:
        module.SpooledTemporaryFile = original
        try:
            adapter.delete(keys)
        finally:
            adapter.close()


if __name__ == "__main__":
    main()
