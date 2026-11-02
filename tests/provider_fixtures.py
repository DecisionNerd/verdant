"""Provider HTTP fixtures use an unread wire stream, as real HTTPX transports do."""

import json as json_codec

import httpx


def model_response(status_code, *, json=None, **kwargs):
    if json is not None:
        kwargs["stream"] = httpx.ByteStream(json_codec.dumps(json).encode())
    return httpx.Response(status_code, **kwargs)
