"""Qualification for the owned SeaweedFS fixture; all credentials and payloads are test-only."""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

parser = argparse.ArgumentParser()
parser.add_argument("--endpoint", default="http://127.0.0.1:18333")
parser.add_argument("--receipt", default="/tmp/verdant-issue12-storage-receipt.json")
parser.add_argument("--verify-restart", action="store_true")
parser.add_argument("--browser-config")
parser.add_argument("--origin", default="http://127.0.0.1:18787")
args = parser.parse_args()
if urlsplit(args.endpoint).hostname not in {"127.0.0.1", "localhost", "objects"}:
    raise RuntimeError("This public-credential fixture only supports its local endpoints")
config = Config(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 1},
)


def client(key, secret):
    return boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        region_name="us-east-1",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=config,
    )


admin = client("verdant-test-admin", "verdant-test-only-admin-secret")
registry = client("verdant-test-registry", "verdant-test-only-registry-secret")
other_owner = client("verdant-test-other", "verdant-test-only-other-secret")
bucket = "verdant-artifacts"

# Container start is not S3 readiness.
# Retry only bounded startup/network failures; never reset data.
deadline = time.monotonic() + 30
while True:
    try:
        httpx.get(args.endpoint + "/healthz", timeout=1).raise_for_status()
        admin.list_buckets()
        break
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 503:
            raise
    except (
        httpx.HTTPError,
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ReadTimeoutError,
    ):
        pass
    if time.monotonic() >= deadline:
        raise RuntimeError("Object service did not become ready within 30 seconds")
    time.sleep(0.25)
