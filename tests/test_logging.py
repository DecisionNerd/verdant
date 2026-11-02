import logging

from verdant.logging import QueryRedactionFilter


def test_access_log_omits_callback_secrets():
    record = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "GET",
            "/auth/callback?code=private-code&state=private-state",
            "1.1",
            303,
        ),
        None,
    )
    assert QueryRedactionFilter().filter(record)
    assert "/auth/callback" in record.getMessage()
    assert "private-code" not in record.getMessage()
    assert "private-state" not in record.getMessage()


def test_httpx_signed_capability_query_is_redacted():
    import httpx

    record = logging.LogRecord(
        "httpx",
        20,
        "",
        1,
        'HTTP Request: %s %s "%s %d %s"',
        (
            "GET",
            httpx.URL("https://objects.test/key?X-Amz-Signature=private"),
            "HTTP/1.1",
            200,
            "OK",
        ),
        None,
    )
    assert QueryRedactionFilter().filter(record)
    assert "https://objects.test/key" in record.getMessage()
    assert "Signature" not in record.getMessage() and "private" not in record.getMessage()
