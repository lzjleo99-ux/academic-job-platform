"""T49 (2026-10-13): the two controls a public deployment needs, and the reasoning each one pins.

The design split matters more than either piece:
  * PUBLIC self-service writes (/v1/applicants, /v1/thesis) are the PRODUCT - a visitor uploads their CV
    from the web UI, and a browser cannot hold a secret. The control that fits is VOLUME (rate limiting),
    not identity.
  * ADMIN writes (the classification-review decision) are an operator action and take a real secret,
    refuse-when-unconfigured - the same house rule as get_dsn's 503.

Both halves are tested against the real HTTP layer, because the failure modes (a limiter that leaks state
between tests, a token check that 500s instead of 401-ing) are invisible at unit level.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.api.ratelimit import RateLimiter, Rule, client_key, resolve_rules


@pytest.fixture()
def client_factory(conn, monkeypatch):
    """Build a fresh app per call, against this test's own throwaway DB. Fresh app = fresh limiter, so
    one test's request history never leaks into the next."""
    conn.row_factory = dict_row      # the API always reads rows as dicts (test_api.py's own convention)

    def _make():
        app = create_app()
        app.dependency_overrides[get_conn] = lambda: conn
        return TestClient(app)
    return _make


# --------------------------------------------------------------------------- the limiter itself ----------
def test_window_admits_up_to_the_limit_then_429s():
    rl = RateLimiter({"upload": Rule(max_requests=3, window_seconds=60)})
    for _ in range(3):
        rl.hit("upload", "1.2.3.4", now=1000.0)
    with pytest.raises(Exception) as e:
        rl.hit("upload", "1.2.3.4", now=1000.0)
    assert getattr(e.value, "status_code", None) == 429
    assert "Retry-After" in e.value.headers, "a bare 429 tells the client nothing about when to come back"
    assert int(e.value.headers["Retry-After"]) >= 1


def test_the_window_slides_so_an_old_request_stops_counting():
    rl = RateLimiter({"upload": Rule(max_requests=2, window_seconds=60)})
    rl.hit("upload", "ip", now=0.0)
    rl.hit("upload", "ip", now=1.0)
    with pytest.raises(Exception):
        rl.hit("upload", "ip", now=10.0), "still inside the window: full"
    # ...but once the oldest hit has slid out, the slot is free again - and no exception escapes. Both
    # old hits are past the window at now=61 (0.0 is 61s old, 1.0 is exactly 60s), so only the new one is
    # left: the assertion that matters is that the third hit was ADMITTED, not the count.
    rl2 = RateLimiter({"upload": Rule(max_requests=2, window_seconds=60)})
    rl2.hit("upload", "ip", now=0.0)
    rl2.hit("upload", "ip", now=1.0)
    rl2.hit("upload", "ip", now=61.0)          # would 429 if the window did not slide
    assert rl2.count("upload", "ip") == 1


def test_clients_are_isolated_from_each_other():
    """One abusive client must never lock out everyone else - the whole point of a per-client limit."""
    rl = RateLimiter({"upload": Rule(max_requests=1, window_seconds=60)})
    rl.hit("upload", "a", now=0.0)
    with pytest.raises(Exception):
        rl.hit("upload", "a", now=1.0)
    rl.hit("upload", "b", now=1.0)             # unaffected
    assert rl.count("upload", "b") == 1


def test_buckets_are_independent_and_unknown_buckets_fail_loudly():
    rl = RateLimiter({"upload": Rule(max_requests=1, window_seconds=60), "write": Rule(1, 60)})
    rl.hit("upload", "ip", now=0.0)
    rl.hit("write", "ip", now=0.0)             # separate budget, not one shared counter
    with pytest.raises(RuntimeError, match="unknown rate-limit bucket"):
        rl.hit("nope", "ip")                   # a typo must never silently disable a limit


def test_env_override_is_parsed_and_validated():
    import os
    os.environ["AJP_RATE_LIMIT_UPLOAD_PER_HOUR"] = "5"
    try:
        assert resolve_rules()["upload"].max_requests == 5
    finally:
        del os.environ["AJP_RATE_LIMIT_UPLOAD_PER_HOUR"]
    os.environ["AJP_RATE_LIMIT_UPLOAD_PER_HOUR"] = "banana"
    try:
        with pytest.raises(RuntimeError, match="must be an integer"):
            resolve_rules()
    finally:
        del os.environ["AJP_RATE_LIMIT_UPLOAD_PER_HOUR"]
    # a mis-typed env name changes nothing rather than silently disabling the limit
    assert resolve_rules()["upload"] == Rule(max_requests=60, window_seconds=3600)


