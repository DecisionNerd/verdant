"""Never emit authorization codes, callback state or other query secrets in access logs."""

import logging


class QueryRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records use (client, method, path_with_query, version, status).
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            if isinstance(path, str):
                record.args = (client, method, path.partition("?")[0], version, status)
        return True


def configure_access_logging():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, QueryRedactionFilter) for item in logger.filters):
        logger.addFilter(QueryRedactionFilter())
