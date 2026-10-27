"""Route-only proof in the dedicated kind-verdant-issue11-routing cluster."""

import argparse
import ipaddress
import json
import re
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--kubeconfig", required=True)
args = parser.parse_args()
base = ["kubectl", "--kubeconfig", args.kubeconfig]
context = subprocess.check_output(base + ["config", "current-context"], text=True).strip()
if context != "kind-verdant-issue11-routing":
    raise RuntimeError("Refusing to change an unrelated Kubernetes context")


def kubectl(*parts, document=None):
    result = subprocess.run(
        base + list(parts),
        input=json.dumps(document) if document else None,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def apply(document):
    return kubectl("apply", "-f", "-", document=document)


network = json.loads(
    subprocess.check_output(
        [
            "docker",
            "network",
            "inspect",
            "verdant-identity-qualification",
        ],
        text=True,
    )
)[0]
provider = next(
    row for row in network["Containers"].values() if row["Name"] == "verdant-issue11-keycloak"
)
provider_ip = str(ipaddress.ip_interface(provider["IPv4Address"]).ip)
namespace = "verdant-issue11"
apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
apply(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "keycloak-routing", "namespace": namespace},
        "spec": {"ports": [{"name": "http", "port": 18081, "targetPort": 8080}]},
    }
)
apply(
    {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": {
            "name": "keycloak-routing",
            "namespace": namespace,
            "labels": {
                "kubernetes.io/service-name": "keycloak-routing",
                "endpointslice.kubernetes.io/managed-by": "verdant-issue11-proof",
            },
        },
        "addressType": "IPv4",
        "ports": [{"name": "http", "protocol": "TCP", "port": 8080}],
        "endpoints": [{"addresses": [provider_ip], "conditions": {"ready": True}}],
    }
)
service = json.loads(kubectl("-n", namespace, "get", "service", "keycloak-routing", "-o", "json"))
cluster_ip = str(ipaddress.ip_address(service["spec"]["clusterIP"]))
config = json.loads(kubectl("-n", "kube-system", "get", "configmap", "coredns", "-o", "json"))
corefile = config["data"]["Corefile"]
corefile = re.sub(
    r"    # verdant-issue11-routing\n    hosts \{.*?    \}\n", "", corefile, flags=re.S
)
marker = "    kubernetes cluster.local"
if corefile.count(marker) != 1:
    raise RuntimeError("Unexpected owned CoreDNS configuration")
hosts = (
    "    # verdant-issue11-routing\n    hosts {\n"
    f"        {cluster_ip} auth.127.0.0.1.sslip.io\n"
    "        ttl 30\n        fallthrough\n    }\n"
)
corefile = corefile.replace(marker, hosts + marker)
kubectl(
    "-n",
    "kube-system",
    "patch",
    "configmap",
    "coredns",
    "--type",
    "merge",
    "-p",
    json.dumps({"data": {"Corefile": corefile}}),
)
kubectl("-n", "kube-system", "rollout", "restart", "deployment/coredns")
kubectl("-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=60s")
name = "issuer-route-" + str(int(time.time()))
apply(
    {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "verifier",
                            "image": "verdant-identity-route:issue11-candidate",
                            "imagePullPolicy": "Never",
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "1", "memory": "256Mi"},
                            },
                        }
                    ],
                }
            },
        },
    }
)
print("Owned routing Job started: " + name, flush=True)
try:
    print(
        kubectl(
            "-n", namespace, "wait", "--for=condition=complete", "job/" + name, "--timeout=180s"
        ),
        end="",
    )
finally:
    print(kubectl("-n", namespace, "logs", "job/" + name), end="")