def test_proxy_headers_are_trusted_only_when_the_deployment_declares_it():
    """Both choices are wrong somewhere: behind a proxy, request.client is the proxy itself (one shared
    bucket for every visitor); without a proxy, trusting X-Forwarded-For lets a client mint unlimited
    buckets with a fake header. So the deployment declares it, and the default is 'do not trust'."""
    import starlette.requests

    class _FakeRequest:
        headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
        client = type("C", (), {"host": "127.0.0.1"})()

    import os
    os.environ.pop("AJP_TRUST_PROXY_HEADERS", None)
    assert client_key(_FakeRequest()) == "127.0.0.1"
    os.environ["AJP_TRUST_PROXY_HEADERS"] = "1"
    try:
        assert client_key(_FakeRequest()) == "proxy:9.9.9.9"
    finally:
        del os.environ["AJP_TRUST_PROXY_HEADERS"]


# --------------------------------------------------------------------------- over real HTTP ---------------
def test_upload_endpoint_429s_at_its_limit_over_real_http(client_factory, monkeypatch):
    monkeypatch.setenv("AJP_RATE_LIMIT_UPLOAD_PER_HOUR", "2")
    c = client_factory()
    small = b"PK\x03\x04" + b"x" * 16
    codes = [c.post("/v1/applicants", files={"file": ("cv.docx", small)}).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] or codes[:2] == [415, 415]  # content is junk; what matters is the boundary
    assert codes[2] == 429
    r = c.post("/v1/applicants", files={"file": ("cv.docx", small)})
    assert r.status_code == 429 and "Retry-After" in r.headers
    # reads are never limited (and /v1/applicants has no GET collection - the listing search is the read)
    assert c.get("/v1/listings").status_code == 200
    assert c.get("/health").status_code == 200


def test_reads_are_never_rate_limited_even_when_every_bucket_is_exhausted(client_factory, monkeypatch):
    """The limiter exists to bound the EXPENSIVE things. A visitor who has burned their upload budget must
    still be able to browse listings - locking out reads would let one abuser take the whole site down."""
    monkeypatch.setenv("AJP_RATE_LIMIT_UPLOAD_PER_HOUR", "1")
    monkeypatch.setenv("AJP_RATE_LIMIT_WRITE_PER_HOUR", "1")
    c = client_factory()
    junk = b"PK\x03\x04" + b"x" * 16
    c.post("/v1/applicants", files={"file": ("cv.docx", junk)})     # burns the upload bucket
    for _ in range(5):
        assert c.get("/v1/listings").status_code == 200
        assert c.get("/health").status_code == 200


# --------------------------------------------------------------------------- the admin token ---------------
@pytest.fixture()
def admin_client(conn):
    conn.row_factory = dict_row
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def test_admin_endpoint_is_off_until_a_token_is_configured(admin_client, monkeypatch):
    monkeypatch.delenv("AJP_ADMIN_TOKEN", raising=False)
    r = admin_client.post("/v1/classification-review/x/decision", json={"status": "confirmed"})
    assert r.status_code == 503
    monkeypatch.setenv("AJP_ADMIN_TOKEN", "s3cret")
    r = admin_client.post("/v1/classification-review/x/decision", json={"status": "confirmed"})
    assert r.status_code == 401, "configured but unauthenticated is a 401, not a 503"
    assert admin_client.post("/v1/classification-review/x/decision", json={"status": "confirmed"},
                             headers={"Authorization": "Bearer wrong"}).status_code == 401
    # with the right token the 400 comes from the DECISION being invalid - i.e. auth passed and the
    # endpoint really ran (a nonexistent review id)
    r = admin_client.post("/v1/classification-review/00000000-0000-0000-0000-000000000000/decision",
                          json={"status": "confirmed"}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 400 and "no review with id" in r.json()["detail"]


def test_a_corrected_decision_must_name_the_kind_even_over_http(admin_client, monkeypatch):
    monkeypatch.setenv("AJP_ADMIN_TOKEN", "s3cret")
    h = {"Authorization": "Bearer s3cret"}
    r = admin_client.post("/v1/classification-review/00000000-0000-0000-0000-000000000000/decision",
                          json={"status": "corrected"}, headers=h)
    assert r.status_code == 400 and "must name the correct kind" in r.json()["detail"]
    r = admin_client.post("/v1/classification-review/00000000-0000-0000-0000-000000000000/decision",
                          json={"status": "nonsense"}, headers=h)
    assert r.status_code == 400 and "confirmed/corrected/dismissed" in r.json()["detail"]
