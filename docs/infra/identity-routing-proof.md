# Canonical issuer routing qualification

This is an isolated HTTP routing fixture for issue #11, not a Kubernetes application deployment or production TLS qualification. It does not modify the existing browser realm, Label Studio containers or Label Studio cluster. Full platform Compose/bootstrap and deployment remain #12 work.

The literal issuer used by the host, Docker verifier and Kubernetes Job is:

```text
http://auth.127.0.0.1.sslip.io:18081/realms/verdant-routing-qualification
```

Host DNS resolves the name to loopback. A proxy on the owned Docker network resolves it to the existing provider at its internal port. In the dedicated Kubernetes cluster, CoreDNS resolves the same name to a selectorless Service whose EndpointSlice reaches the provider. No token, discovery issuer or verifier URL is rewritten. The new Keycloak realm's supported `frontendUrl` attribute supplies that canonical origin; the existing browser realm retains its original issuer and subjects.

## Prerequisites

Use the owned local fixture: network `verdant-identity-qualification`, container `verdant-issue11-keycloak` on internal port 8080 and host loopback port 18081, and its persisted PostgreSQL. Public test-only credentials are in the fixture files; do not point these scripts at organizational realms. `scripts/qualify_issuer_route.py --setup` creates only the separate `verdant-routing-qualification` realm if absent. Existing-realm subjects are not recreated.

Use kind **0.33.0** with a task-local kubeconfig. The official Darwin-arm64 binary SHA256 observed was `0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f`. Kind 0.31.0 could create the cached node but could not load images with its containerd config version; do not change provider security or containerd configuration to bypass that incompatibility.

## Run

Run from the repository root. Set `TASK_KIND` to your verified kind 0.33.0 executable and `TASK_KUBECONFIG` to a dedicated new kubeconfig path. These commands create only the named test cluster and proxy; if those names already exist, verify ownership and reuse them instead of replacing them.

```sh
uv run --locked python scripts/qualify_issuer_route.py --setup

docker run -d --name verdant-issue11-routing-proxy \
  --network verdant-identity-qualification \
  --network-alias auth.127.0.0.1.sslip.io \
  --mount type=bind,src="$PWD/tests/fixtures/keycloak/route-proxy.conf",dst=/etc/nginx/conf.d/default.conf,readonly \
  nginx@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

docker build -f tests/fixtures/keycloak/RouteProbe.Dockerfile \
  -t verdant-identity-route:issue11-candidate .
docker run --rm --network verdant-identity-qualification verdant-identity-route:issue11-candidate

"$TASK_KIND" create cluster --name verdant-issue11-routing \
  --image kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d \
  --kubeconfig "$TASK_KUBECONFIG"
docker network connect verdant-identity-qualification verdant-issue11-routing-control-plane
"$TASK_KIND" load docker-image verdant-identity-route:issue11-candidate --name verdant-issue11-routing
uv run --locked python scripts/run_issuer_cluster_probe.py --kubeconfig "$TASK_KUBECONFIG"
```

The cluster script refuses any context other than `kind-verdant-issue11-routing`. It discovers the owned provider IP, applies the namespace/Service/EndpointSlice, adds only its marked CoreDNS host entry, then runs the verifier Job. It never opens the default kubeconfig. The Job has no mounted Kubernetes service-account token and uses the exact application `AccessTokenVerifier` with the committed dependency lock.

## Observed receipt

On 2026-10-27, host, Docker and Job `issuer-route-1793119931` each reported:

```json
{"issuer":"http://auth.127.0.0.1.sslip.io:18081/realms/verdant-routing-qualification","validations":20,"jwks_fetches":1,"wrong_audience":"denied"}
```

Probe image ID: `sha256:16bb390fb9e1c123988bf1af3a8ce4988ae7880a8518ca2434a67b7c41a3d920`. Its base is pinned in `RouteProbe.Dockerfile`. Kubernetes Job completed with one successful pod. This proves canonical discovery/token/JWKS routing and cached validation from a pod; it does not prove browser-to-cluster ingress, trusted HTTPS certificates, a production provider, or whole-platform recovery. Other protocol negatives run in the cryptographic/unit and live-service suite described in [identity qualification](../engineering/IDENTITY-QUALIFICATION.md).

Capture the receipt before cleanup. Jobs expire after one hour. When the test resources are no longer needed, delete only the dedicated cluster with `kind delete cluster --name verdant-issue11-routing` and remove only `verdant-issue11-routing-proxy`. Preserve the provider database and existing realm; never prune unrelated Docker or Kubernetes resources.
