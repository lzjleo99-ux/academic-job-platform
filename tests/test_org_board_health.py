"""Per-org health for the rs_org_boards fan-out - closing PM's own F4.

F4 of pm/outbox/debug_sweep_2026-10-10.md read: "rs_org_boards partially degraded - 3 sub-URLs tripped
R1, but the source itself is healthy (935 listings)". That was reported honestly but unactionably:
`/v1/sources` aggregates crawl_state by source_id, so the fan-out got one rolled-up status plus a list
of reason strings with no attribution - nothing said WHICH three URLs, let alone which organisations.

The data was never missing. Since T21/F8, `scheduler._run_rs_org_boards` persists board.crawl_orgs()'s
own per-org reports into `run_ledger.http_counters` as `{"orgs": N, "per_org": [...]}`, each entry
already carrying the org's slug, its row counts, its `blocked` list of `{url, reason, history}`, and
whether it was demoted to a deeplink. Verified against the live database: the most recent fan-out run
holds 68 per-org entries, 7 of them degraded, each with the exact URL and reason. What never existed
was a READER.

So this adds one (registry.fetch_org_board_health + GET /v1/orgs/board-health) rather than splitting
the fan-out into ~187 per-org source rows. Splitting would change scheduling, cadence, manifest and
robots handling for every org at once, to solve a reporting problem that already has its data - the
expensive fix for a problem the cheap one solves completely.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.registry import fetch_org_board_health

_FANOUT = "rs_org_boards"


def _run(conn, *, started_at, counters, source_id=_FANOUT, status="ok"):
    # run_ledger.source_id has an FK to source, so the row must exist first (ON CONFLICT keeps the
    # helper safe to call repeatedly within one test, e.g. for the latest-run ordering case). 'scrape_html'
    # is the fan-out's own real kind, verified against the live source table - not merely a valid one.
    conn.execute(
        """INSERT INTO source (id, kind, base_url) VALUES (%s, 'scrape_html', 'https://x.invalid')
           ON CONFLICT (id) DO NOTHING""", (source_id,))
    conn.execute(
        """INSERT INTO run_ledger (source_id, started_at, finished_at, status, http_counters)
           VALUES (%s, %s, %s, %s, %s)""",
        (source_id, started_at, started_at + timedelta(minutes=5), status, json.dumps(counters)),
    )


def _org(slug, *, rows_new=0, rows_updated=0, blocked=(), deeplink=False, items=0, pages=1):
    return {
        "slug": slug, "rows_new": rows_new, "rows_updated": rows_updated, "items": items,
        "pages_fetched": pages, "blocked": list(blocked), "degraded_to_deeplink": deeplink,
    }


def _blocked(url, reason="RobotsDenied: robots.txt unavailable"):
    return {"url": url, "reason": reason, "history": [{"run": "2026-10-12T00:00:00+00:00", "error": reason}]}


# ---------------------------------------------------------------- the attribution F4 was missing -------------

def test_every_degraded_org_is_paired_with_the_exact_url_and_reason_that_degraded_it(conn):
    """The whole point. "3 sub-URLs tripped R1" must become three named URLs on three named orgs, not a
    bare status. Assert the blocked list survives the round trip intact - url AND reason - because a
    reason with no URL is exactly the unattributable string F4 was stuck with."""
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 3, "per_org": [
            _org("bg-agrif", blocked=[_blocked("https://agrif.bg.ac.rs/sr/konkursi")]),
            _org("tf-ni", blocked=[_blocked("https://www.tf.ni.ac.rs/konkursi/")]),
            _org("poljfak-ni", blocked=[_blocked("https://www.poljfak.ni.ac.rs/")]),
            _org("bg-fon", rows_new=4, rows_updated=11),
        ],
    })

    health = fetch_org_board_health(conn)
    degraded = {o["slug"]: o for o in health["orgs"] if o["status"] == "degraded"}
    assert set(degraded) == {"bg-agrif", "tf-ni", "poljfak-ni"}
    assert degraded["bg-agrif"]["blocked"] == [
        {"url": "https://agrif.bg.ac.rs/sr/konkursi", "reason": "RobotsDenied: robots.txt unavailable"}]
    assert degraded["tf-ni"]["blocked"][0]["url"] == "https://www.tf.ni.ac.rs/konkursi/"


def test_rollups_are_consistent_with_the_per_org_entries(conn):
    """The rollups exist so a caller sees the shape at a glance; they must never disagree with the list
    they summarise, or the summary is worse than nothing. Asserted by recomputing from `orgs` rather
    than by restating the fixture's own numbers."""
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 5, "per_org": [
            _org("a", rows_new=3),
            _org("b", rows_updated=2),
            _org("c"),                                     # crawled fine, nothing new -> a zero-row org
            _org("d", blocked=[_blocked("https://d.invalid/")]),
            _org("e", blocked=[_blocked("https://e.invalid/")], deeplink=True),
        ],
    })
    health = fetch_org_board_health(conn)
    orgs = health["orgs"]

    assert health["orgs_total"] == len(orgs) == 5
    assert health["orgs_degraded"] == sum(1 for o in orgs if o["status"] == "degraded") == 2
    assert health["orgs_ok"] == sum(1 for o in orgs if o["status"] == "ok") == 3
    assert health["orgs_zero_rows"] == sum(
        1 for o in orgs if not (o["rows_new"] or o["rows_updated"])) == 3
    # degraded first, so the actionable part is at the top of the list without the caller sorting
    assert [o["status"] for o in orgs] == ["degraded", "degraded", "ok", "ok", "ok"]


