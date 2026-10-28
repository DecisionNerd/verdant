# External Label Studio integration

Issue23 is in progress. The current adapter layer has transport-contract tests only; assignment persistence, trusted contributor bindings, immutable import, UI handoff and shared-issuer browser qualification are not implemented yet.

Verdant consumes the external OSS distribution and preserves its stock editor. Authentication code, release maintenance and native annotation ownership stay in that repository. Community project names are not access-control boundaries. Each configured instance is one shared-trust cohort; independent/blind audiences require separate instances, data and credentials. Export must require an explicit namespace-to-cohort admission decision before any private artifact is disclosed.

## Native transport boundary

`src/verdant/label_studio.py` accepts separate canonical HTTPS browser and machine origins and an external image manifest digest. A private registered native service token is used only on machine API requests. Requests do not follow redirects or use ambient proxy configuration. Responses are capped at one MiB and compressed responses are rejected before decompression. Native project/task/annotation identifiers must be positive integers.

Native task GET can invoke prediction evaluation. The adapter avoids that endpoint and uses the dedicated native task-annotations list, whose pinned-source GET does not evaluate predictions. It additionally requires both `evaluate_predictions_automatically` and `show_collab_predictions` explicitly false; absent flags fail closed. Assignment provisioning must set these flags false.

Task inspection fetches the project's exact label configuration, checks the assignment's expected SHA-256, fetches annotations for the assignment's native task ID and rechecks the configuration. Up to100 native annotations are accepted per task. Native annotation endpoints can return scalar or expanded `completed_by`; the adapter selects only its integer native user ID. Email and display names do not bind contributors. A callback will be a refetch hint rather than a trusted annotation payload; the domain importer must resolve native contributors through operator-verified issuer/subject bindings and append immutable revisions with durable idempotency.

The configuration observations detect schema changes around inspection, but do not create a transaction in Label Studio or prove an annotation's original authoring schema. The annotation-list read validates each annotation's task ID; it does not inspect mutable native task data or independently revalidate the task's current native project location. The assignment/import layer must retain and verify exact task/project/schema/revision pins and reject stale or unresolved results before accepting imports. Machine import identity and declared annotation author remain separate provenance fields.

## Existing pilot and deployment gate

The retained external pilot receipt identifies fork source `cc81a17bb2a077664083979dbdbb6e685228e08f` and image `localhost:5001/label-studio-oidc@sha256:d7f8aac5ddee20155a967d91927d7278d0ed5f0f99ccbf9fdb4818cf653a1cea`. A current read verified the running retained pilot uses that exact image. The external checkout has advanced, but the inspected auth/settings/native-author files match that pinned source. This is a fork interoperability pilot, not Verdant integration or production qualification.

The existing Label Studio pilot uses `https://keycloak.localhost:8443/realms/labeling`; Verdant's existing Compose profile uses an HTTP issuer in another realm. They cannot establish one individual organizational SSO session or identical issuer/subject provenance by sharing an email. The qualified fork requires HTTPS. Qualification must use an isolated shared-HTTPS-issuer deployment, preserving current services, volumes, fixture identities and pending browser evidence. No fork authentication relaxation is needed or authorized.

Service credentials belong in the private deployment secret process. No browser token, shared human login, automatic email linking or user-supplied external origin is part of this integration.

The dedicated native list excludes cancelled annotations. Missing native rows never authorize deletion of prior canonical annotations or publication evidence. The importer must append status/provenance revisions where the supported contract requires them.
