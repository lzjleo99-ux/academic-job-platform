"""GET /v1/classification-review against a real (throwaway) database, not a mock - the point of this
endpoint is that it reads a real queue, and the two failure modes that matter (the dict_row mismatch, and
an ordering that buries the worst cases) are both invisible to a unit test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.classification_review import queue_classification


@pytest.fixture()
def client(conn):
    """conn is the project's own per-test database fixture; the API is pointed at it wholesale so the
    endpoint is exercised over the same real schema the migrations produce."""
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def _listing(conn, uid, title, kind="other"):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES ('nature_careers','feed','https://x.invalid') "
                 "ON CONFLICT (id) DO NOTHING")
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, position_kind, kind)
           VALUES (%s,'nature_careers',%s,%s,%s,'other',%s)""",
        (uid, f"https://x.invalid/{uid}", f"https://x.invalid/{uid}", title, kind))
    return conn.execute("SELECT id FROM listing WHERE ext_uid=%s", (uid,)).fetchone()[0]


def test_the_worklist_returns_the_evidence_next_to_the_verdict(client, conn):
    """A reviewer must never be asked to agree or disagree without seeing the text the decision was made
    on - that is the entire reason this endpoint carries `raw`, `rule` and `model` and not just a kind."""
    lid = _listing(conn, "r1", "Some grant-ish announcement")
    queue_classification(conn, lid, "kind", kind="grant_call", conf=0.60, raw="projekat",
                         rule="kind.grant_call", zone="body")
    d = client.get("/v1/classification-review").json()
    assert d["total"] == 1
    item = d["items"][0]
    assert item["raw"] == "projekat" and item["rule"] == "kind.grant_call" and item["zone"] == "body"
    assert item["conf"] == 0.60 and item["model"] == "ajp-rule-prior/1"
    assert item["listing_title"] == "Some grant-ish announcement"
    assert item["status"] == "pending" and item["reviewed_at"] is None


def test_the_worst_failure_is_returned_first(client, conn):
    """Ordering is the difference between a worklist and a dump: 'unclassified_academic' (a real ad no
    rule covered - the failure mode PM's own audits found by hand) must come before a merely-broad rule."""
    for uid, args in (
        ("r2", dict(kind="grant_call", conf=0.60, raw="projekat", rule="kind.grant_call")),
        ("r3", dict(kind="other", conf=None, raw=None, rule=None, academic_context=True)),
        ("r4", dict(kind="other", conf=None, raw="Catering", rule="kind.non_academic_role")),
    ):
        queue_classification(conn, _listing(conn, uid, uid), "kind", **args)
    assert [i["reason"] for i in client.get("/v1/classification-review").json()["items"]] == [
        "unclassified_academic", "excluded_by_rule", "low_confidence"]


def test_filter_by_reason_and_pagination(client, conn):
    for i in range(4):
        queue_classification(conn, _listing(conn, f"p{i}", f"ad {i}"), "kind", kind="grant_call",
                             conf=0.60, raw="projekat", rule="kind.grant_call")
    assert len(client.get("/v1/classification-review?limit=2").json()["items"]) == 2
    assert len(client.get("/v1/classification-review?limit=2&offset=2").json()["items"]) == 2
    only = client.get("/v1/classification-review?reason=low_confidence").json()["items"]
    assert len(only) == 4
    # an unknown filter value degrades to no filter rather than 500-ing on a typo
    assert client.get("/v1/classification-review?reason=nonsense").status_code == 200


def test_empty_queue_is_a_200_with_counts_not_an_error(client, conn):
    d = client.get("/v1/classification-review").json()
    assert d["items"] == [] and d["total"] == 0
    assert d["threshold"] == 0.70 and d["model"] == "ajp-rule-prior/1"
    assert d["pending_by_reason"] == {"low_confidence": 0, "excluded_by_rule": 0,
                                      "unclassified_academic": 0}


def test_the_decision_route_needs_a_secret_not_a_405(client):
    """T49 (2026-10-13): the write route now EXISTS - it was deliberately withheld until this API had any
    auth at all, because recording a decision is a mutation and /v1/applicants + /v1/thesis were already
    open writes (an unfixed exposure, not a precedent to extend). It is an OPERATOR action, so it takes a
    real secret (AJP_ADMIN_TOKEN) rather than the volume bound the public self-service endpoints get.
    With no token configured the endpoint is OFF (503, the same refuse-when-unconfigured rule as get_dsn);
    a wrong token is 401. Reading stays public."""
    assert client.get("/v1/classification-review").status_code == 200
    r = client.post("/v1/classification-review/00000000-0000-0000-0000-000000000000/decision",
                    json={"status": "confirmed"})
    assert r.status_code == 503, "no AJP_ADMIN_TOKEN configured: the admin surface must be off, not open"
    assert "AJP_ADMIN_TOKEN" in r.json()["detail"]
    # the collection path itself is still read-only
    assert client.post("/v1/classification-review", json={}).status_code == 405
    assert client.patch("/v1/classification-review").status_code == 405