def test_a_partially_blocked_org_is_distinguished_from_one_fully_degraded_to_a_deeplink(conn):
    """board.py itself keeps these two states apart: an org with ONE blocked URL out of several is still
    partially crawling, while one whose EVERY url is blocked gets demoted to a deeplink by
    degrade_to_deeplink(). Collapsing them into one boolean would lose which one happened - and the
    second is the state an operator has to act on."""
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 2, "per_org": [
            _org("partial", blocked=[_blocked("https://p.invalid/a")], rows_new=5),
            _org("fully", blocked=[_blocked("https://f.invalid/a"), _blocked("https://f.invalid/b")],
                 deeplink=True),
        ],
    })
    orgs = {o["slug"]: o for o in fetch_org_board_health(conn)["orgs"]}
    assert orgs["partial"]["degraded_to_deeplink"] is False and orgs["partial"]["status"] == "degraded"
    assert orgs["fully"]["degraded_to_deeplink"] is True and orgs["fully"]["status"] == "degraded"
    assert len(orgs["fully"]["blocked"]) == 2        # every blocked URL, not just the first


# ---------------------------------------------------------------- reader correctness --------------------------

def test_reads_the_latest_fan_out_run_not_an_older_one(conn):
    """Ordering by started_at DESC is the whole reader. If it ever picked the oldest row (or an
    arbitrary one), a recovered org would look permanently degraded and a newly broken one invisible -
    the failure mode is silently stale health data, which is worse than none."""
    _run(conn, started_at=datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 1, "per_org": [_org("stale", blocked=[_blocked("https://stale.invalid/")])]})
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 1, "per_org": [_org("fresh", rows_new=9)]})

    health = fetch_org_board_health(conn)
    assert [o["slug"] for o in health["orgs"]] == ["fresh"]
    assert health["orgs_degraded"] == 0
    assert health["run_started_at"] == datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)


def test_ignores_run_ledger_rows_belonging_to_other_sources(conn):
    """Scoping. A generic source's http_counters is its own business (per-connector counters), and
    reading one by accident would attribute another source's internals to the org fan-out."""
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc),
         counters={"orgs": 1, "per_org": [_org("bg-fon", rows_new=1)]})
    _run(conn, started_at=datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc), source_id="nature_careers",
         counters={"orgs": 99, "per_org": [_org("not-an-org", blocked=[_blocked("https://x.invalid/")])]})

    health = fetch_org_board_health(conn)
    assert health["orgs_total"] == 1 and [o["slug"] for o in health["orgs"]] == ["bg-fon"]