if args.browser_config:
    if urlsplit(args.origin).hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Browser proof requires its explicit loopback origin")
    receipt = json.loads(Path(args.receipt).read_text())
    rules = admin.get_bucket_cors(Bucket=bucket)["CORSRules"]
    for rule in rules:
        if args.origin not in rule["AllowedOrigins"]:
            rule["AllowedOrigins"].append(args.origin)
    admin.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
    receipt["cors"] = admin.get_bucket_cors(Bucket=bucket)["CORSRules"]
    Path(args.receipt).write_text(json.dumps(receipt) + "\n")
    key = "qualification/" + uuid4().hex + "/browser"
    body = "Browser storage qualification\n"
    params = {"Bucket": bucket, "Key": key}
    config = {
        "put_url": registry.generate_presigned_url(
            "put_object", Params=params | {"ContentType": "application/octet-stream"}, ExpiresIn=120
        ),
        "get_url": registry.generate_presigned_url("get_object", Params=params, ExpiresIn=120),
        "body": body,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    Path(args.browser_config).write_text(json.dumps(config) + "\n")
    print("Browser fixture prepared with 120-second scoped URLs; no credentials exported")
    raise SystemExit(0)

if args.verify_restart:
    receipt = json.loads(Path(args.receipt).read_text())
    assert receipt["bucket"] == bucket and receipt["key"].startswith("qualification/")
    result = registry.get_object(Bucket=bucket, Key=receipt["key"])
    assert hashlib.sha256(result["Body"].read()).hexdigest() == receipt["sha256"]
    assert result["Metadata"]["sha256"] == receipt["sha256"]
    assert admin.get_bucket_cors(Bucket=bucket)["CORSRules"] == receipt["cors"]
    assert (
        httpx.get(args.endpoint + "/" + bucket + "/" + receipt["key"], timeout=5).status_code == 403
    )
    print("Object bytes, private access and bucket CORS metadata persisted across restart")
    raise SystemExit(0)

for name in [bucket, "verdant-other"]:
    try:
        admin.create_bucket(Bucket=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise
admin.put_bucket_cors(
    Bucket=bucket,
    CORSConfiguration={
        "CORSRules": [
            {
                "AllowedOrigins": ["http://127.0.0.1:18787"],
                "AllowedMethods": ["GET", "PUT", "HEAD"],
                "AllowedHeaders": ["content-type", "x-amz-*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 60,
            }
        ]
    },
)
prefix = "qualification/" + uuid4().hex + "/"
body = b"private object qualification\n"
key = prefix + "persistent"
registry.put_object(
    Bucket=bucket, Key=key, Body=body, Metadata={"sha256": hashlib.sha256(body).hexdigest()}
)
head = registry.head_object(Bucket=bucket, Key=key)
assert head["ContentLength"] == len(body)
assert head["Metadata"]["sha256"] == hashlib.sha256(body).hexdigest()
assert registry.get_object(Bucket=bucket, Key=key)["Body"].read() == body
assert registry.get_object(Bucket=bucket, Key=key, Range="bytes=2-7")["Body"].read() == body[2:8]
for path in ["/" + bucket, "/" + bucket + "/" + key]:
    assert httpx.get(args.endpoint + path, timeout=5).status_code == 403
assert httpx.head(args.endpoint + "/" + bucket + "/" + key, timeout=5).status_code == 403
assert (
    httpx.put(
        args.endpoint + "/" + bucket + "/" + prefix + "unsigned", content=b"unsigned", timeout=5
    ).status_code
    == 403
)
other_key = prefix + "other-owner"
other_owner.put_object(Bucket="verdant-other", Key=other_key, Body=b"other-private")
assert (
    other_owner.get_object(Bucket="verdant-other", Key=other_key)["Body"].read() == b"other-private"
)
for denied_client, denied_bucket, denied_key in [
    (registry, "verdant-other", other_key),
    (other_owner, bucket, key),
]:
    try:
        denied_client.get_object(Bucket=denied_bucket, Key=denied_key)
    except ClientError as exc:
        assert exc.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    else:
        raise AssertionError("Cross-owner object read was allowed")
other_owner.delete_object(Bucket="verdant-other", Key=other_key)
try:
    registry.list_objects_v2(Bucket="verdant-other")
except ClientError as exc:
    assert exc.response["ResponseMetadata"]["HTTPStatusCode"] == 403
else:
    raise AssertionError("Cross-owner bucket listing was allowed")

barrier = Barrier(2)
conditional = prefix + "conditional"


def create_once(index):
    barrier.wait(10)
    try:
        registry.put_object(
            Bucket=bucket, Key=conditional, Body=str(index).encode(), IfNoneMatch="*"
        )
        return index, 200
    except ClientError as exc:
        return index, exc.response["ResponseMetadata"]["HTTPStatusCode"]


with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(create_once, [1, 2]))
assert sorted(status for _, status in results) == [200, 412]
winner = next(index for index, status in results if status == 200)
assert registry.get_object(Bucket=bucket, Key=conditional)["Body"].read() == str(winner).encode()

for index in range(3):
    registry.put_object(Bucket=bucket, Key=prefix + "page/" + str(index), Body=b"page")
seen, continuation = [], None
while True:
    params = {"Bucket": bucket, "Prefix": prefix + "page/", "MaxKeys": 2}
    if continuation:
        params["ContinuationToken"] = continuation
    page = registry.list_objects_v2(**params)
    seen.extend(item["Key"] for item in page.get("Contents", []))
    if not page["IsTruncated"]:
        break
    continuation = page["NextContinuationToken"]
assert len(seen) == len(set(seen)) == 3

multipart_key = prefix + "multipart"
upload = registry.create_multipart_upload(Bucket=bucket, Key=multipart_key)
parts = [b"a" * (5 * 1024 * 1024), b"final-part"]
completed = []
for number, data in enumerate(parts, 1):
    result = registry.upload_part(
        Bucket=bucket, Key=multipart_key, UploadId=upload["UploadId"], PartNumber=number, Body=data
    )
    completed.append({"PartNumber": number, "ETag": result["ETag"]})
registry.complete_multipart_upload(
    Bucket=bucket,
    Key=multipart_key,
    UploadId=upload["UploadId"],
    MultipartUpload={"Parts": completed},
)
assert registry.get_object(Bucket=bucket, Key=multipart_key)["Body"].read() == b"".join(parts)
aborted = registry.create_multipart_upload(Bucket=bucket, Key=prefix + "aborted")
registry.abort_multipart_upload(Bucket=bucket, Key=prefix + "aborted", UploadId=aborted["UploadId"])
assert not registry.list_multipart_uploads(Bucket=bucket, Prefix=prefix + "aborted").get("Uploads")

presigned_key = prefix + "presigned"
put_url = registry.generate_presigned_url(
    "put_object", Params={"Bucket": bucket, "Key": presigned_key}, ExpiresIn=120
)
get_url = registry.generate_presigned_url(
    "get_object", Params={"Bucket": bucket, "Key": presigned_key}, ExpiresIn=120
)
with httpx.Client(timeout=10) as http:
    preflight = http.options(
        put_url,
        headers={
            "Origin": "http://127.0.0.1:18787",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:18787"
    assert http.put(put_url, content=body).status_code == 200
    assert http.get(get_url).content == body
    assert http.get(get_url, headers={"Host": "changed.invalid"}).status_code == 403
    tampered = get_url.replace("/" + bucket + "/", "/verdant-other/")
    assert http.get(tampered).status_code == 403
    expired = registry.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": presigned_key}, ExpiresIn=1
    )
    time.sleep(2)
    assert http.get(expired).status_code == 403

# Delete only this run's disposable objects; retain one explicit restart receipt.
objects = [{"Key": item} for item in seen + [conditional, multipart_key, presigned_key]]
registry.delete_objects(Bucket=bucket, Delete={"Objects": objects})
Path(args.receipt).write_text(
    json.dumps(
        {
            "bucket": bucket,
            "key": key,
            "sha256": hashlib.sha256(body).hexdigest(),
            "cors": admin.get_bucket_cors(Bucket=bucket)["CORSRules"],
        }
    )
    + "\n"
)
print(
    "Private S3, concurrent conditional create, ranges, pagination, multipart/abort, "
    "signing expiry/tamper and CORS preflight passed"
)
