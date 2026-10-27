"""Quiescent full-/data backup/restore proof for ONLY the named local object fixture."""

import argparse
import json
import subprocess
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--receipt", default="/tmp/verdant-issue12-storage-receipt.json")
args = parser.parse_args()
image = (
    "chrislusf/seaweedfs@sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8"
)
api_image = "verdant-platform-api:issue12-candidate"
original = "verdant-issue12-objects"
restore = "verdant-issue12-objects-restore"
suffix = uuid4().hex[:8]
backup_volume = "verdant-issue12-backup-" + suffix
restore_volume = "verdant-issue12-restore-" + suffix


def docker(*parts):
    return subprocess.check_output(["docker", *parts], text=True).strip()


metadata = json.loads(docker("inspect", original))[0]
assert metadata["Config"]["Image"] == image and metadata["State"]["Running"]
assert metadata["Config"]["Cmd"][0] == "mini"
source = next(m for m in metadata["Mounts"] if m["Destination"] == "/data")
assert source["Type"] == "volume" and source["Name"] == "verdant-issue12-objects-data"
credential = next(
    m for m in metadata["Mounts"] if m["Destination"] == "/run/secrets/objects-s3.json"
)
assert not credential["RW"]
docker("volume", "create", backup_volume)
docker("volume", "create", restore_volume)
docker("stop", "--timeout", "30", original)
clone_started = False
try:
    docker(
        "run",
        "--rm",
        "--user",
        "0",
        "--entrypoint",
        "tar",
        "--mount",
        f"type=volume,src={source['Name']},dst=/source,readonly",
        "--mount",
        f"type=volume,src={backup_volume},dst=/backup",
        api_image,
        "-czf",
        "/backup/data.tgz",
        "-C",
        "/source",
        ".",
    )
    docker(
        "run",
        "--rm",
        "--user",
        "0",
        "--entrypoint",
        "tar",
        "--mount",
        f"type=volume,src={backup_volume},dst=/backup,readonly",
        "--mount",
        f"type=volume,src={restore_volume},dst=/restore",
        api_image,
        "-xzf",
        "/backup/data.tgz",
        "-C",
        "/restore",
    )
    docker(
        "run",
        "-d",
        "--name",
        restore,
        "--network",
        "verdant-identity-qualification",
        "--network-alias",
        "objects",
        "-p",
        "127.0.0.1:18334:8333",
        "--mount",
        f"type=volume,src={restore_volume},dst=/data",
        "--mount",
        f"type=bind,src={credential['Source']},dst=/run/secrets/objects-s3.json,readonly",
        image,
        *metadata["Config"]["Cmd"],
    )
    clone_started = True
    subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "python",
            "scripts/qualify_object_store.py",
            "--endpoint",
            "http://127.0.0.1:18334",
            "--receipt",
            args.receipt,
            "--verify-restart",
        ],
        check=True,
    )
finally:
    if clone_started:
        docker("stop", "--timeout", "30", restore)
        docker("rm", restore)
    docker("start", original)
subprocess.run(
    [
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/qualify_object_store.py",
        "--receipt",
        args.receipt,
        "--verify-restart",
    ],
    check=True,
)
print(
    json.dumps(
        {
            "backup_volume": backup_volume,
            "restore_volume": restore_volume,
            "result": "restored bytes, SHA-256 metadata, CORS and private access verified; "
            "original recovered",
        }
    )
)
