"""Conservative, recoverable artifact collection through the shared artifact guard."""

from sqlalchemy import select

from verdant.application import RegistryService
from verdant.domain import Conflict, NotFound
from verdant.identity import current_principal, require_current_access
from verdant.persistence import (
    ArtifactLocationRow,
    ArtifactRow,
    IngestionItemRow,
    PublicationGroupRow,
    RegistryRepository,
    RetentionReferenceRow,
    SampleArtifactRow,
)
from verdant.publication import ArtifactSource
from verdant.storage import ObjectMissing, S3Storage, StorageUnavailable
from verdant.telemetry import observed


class ArtifactCollector:
    def __init__(self, sessions, storage: S3Storage, *, store_id="primary"):
        self.sessions, self.storage, self.store_id = sessions, storage, store_id

    def claim(self, actor, namespace_id, artifact_id):
        with self.sessions.begin() as session:
            current_principal(session, actor.id).require(namespace_id, write=True)
            artifact = session.scalar(
                select(ArtifactRow)
                .where(ArtifactRow.id == artifact_id, ArtifactRow.namespace_id == namespace_id)
                .with_for_update()
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            if artifact is None:
                raise NotFound("Artifact not found")
            if artifact.collection_state == "deleted":
                return ()
            # No group locks here: reference creation takes group then artifact.
            protected = session.scalar(
                select(RetentionReferenceRow.artifact_id)
                .join(PublicationGroupRow, PublicationGroupRow.id == RetentionReferenceRow.group_id)
                .where(
                    RetentionReferenceRow.artifact_id == artifact_id,
                    PublicationGroupRow.state.in_(["building", "prepared", "published"]),
                )
                .limit(1)
            )
            registered = session.scalar(
                select(SampleArtifactRow.revision_id)
                .where(
                    SampleArtifactRow.namespace_id == namespace_id,
                    SampleArtifactRow.artifact_id == artifact_id,
                )
                .limit(1)
            )
            ingesting = session.scalar(
                select(IngestionItemRow.id)
                .where(
                    IngestionItemRow.namespace_id == namespace_id,
                    IngestionItemRow.artifact_id == artifact_id,
                    IngestionItemRow.state.in_(["awaiting_upload", "uploading", "ready"]),
                )
                .limit(1)
            )
            if protected is not None or registered is not None or ingesting is not None:
                raise Conflict(
                    "Artifact is protected by publication, registered data or active ingestion"
                )
            locations = session.scalars(
                select(ArtifactLocationRow)
                .where(
                    ArtifactLocationRow.namespace_id == namespace_id,
                    ArtifactLocationRow.artifact_id == artifact_id,
                )
                .order_by(ArtifactLocationRow.id)
                .limit(101)
            ).all()
            if len(locations) > 100 or any(
                location.store_id != self.store_id
                or location.bucket != self.storage.settings.bucket
                for location in locations
            ):
                raise Conflict("Collection needs a worker qualified for every recorded placement")
            newly_claimed = artifact.collection_state != "deleting"
            artifact.collection_state = "deleting"
            session.flush([artifact])
            if newly_claimed:
                RegistryService._audit(
                    RegistryRepository(session),
                    actor,
                    namespace_id,
                    artifact_id,
                    "artifact.collection_claimed",
                    {"placement_count": len(locations)},
                )
            return tuple(
                ArtifactSource(
                    artifact.id,
                    location.id,
                    artifact.sha256,
                    artifact.size_bytes,
                    location.object_key,
                )
                for location in locations
            )

    @observed("collection")
    def collect(self, actor, namespace_id, artifact_id):
        sources = self.claim(actor, namespace_id, artifact_id)
        for source in sources:
            # Both deletion and its missing-object observation are replayable. Unknown
            # provider outcomes leave the durable artifact in deleting for reconciliation.
            self.storage.delete_one(source.object_key)
            try:
                self.storage.size(source.object_key)
            except ObjectMissing:
                pass
            else:
                raise StorageUnavailable("Object deletion has not been confirmed")
        with self.sessions.begin() as session:
            artifact = session.scalar(
                select(ArtifactRow)
                .where(ArtifactRow.id == artifact_id, ArtifactRow.namespace_id == namespace_id)
                .with_for_update()
            )
            require_current_access(session, actor.id, namespace_id, write=True)
            if artifact is None:
                raise NotFound("Artifact not found")
            if artifact.collection_state == "deleted":
                return
            if artifact.collection_state != "deleting":
                raise Conflict("Artifact has no active deletion claim")
            artifact.collection_state = "deleted"
            RegistryService._audit(
                RegistryRepository(session),
                actor,
                namespace_id,
                artifact_id,
                "artifact.collection_completed",
                {},
            )
