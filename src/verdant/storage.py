"""Concrete S3 transport; domain services own authorization and artifact placement.

Browser URLs target staging only. Promotion verifies bytes before conditional final
creation, so replayable staging URLs cannot mutate an accepted artifact.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

CHUNK_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
PART_BYTES = 8 * 1024 * 1024
URL_SECONDS = 120


class StorageError(Exception):
    """Provider-independent errors safe to present without object keys or credentials."""


class ObjectMissing(StorageError):
    pass


class ObjectCorrupt(StorageError):
    pass


class StorageUnavailable(StorageError):
    pass


def validate_source_path(value: str):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 500
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(char in value for char in "\\:%?#")
    ):
        raise ValueError("Source paths must be canonical relative keys, not URLs")
    value.encode("utf-8")  # Reject non-UTF-8 surrogate input before provider requests.
    return value


def configured_source_key(prefix: str, namespace_id, relative: str):
    validate_source_path(prefix)
    validate_source_path(relative)
    key = f"{prefix}/{namespace_id}/{relative}"
    if len(key.encode("utf-8")) > 1024:
        raise ValueError("Configured source key exceeds supported length")
    return key


@dataclass(frozen=True)
class S3Settings:
    bucket: str
    endpoint: str
    browser_endpoint: str
    region: str = "us-east-1"
    addressing_style: str = "path"
    allow_insecure_http: bool = False
    source_prefix: str | None = None

    def __post_init__(self):
        if self.source_prefix is not None:
            validate_source_path(self.source_prefix)
        for endpoint in (self.endpoint, self.browser_endpoint):
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in ({"https", "http"} if self.allow_insecure_http else {"https"})
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("Invalid S3 endpoint configuration")
        if not self.bucket or self.addressing_style not in {"path", "virtual"}:
            raise ValueError("Invalid S3 bucket or addressing configuration")


@dataclass(frozen=True)
class ObjectDigest:
    sha256: str
    size_bytes: int

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256) or not (
            0 <= self.size_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError("Invalid artifact digest or supported size")


def _translate(exc):
    if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in {
        "NoSuchKey",
        "NoSuchUpload",
        "NotFound",
        "404",
    }:
        return ObjectMissing("Object or upload is missing")
    return StorageUnavailable("Object storage is unavailable or denied")


class S3Storage:
    @classmethod
    def from_env(cls):
        endpoint = os.environ.get("VERDANT_S3_ENDPOINT")
        if not endpoint:
            return None
        access, secret = (
            os.environ.get("VERDANT_S3_ACCESS_KEY"),
            os.environ.get("VERDANT_S3_SECRET_KEY"),
        )
        if bool(access) != bool(secret):
            raise ValueError("Incomplete configured object-storage credentials")
        settings = S3Settings(
            bucket=os.environ["VERDANT_S3_BUCKET"],
            endpoint=endpoint,
            browser_endpoint=os.environ.get("VERDANT_S3_BROWSER_ENDPOINT", endpoint),
            region=os.environ.get("VERDANT_S3_REGION", "us-east-1"),
            addressing_style=os.environ.get("VERDANT_S3_ADDRESSING_STYLE", "path"),
            source_prefix=os.environ.get("VERDANT_S3_SOURCE_PREFIX") or None,
            allow_insecure_http=os.environ.get("VERDANT_INSECURE_LOCAL_HTTP") == "1",
        )
        session = (
            boto3.Session(aws_access_key_id=access, aws_secret_access_key=secret)
            if access
            else None
        )
        return cls(settings, session=session)

    def __init__(self, settings: S3Settings, *, session=None):
        self.settings = settings
        session = session or boto3.Session()
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style},
            connect_timeout=3,
            read_timeout=30,
            retries={"total_max_attempts": 1},
        )
        self.client = session.client(
            "s3", endpoint_url=settings.endpoint, region_name=settings.region, config=config
        )
        self.signer = session.client(
            "s3", endpoint_url=settings.browser_endpoint, region_name=settings.region, config=config
        )

    def close(self):
        self.client.close()
        self.signer.close()

    def _call(self, method, **kwargs):
        try:
            return getattr(self.client, method)(Bucket=self.settings.bucket, **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise _translate(exc) from None

    def _sign(self, method, params):
        try:
            return self.signer.generate_presigned_url(method, Params=params, ExpiresIn=URL_SECONDS)
        except (BotoCoreError, ClientError) as exc:
            raise _translate(exc) from None

    def initiate(self, key: str):
        return self._call("create_multipart_upload", Key=key)["UploadId"]

    def find_uploads(self, key: str):
        """Reconcile ambiguous initiation for one domain-allocated staging key."""
        page = self._call("list_multipart_uploads", Prefix=key, MaxUploads=100)
        if page.get("IsTruncated"):
            raise StorageUnavailable("Upload reconciliation exceeds its bounded page")
        return [item["UploadId"] for item in page.get("Uploads", []) if item["Key"] == key]

    def parts(self, key: str, upload_id: str):
        """One bounded upload: at most 32 fixed-size parts in the supported profile."""
        result = self._call("list_parts", Key=key, UploadId=upload_id, MaxParts=100)
        if result.get("IsTruncated") or len(result.get("Parts", [])) > 32:
            raise ObjectCorrupt("Upload exceeds supported part count")
        return [
            {"PartNumber": part["PartNumber"], "ETag": part["ETag"], "Size": part["Size"]}
            for part in result.get("Parts", [])
        ]

    def sign_part(self, key: str, upload_id: str, number: int, size_bytes: int):
        if not 1 <= number <= 32 or not 0 <= size_bytes <= PART_BYTES:
            raise ValueError("Unsupported upload part number or size")
        return self._sign(
            "upload_part",
            {
                "Bucket": self.settings.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": number,
                "ContentLength": size_bytes,
            },
        )

    def complete(self, key: str, upload_id: str, expected_size: int):
        if not 0 <= expected_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Unsupported upload size")
        parts = self.parts(key, upload_id)
        if (
            not parts
            or [p["PartNumber"] for p in parts] != list(range(1, len(parts) + 1))
            or sum(p["Size"] for p in parts) != expected_size
            or any(p["Size"] != PART_BYTES for p in parts[:-1])
            or parts[-1]["Size"] > PART_BYTES
        ):
            raise ObjectCorrupt("Uploaded parts do not match declared size or layout")
        self._call(
            "complete_multipart_upload",
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]
            },
        )

    def abort(self, key: str, upload_id: str):
        try:
            self._call("abort_multipart_upload", Key=key, UploadId=upload_id)
        except ObjectMissing:
            pass

    def size(self, key: str):
        """Provider existence/size observation; this does not establish byte integrity."""
        return self._call("head_object", Key=key)["ContentLength"]

    def verify(self, key: str, expected: ObjectDigest, *, destination: BinaryIO | None = None):
        response = self._call("get_object", Key=key)
        body = response["Body"]
        digest, size = hashlib.sha256(), 0
        try:
            if response["ContentLength"] != expected.size_bytes:
                raise ObjectCorrupt("Object size differs from declared size")
            while chunk := body.read(CHUNK_BYTES):
                size += len(chunk)
                if size > expected.size_bytes:
                    raise ObjectCorrupt("Object exceeds declared size")
                digest.update(chunk)
                if destination is not None:
                    destination.write(chunk)
        except (BotoCoreError, ClientError, OSError):
            raise StorageUnavailable("Object stream was interrupted") from None
        finally:
            body.close()
        if size != expected.size_bytes or digest.hexdigest() != expected.sha256:
            raise ObjectCorrupt("Object bytes differ from declared digest")
        return expected

    def promote(self, staging_key: str, final_key: str, expected: ObjectDigest):
        if staging_key == final_key:
            raise ValueError("Staging and final locations must differ")
        try:
            return self.verify(final_key, expected)
        except ObjectMissing:
            pass
        # A completed final write is authoritative even if staging expired or was replayed.
        # Spill above one MiB to bounded worker scratch; never buffer a dataset in API memory.
        with SpooledTemporaryFile(max_size=CHUNK_BYTES, mode="w+b") as spool:
            self.verify(staging_key, expected, destination=spool)
            spool.seek(0)
            try:
                self.client.put_object(
                    Bucket=self.settings.bucket,
                    Key=final_key,
                    Body=spool,
                    ContentLength=expected.size_bytes,
                    IfNoneMatch="*",
                )
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                    raise _translate(exc) from None
            except BotoCoreError as exc:
                raise _translate(exc) from None
        # Conditional-write replay and lost responses reconcile against actual final bytes.
        return self.verify(final_key, expected)

    def sign_read(self, key: str):
        # Force attachment. Text/image preview belongs to a sandboxed rendering boundary.
        return self._sign(
            "get_object",
            {
                "Bucket": self.settings.bucket,
                "Key": key,
                "ResponseContentDisposition": "attachment",
                "ResponseContentType": "application/octet-stream",
            },
        )

    def list_page(self, prefix: str, *, continuation: str | None = None, limit: int = 100):
        if not 1 <= limit <= 100:
            raise ValueError("Unsupported object page size")
        params = {"Prefix": prefix, "MaxKeys": limit}
        if continuation:
            params["ContinuationToken"] = continuation
        return self._call("list_objects_v2", **params)

    def read_range(self, key: str, start: int, end: int):
        if not 0 <= start <= end or end - start >= CHUNK_BYTES:
            raise ValueError("Unsupported object range")
        response = self._call("get_object", Key=key, Range=f"bytes={start}-{end}")
        body = response["Body"]
        try:
            if response["ResponseMetadata"]["HTTPStatusCode"] != 206:
                raise ObjectCorrupt("Object store ignored range request")
            data = body.read(end - start + 2)
            if len(data) > end - start + 1:
                raise ObjectCorrupt("Object store exceeded requested range")
            return data
        except (BotoCoreError, ClientError, OSError):
            raise StorageUnavailable("Object range stream was interrupted") from None
        finally:
            body.close()

    def delete(self, keys: list[str]):
        if not 1 <= len(keys) <= 100:
            raise ValueError("Unsupported deletion batch")
        result = self._call("delete_objects", Delete={"Objects": [{"Key": key} for key in keys]})
        if result.get("Errors"):
            raise StorageUnavailable("Some owned objects could not be deleted")

    def delete_one(self, key: str):
        self._call("delete_object", Key=key)
