"""Managed, bounded publication I/O outside registry transactions."""

from tempfile import SpooledTemporaryFile

from verdant.domain import Conflict
from verdant.manifest import ManifestTooLarge, write_manifest
from verdant.publication import PublicationService
from verdant.storage import CHUNK_BYTES, ObjectCorrupt, ObjectMissing, S3Storage, StorageError


class PublicationRunner:
    def __init__(self, sessions, storage: S3Storage, *, store_id="primary"):
        self.storage = storage
        self.publications = PublicationService(
            sessions, bucket=storage.settings.bucket, store_id=store_id
        )

    def prepare(self, actor, namespace_id, dataset_id, group_id, command):
        epoch = None
        try:
            batch = self.publications.claim_batch(
                actor, namespace_id, dataset_id, group_id, command
            )
            if batch is None:
                return self.publications.get(actor, namespace_id, dataset_id, group_id)
            epoch = batch.epoch
            # Repeated artifacts in one bounded membership batch require one byte read.
            sources = {member.artifact.id: member.artifact for member in batch.members}
            for source in sources.values():
                self.storage.verify(source.object_key, source.digest)
            current = self.publications.accept_batch(actor, namespace_id, dataset_id, batch)
            if current.prepared_count != current.expected_count:
                return current
            work = self.publications.claim_manifest(actor, namespace_id, dataset_id, group_id)
            epoch = work.epoch
            with SpooledTemporaryFile(max_size=CHUNK_BYTES, mode="w+b") as spool:
                digest = write_manifest(
                    spool,
                    work.header,
                    self.publications.manifest_members(actor, namespace_id, dataset_id, work),
                    expected_member_count=work.expected_count,
                )
                spool.flush()
                source = self.publications.allocate_manifest(
                    actor, namespace_id, dataset_id, work, digest
                )
                self.storage.write_immutable(source.object_key, spool, digest)
                return self.publications.accept_manifest(
                    actor, namespace_id, dataset_id, work, source
                )
        except (StorageError, ManifestTooLarge, OSError) as exc:
            code = (
                "manifest_too_large"
                if isinstance(exc, ManifestTooLarge)
                else "preparation_io_unavailable"
                if isinstance(exc, OSError)
                else "artifact_missing"
                if isinstance(exc, ObjectMissing)
                else "artifact_corrupt"
                if isinstance(exc, ObjectCorrupt)
                else "storage_unavailable"
            )
            if epoch is not None:
                self.publications.record_error(
                    actor, namespace_id, dataset_id, group_id, epoch, code
                )
            reason = (
                "The manifest exceeds 256 MiB; capture a smaller candidate."
                if code == "manifest_too_large"
                else "Worker scratch is unavailable; retry when storage capacity is restored."
                if code == "preparation_io_unavailable"
                else code
            )
            raise Conflict(
                f"Preparation stopped: {reason} Committed progress and protection are retained."
            ) from None
