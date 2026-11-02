"""Consume an exact version with current bearer identity and verified manifest bytes.

Set VERDANT_API_URL, VERDANT_ACCESS_TOKEN and optional comma-separated
VERDANT_SDK_STORAGE_ORIGINS. Pass namespace, dataset and version UUIDs.
No token, signed URL, private content or filesystem path is printed.
"""

import argparse
import os
from uuid import UUID

from verdant.sdk import Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("namespace", type=UUID)
    parser.add_argument("dataset", type=UUID)
    parser.add_argument("version", type=UUID)
    parser.add_argument("--local-loopback-http", action="store_true")
    args = parser.parse_args()
    origins = tuple(
        value.strip()
        for value in os.environ.get("VERDANT_SDK_STORAGE_ORIGINS", "").split(",")
        if value.strip()
    )
    with Client(
        os.environ["VERDANT_API_URL"],
        lambda: os.environ["VERDANT_ACCESS_TOKEN"],
        storage_origins=origins,
        allow_insecure_http=args.local_loopback_http,
    ) as sdk:
        manifest = sdk.verified_manifest(
            namespace_id=args.namespace, dataset_id=args.dataset, version_id=args.version
        )
        print(f"generation={manifest.generation_id} members={len(manifest.members)} verified=true")


if __name__ == "__main__":
    main()
