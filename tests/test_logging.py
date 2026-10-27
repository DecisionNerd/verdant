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
