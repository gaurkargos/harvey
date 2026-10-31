"""API keys must not survive into httpx's INFO request lines.

The verifiers take their API key in the query string, so httpx's
``HTTP Request: GET <url>`` lines carried live keys into captured output
(heartbeat-run.log). harvey/logredact.py installs a filter on the httpx
logger; these tests pin the redaction and the logging integration.
"""

import logging

import httpx

from harvey import logredact

REOON_URL = (
    "https://emailverifier.reoon.com/api/v1/verify"
    "?email=person%40example.com&key=LIVE-REOON-KEY&mode=power"
)
REDACTED_REOON_URL = (
    "https://emailverifier.reoon.com/api/v1/verify"
    "?email=person%40example.com&key=[REDACTED]&mode=power"
)
HUNTER_URL = (
    "https://api.hunter.io/v2/domain-search"
    "?domain=example.com&api_key=LIVE-HUNTER-KEY&limit=1"
)


def test_reoon_key_redacted():
    assert logredact.redact_url(REOON_URL) == REDACTED_REOON_URL


def test_hunter_api_key_redacted():
    assert "LIVE-HUNTER-KEY" not in logredact.redact_url(HUNTER_URL)
    assert "api_key=[REDACTED]" in logredact.redact_url(HUNTER_URL)


def test_other_params_pass_through_byte_for_byte():
    url = (
        "https://osm.example/search?q=dentist&format=json&limit=1"
        "&utm_source=harvey&mode=power"
    )
    assert logredact.redact_url(url) == url


def test_param_name_is_case_insensitive():
    assert "[REDACTED]" in logredact.redact_url(
        "https://api.example/v1?Email=a%40example.com&KEY=SECRET-VALUE")


def test_whole_name_only():
    # A longer parameter that merely ends the same way is not touched.
    untouched = "https://api.example/v1?monkey=1&author=jane"
    assert logredact.redact_url(untouched) == untouched


def test_empty_value_left_alone():
    url = "https://api.example/v1?key=&mode=power"
    assert logredact.redact_url(url) == url


def test_url_without_query_unchanged():
    url = "https://api.hunter.io/v2/domain-search"
    assert logredact.redact_url(url) == url


def test_value_runs_to_next_separator_only():
    redacted = logredact.redact_url(
        "https://api.example/v1?key=abc&mode=power")
    assert redacted == "https://api.example/v1?key=[REDACTED]&mode=power"
    # '=' inside a value (base64 padding) is part of the value.
    redacted = logredact.redact_url(
        'https://api.example/v1?key=abc=def&mode=power "HTTP/1.1 200 OK"')
    assert redacted == (
        'https://api.example/v1?key=[REDACTED]&mode=power "HTTP/1.1 200 OK"')


def test_filter_rewrites_httpx_style_record():
    record = logging.LogRecord(
        "httpx", logging.INFO, "httpx/_client.py", 1026,
        'HTTP Request: %s %s "%s %d %s"',
        ("GET", REOON_URL, "HTTP/1.1", 200, "OK"), None,
    )
    assert logredact.QuerySecretsFilter().filter(record) is True
    assert record.getMessage() == (
        'HTTP Request: GET ' + REDACTED_REOON_URL + ' "HTTP/1.1 200 OK"')


def test_logger_line_redacted(caplog):
    logredact.install()
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "GET", HUNTER_URL, "HTTP/1.1", 200, "OK")
    line = caplog.records[-1].getMessage()
    assert "LIVE-HUNTER-KEY" not in line
    assert "api_key=[REDACTED]" in line
    assert "domain=example.com" in line  # everything else still readable


def test_real_httpx_request_redacted(caplog):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    logredact.install()
    with caplog.at_level(logging.INFO, logger="httpx"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            client.get("https://emailverifier.reoon.com/api/v1/verify",
                       params={"email": "person@example.com",
                               "key": "LIVE-REOON-KEY", "mode": "power"})
    line = caplog.records[-1].getMessage()
    assert calls and calls[0].url.params["key"] == "LIVE-REOON-KEY"  # wire unchanged
    assert "LIVE-REOON-KEY" not in line
    assert "key=%5BREDACTED%5D" in line or "key=[REDACTED]" in line


def test_install_is_idempotent():
    assert logredact.install() in (True, False)  # first call in this process already ran
    assert logredact.install() is False
    filters = [f for f in logging.getLogger("httpx").filters
               if isinstance(f, logredact.QuerySecretsFilter)]
    assert len(filters) == 1


def test_package_import_installed_the_filter():
    import harvey  # noqa: F401 - imported for side effect

    filters = [f for f in logging.getLogger("httpx").filters
               if isinstance(f, logredact.QuerySecretsFilter)]
    assert len(filters) == 1
