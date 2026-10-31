"""Keep API keys out of log lines.

httpx logs every request at INFO as ``HTTP Request: GET <full-url>`` — and
the email verifiers (Reoon, ZeroBounce, Hunter) take their API key in the
query string, so a routine address verification printed the live key into
heartbeat-run.log and anywhere else Harvey's output is captured. A filter on
the httpx logger redacts credential-bearing query parameters before the
record reaches any handler, so the line still says what was called and how
it answered — just not the key.
"""

import logging
import re

# Query-parameter names that carry credentials. Their values get blanked.
# Case-insensitive; the ``[?&]name=`` anchor means a name only matches whole
# (``monkey=`` is left alone), and a value runs to the next ``&``, whitespace
# or quote.
SECRET_PARAMS = (
    "access-token", "access_token", "api-token", "api_token", "api-key",
    "api_key", "apikey", "auth_token", "auth", "client_secret", "credential",
    "key", "password", "passwd", "secret", "signature", "token",
)

_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&](?:" + "|".join(SECRET_PARAMS) + r")=)[^&\s\"']+"
)

REDACTED = "[REDACTED]"


def redact_url(url: str) -> str:
    """Blank the value of any credential-bearing query parameter in ``url``.

    Everything else — scheme, host, path, other parameters, their order and
    encoding — passes through byte for byte.
    """
    return _SECRET_PARAM_RE.sub(r"\g<1>" + REDACTED, url)


class QuerySecretsFilter(logging.Filter):
    """Redact credential query parameters in a log record's message.

    httpx logs the request line as a format string with the URL among the
    args, so the record is rendered first, redacted, and re-attached with no
    args. Failures never break logging — the line simply goes out unredacted
    rather than taking the pipeline down.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                rendered = record.getMessage()
                redacted = redact_url(rendered)
                if redacted != rendered:
                    record.msg, record.args = redacted, None
            elif isinstance(record.msg, str):
                record.msg = redact_url(record.msg)
        except Exception:
            pass
        return True


def install(logger_name: str = "httpx") -> bool:
    """Attach the filter to a logger, once. True if newly added."""
    log = logging.getLogger(logger_name)
    if any(isinstance(f, QuerySecretsFilter) for f in log.filters):
        return False
    log.addFilter(QuerySecretsFilter())
    return True