def test_a_fresh_install_with_no_fan_out_run_is_a_valid_empty_answer_not_an_error(conn):
    """Read-only endpoints must not 500 on a legitimate empty state. A brand-new database has no
    rs_org_boards run yet; that is a correct "nothing crawled yet", not a failure - and inventing a
    placeholder org to avoid an empty list would be exactly the fabrication D6 forbids."""
    health = fetch_org_board_health(conn)
    assert health["orgs"] == [] and health["orgs_total"] == 0
    assert health["orgs_degraded"] == 0 and health["run_started_at"] is None


def test_handles_http_counters_delivered_as_text_rather_than_a_decoded_jsonb(conn):
    """registry must behave identically regardless of the caller's connection setup - the same
    independence fetch_source_statuses already documents. psycopg normally decodes jsonb for us, but a
    caller that disabled jsonb loading hands back a str; returning an empty health report there would
    be a silent wrong answer rather than a visible error."""
    conn.execute(
        """INSERT INTO source (id, kind, base_url) VALUES (%s, 'scrape_html', 'https://x.invalid')
           ON CONFLICT (id) DO NOTHING""", (_FANOUT,))
    conn.execute(
        """INSERT INTO run_ledger (source_id, started_at, finished_at, status, http_counters)
           VALUES (%s, %s, %s, 'ok', %s::text::jsonb)""",
        (_FANOUT, datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc),
         datetime(2026, 10, 12, 2, 5, tzinfo=timezone.utc),
         json.dumps({"orgs": 1, "per_org": [_org("as-text", rows_new=2)]})),
    )
    # force the str path the same way a jsonb-loading-disabled caller would
    conn.row_factory = dict_row
    row = conn.execute(
        "SELECT http_counters::text AS c FROM run_ledger WHERE source_id = %s", (_FANOUT,)).fetchone()
    assert isinstance(row["c"], str)
    health = fetch_org_board_health(conn)
    assert [o["slug"] for o in health["orgs"]] == ["as-text"]


# ---------------------------------------------------------------- the API surface ------------------------------

@pytest.fixture()
def client(conn):
    app = create_app()
    conn.row_factory = dict_row
    app.dependency_overrides[get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c


def test_board_health_endpoint_returns_the_attributed_breakdown(client, conn):
    _run(conn, started_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc), counters={
        "orgs": 2, "per_org": [
            _org("bg-agrif", blocked=[_blocked("https://agrif.bg.ac.rs/sr/konkursi")]),
            _org("bg-fon", rows_new=7),
        ],
    })
    r = client.get("/v1/orgs/board-health")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == _FANOUT
    assert body["orgs_total"] == 2 and body["orgs_degraded"] == 1
    degraded = next(o for o in body["orgs"] if o["status"] == "degraded")
    assert degraded["slug"] == "bg-agrif"
    assert degraded["blocked"][0]["url"] == "https://agrif.bg.ac.rs/sr/konkursi"


def test_board_health_is_not_swallowed_by_the_org_id_path_parameter(client):
    """The route-ordering trap, and the reason the literal segment is declared before /orgs/{org_id}:
    FastAPI matches in registration order, so a "board-health" declared after the parameter would be
    captured as an org_id and 404 - a working endpoint that looks broken. This test fails if the
    declaration order is ever reversed."""
    r = client.get("/v1/orgs/board-health")
    assert r.status_code == 200, "the literal path must win over /orgs/{org_id}"
    assert r.json()["orgs"] == []


def test_an_unknown_org_id_still_404s_rather_than_being_served_the_health_payload(client):
    """The other side of the same trap: adding the literal route must not break the parameterised one."""
    r = client.get("/v1/orgs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
