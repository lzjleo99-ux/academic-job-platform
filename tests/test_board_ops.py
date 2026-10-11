"""Pagination bounds, run-level retry counter (R1) and the Playwright render path, against local stub servers."""
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from ajp import orgs
from ajp.connectors import board
from ajp.connectors.base import FetchFailed, PoliteClient
from ajp.connectors.render import PlaywrightRenderer, RenderUnavailable
from ajp.rawstore import LocalFsStore, get_raw
from ajp.robots import RobotsDenied
from stubserver import Stub


def page(n, nxt=None, pager=""):
    item = f'<article><h3><a href="/i/{n}">Конкурс за избор у звање доцента број {n} за област Примерна</a></h3><time datetime="2026-09-{n:02d}">x</time></article>'
    link = f'<link rel="next" href="{nxt}">' if nxt else ""
    return f"<html><head>{link}</head><body><main>{item}</main>{pager}</body></html>".encode()


@pytest.fixture()
def stub():
    s = Stub(); s.robots("")
    yield s
    s.stop()


def org_for(stub, **cfg):
    return {"id": None, "slug": "t", "name_en": "T", "city": "Belgrade", "homepage": stub.base + "/", "career_urls": [stub.base + "/board"],
            "render_mode": "httpx", "crawl_config": cfg}


def chain(stub, n_pages, cap=None):
    for i in range(1, n_pages + 1):
        path = "/board" if i == 1 else f"/board?page={i}"
        stub.route(path, body=page(i, f"/board?page={i + 1}" if i < n_pages else None))


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


# ---------------------------------------------------------------- pagination -------------
def test_default_is_one_page_and_config_raises_the_cap_exactly(stub, tmp_path):
    chain(stub, 6)
    _, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
    assert rep["pages_fetched"] == 1 and rep["max_pages"] == 1 and stub.paths().count("/board?page=2") == 0
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub, max_pages=3)).crawl()
    assert rep["pages_fetched"] == 3 and len(rows) == 3
    assert "/board?page=4" not in stub.paths()                                   # never beyond the configured cap


def test_hard_ceiling_beats_any_config_and_paging_stops_when_there_is_no_next(stub, tmp_path):
    chain(stub, 14)
    _, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub, max_pages=99)).crawl()
    assert rep["pages_fetched"] == board.MAX_PAGES_HARD == 10 and rep["max_pages"] == 10
    stub2 = Stub(); stub2.robots(""); chain(stub2, 2)
    try:
        _, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub2, max_pages=5)).crawl()
        assert rep["pages_fetched"] == 2                                          # last page has no rel=next
    finally:
        stub2.stop()


def test_paging_stops_when_a_page_adds_nothing_new_and_on_loops(stub, tmp_path):
    stub.route("/board", body=page(1, "/board?page=2")).route("/board?page=2", body=page(1, "/board"))     # same item, loops back
    _, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub, max_pages=5)).crawl()
    assert rep["pages_fetched"] == 2 and rep["items"] == 1


def test_next_page_url_rel_next_and_numbered_pagers():
    assert board.next_page_url('<link rel="next" href="/p/2">', "https://x.invalid/p/1", 1) == "https://x.invalid/p/2"
    assert board.next_page_url('<a rel="next" href="?page=2">></a>', "https://x.invalid/b", 1) == "https://x.invalid/b?page=2"
    html = '<ul class="pagination"><li><a href="/b?start=11">2</a></li><li><a href="/b?start=22">3</a></li></ul>'
    assert board.next_page_url(html, "https://x.invalid/b", 1) == "https://x.invalid/b?start=11"
    assert board.next_page_url(html, "https://x.invalid/b?start=11", 2) == "https://x.invalid/b?start=22"
    assert board.next_page_url('<a href="/about">2</a>', "https://x.invalid/b", 1) is None                # '2' but not a pager link
    assert board.next_page_url("<p>none</p>", "https://x.invalid/b", 1) is None


def test_off_domain_pager_link_ends_paging_without_a_request(stub, tmp_path):
    other = Stub(); other.robots("").route("/board?page=2", body=page(2))
    try:
        stub.route("/board", body=page(1, other.base + "/board?page=2"))
        _, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub, max_pages=3)).crawl()
        assert rep["pages_fetched"] == 1 and other.hits == []                    # manifest = the org's own host family
    finally:
        other.stop()


# ---------------------------------------------------------------- R1 run-level retry -----
@pytest.fixture()
def org_row(conn, stub):
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, homepage, host, career_urls, render_mode, robots_status)
                    VALUES ('t-org','Test Org','RS','faculty',%s,'127.0.0.1',%s,'httpx','allowed')""", (stub.base + "/", [stub.base + "/board"]))
    return next(t for t in orgs.crawl_targets(conn) if t["slug"] == "t-org")


def run(conn, tmp_path, org, run_id):
    return board.crawl_orgs(conn, fast(), LocalFsStore(tmp_path), [org], run_id)[0]


def state(conn, stub):
    return conn.execute("SELECT consecutive_failures, blocked_reason, last_run_id FROM crawl_state WHERE key=%s", (stub.base + "/board",)).fetchone()


def test_third_failed_run_blocks_and_degrades_to_deeplink(conn, stub, tmp_path, org_row):
    stub.route("/board", status=503)
    r1 = run(conn, tmp_path, org_row, "run-1")
    assert r1["degraded_to_deeplink"] is False and state(conn, stub)[:2] == (1, None)
    run(conn, tmp_path, org_row, "run-1")                                              # same run again: still counted once
    assert state(conn, stub)[0] == 1
    run(conn, tmp_path, org_row, "run-2")
    assert state(conn, stub)[:2] == (2, None) and orgs.crawl_targets(conn)                # still crawlable after 2 failed runs
    r3 = run(conn, tmp_path, org_row, "run-3")
    assert r3["degraded_to_deeplink"] is True
    n, reason, _ = state(conn, stub)
    assert n == 3 and reason.startswith("unreachable_after_3_runs")
    hist = conn.execute("SELECT history FROM crawl_state WHERE key=%s", (stub.base + "/board",)).fetchone()[0]
    assert [h["run"] for h in hist] == ["run-1", "run-2", "run-3"]                    # error history kept
    assert conn.execute("SELECT render_mode, notes FROM org_master WHERE slug='t-org'").fetchone()[0] == "deeplink_only"
    assert conn.execute("SELECT kind FROM source WHERE id='t_org'").fetchone() == ("channel",)
    assert "t-org" not in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_blocked_url_is_skipped_without_any_request(conn, stub, tmp_path, org_row):
    conn.execute("INSERT INTO crawl_state (source_id, key, consecutive_failures, blocked_reason) VALUES ('rs_org_boards', %s, 3, 'unreachable_after_3_runs: x')", (stub.base + "/board",))
    stub.route("/board", body=page(1))
    rep = run(conn, tmp_path, org_row, "run-9")
    assert "/board" not in stub.paths() and "blocked earlier" in rep["blocked"][0]["reason"]


def test_success_resets_the_counter(conn, stub, tmp_path, org_row):
    stub.route("/board", status=503)
    run(conn, tmp_path, org_row, "run-1"); run(conn, tmp_path, org_row, "run-2")
    assert state(conn, stub)[0] == 2
    stub.route("/board", body=page(1))
    rep = run(conn, tmp_path, org_row, "run-3")
    assert rep["rows"] == 1 and state(conn, stub)[:2] == (0, None)


def test_crawl_orgs_ingested_count_distinguishes_new_from_updated_rows(conn, stub, tmp_path, org_row):
    """F8 (found live wiring rs_org_boards into the scheduler, 2026-09-24): upsert_listing returns
    (listing_id, inserted) - a 2-tuple, ALWAYS truthy - so `if upsert_listing(...)` previously counted every
    row as 'ingested' regardless of whether it was a fresh insert or a re-crawl of the same posting."""
    stub.route("/board", body=page(1))
    rep1 = run(conn, tmp_path, org_row, "run-1")
    assert rep1["rows_new"] == 1 and rep1["rows_updated"] == 0 and rep1["ingested"] == 1
    rep2 = run(conn, tmp_path, org_row, "run-2")                       # same posting seen again
    assert rep2["rows_new"] == 0 and rep2["rows_updated"] == 1 and rep2["ingested"] == 1


def test_crawl_orgs_seeds_sources_so_its_rows_are_visible_to_filters_and_facets(conn, stub, tmp_path, org_row):
    """F14 (2026-10-11): crawl_orgs ingested through plain upsert_listing, and `sources` is NOT an insertable
    column (ingest.LISTING_COLUMNS excludes it as managed by upsert_listing_deduped), so every row it wrote
    kept migration 0014's DEFAULT '[]' forever - invisible to the `source=` filter and to /v1/facets' source
    dimension, both of which read jsonb_array_elements(l.sources), and canonical_source() returned None
    (verified_direct False) for exactly the employer-direct rows that flag exists for."""
    stub.route("/board", body=page(1))
    rep = run(conn, tmp_path, org_row, "run-1")
    assert rep["rows_new"] == 1
    sources = conn.execute("SELECT sources FROM listing").fetchone()[0]
    assert [s["source"] for s in sources] == ["rs_org_boards"]


# ---------------------------------------------------------------- F8: wired into the real scheduler ------------
def test_rs_org_boards_is_actually_crawled_by_run_once_not_a_noop(conn, stub, tmp_path, org_row):
    """F8 (PM live audit 2026-09-24): rs_org_boards had no single connector instance the generic factory
    could return, so board.crawl_orgs() was NEVER actually called from the real scheduler at all - only a
    labelled NoOpConnector, invoked only from tests/tools. A real ajp-run --once must actually crawl it."""
    from ajp import scheduler

    board.BoardConnector.ensure_source(conn)   # normally done by ajp.bootstrap.ensure_sources() during ajp-init
    stub.route("/board", body=page(1))
    result = scheduler.run_once(conn, fast(), LocalFsStore(tmp_path), "unused",
                                now=datetime.now(timezone.utc), skip_fx=True)
    rs_result = next(r for r in result["results"] if r["source_id"] == "rs_org_boards")
    assert rs_result["status"] == "ok"
    assert rs_result["rows_new"] == 1 and rs_result["orgs"] == 1
    listing_count = conn.execute("SELECT count(*) FROM listing").fetchone()[0]
    assert listing_count == 1
    ledger = conn.execute("SELECT status, pages_fetched, rows_new, note FROM run_ledger WHERE source_id = 'rs_org_boards'").fetchone()
    assert ledger[0] == "ok" and ledger[1] == 1 and ledger[2] == 1
    assert ledger[3] is None or "org-fan-out source" not in (ledger[3] or "")   # not the old NoOp note


def test_rs_org_boards_with_zero_eligible_orgs_is_an_honest_ok_not_an_error(conn, tmp_path):
    """No RS org meets crawl_targets()'s own criteria (robots-allowed, a real career URL, a fetch mode that
    performs requests) - a real, honest state, never fabricated as an error just because nothing ran."""
    from ajp import scheduler
    from ajp.connectors.board import BoardConnector

    BoardConnector.ensure_source(conn)
    result = scheduler.run_once(conn, fast(), LocalFsStore(tmp_path), "unused",
                                now=datetime.now(timezone.utc), skip_fx=True)
    rs_result = next(r for r in result["results"] if r["source_id"] == "rs_org_boards")
    assert rs_result["status"] == "ok" and "note" in rs_result and "no orgs eligible" in rs_result["note"]


def test_robots_disallow_is_permanent_but_robots_unreachable_is_transient(conn, stub, tmp_path, org_row):
    stub.robots("User-agent: *\nDisallow: /board\n").route("/board", body=page(1))
    rep = run(conn, tmp_path, org_row, "run-1")
    n, reason, _ = state(conn, stub)
    assert reason.startswith("robots_disallowed") and rep["degraded_to_deeplink"] is True and "/board" not in stub.paths()
    conn.execute("DELETE FROM crawl_state"); conn.execute("UPDATE org_master SET render_mode='httpx' WHERE slug='t-org'")
    stub.robots(status=500)
    org = next(t for t in orgs.crawl_targets(conn) if t["slug"] == "t-org")
    rep = run(conn, tmp_path, org, "run-2")
    assert state(conn, stub)[:2] == (1, None) and rep["degraded_to_deeplink"] is False        # transient: counted, not blocked
    assert "/board" not in stub.paths()


def test_robots_denied_carries_its_reason():
    from ajp.robots import RobotsCache
    c = RobotsCache(lambda origin: (200, "User-agent: *\nDisallow: /x\n"))
    with pytest.raises(RobotsDenied) as e:
        c.require("https://a.invalid/x", "ua")
    assert e.value.reason == "disallowed"
    c = RobotsCache(lambda origin: (503, ""))
    with pytest.raises(RobotsDenied) as e:
        c.require("https://a.invalid/x", "ua")
    assert e.value.reason == "unavailable"


# ---------------------------------------------------------------- JS shell heuristic -----
SHELL = '<html><head><script src="/a.js"></script><script>boot()</script></head><body><div id="root"></div><noscript>Enable JavaScript</noscript></body></html>'


def test_js_shell_detection():
    assert board.looks_like_js_shell(SHELL)
    assert not board.looks_like_js_shell("<html><body><main>" + "Конкурс за избор у звање. " * 40 + "</main><script>a()</script><script>b()</script></body></html>")
    assert not board.looks_like_js_shell("<html><body><p>Кратка страница</p></body></html>")            # short but no scripts / mount point


# ---------------------------------------------------------------- render path (real Chromium) ---
JS_BOARD = ('<html><body><div id="root"></div><script>document.addEventListener("DOMContentLoaded",function(){'
            'document.getElementById("root").innerHTML=\'<article><h3><a href="/a/1">Конкурс за избор у звање доцента за област Математика</a></h3>'
            '<time datetime="2026-09-14">14.09.2026.</time></article>\';});</script>'
            '<img src="/private/track.png"><script src="/private/tracker.js"></script></body></html>').encode()


@pytest.fixture(scope="module")
def chromium_ok():
    s = Stub()
    try:
        s.robots("")
        r = PlaywrightRenderer(PoliteClient(rps=1000, sleep=lambda x: None), board.OrgManifest.for_hosts([f"127.0.0.1:{s.port}"]))
        r._ensure(); r.close()
    except RenderUnavailable as e:
        pytest.skip(str(e))
    finally:
        s.stop()


def renderer_factory(client, manifest):
    return PlaywrightRenderer(client, manifest, settle_ms=150, timeout_ms=15000)


def test_httpx_sees_an_empty_shell_and_the_browser_reads_the_board(chromium_ok, stub, tmp_path):
    stub.robots("User-agent: *\nDisallow: /private/\n").route("/board", body=JS_BOARD).route("/private/track.png").route("/private/tracker.js")
    store = LocalFsStore(tmp_path)
    plain = board.BoardConnector(fast(), store, org_for(stub))
    rows, rep = plain.crawl()
    assert rows == [] and rep["rendered"] == 0                                        # no renderer configured: nothing found, nothing invented
    c = board.BoardConnector(fast(), store, org_for(stub), renderer_factory=renderer_factory)
    try:
        rows, rep = c.crawl()
    finally:
        c.close()
    assert len(rows) == 1 and rep["rendered"] == 1 and rows[0]["kind"] == "employment" and rows[0]["title_raw"].startswith("Конкурс за избор")
    _, meta = get_raw(store, rows[0]["raw_html_ref"])
    assert meta["extra"] == {"render": "playwright"} and meta["robots_allowed"] is True and meta["http_status"] == 200
    assert not [p for p in stub.paths() if p.startswith("/private/")]                 # robots-denied subresources never requested


def test_render_never_requests_robots_denied_or_offorigin_urls(chromium_ok, stub):
    other = Stub(); other.robots("").route("/x.png")
    try:
        stub.robots("User-agent: *\nDisallow: /private/\n").route("/ok", body=f'<html><body><img src="{other.base}/x.png"><script>fetch("/private/api")</script>ok</body></html>'.encode())
        stub.route("/private/page").route("/redir", status=302, headers={"Location": "/private/target"}).route("/private/target")
        manifest = board.OrgManifest.for_hosts([f"127.0.0.1:{stub.port}"])
        with PlaywrightRenderer(fast(), manifest, settle_ms=100) as r:
            assert r.render(stub.base + "/ok").status == 200
            with pytest.raises(RobotsDenied):
                r.render(stub.base + "/private/page")                                 # refused before the browser is involved
            with pytest.raises(FetchFailed):
                r.render(stub.base + "/redir")                                        # redirect hop into a denied path is aborted
        assert not [p for p in stub.paths() if p.startswith("/private/")] and other.hits == []
        assert {d[2] for d in r.denied} <= {"ManifestViolation", "RobotsDenied"} and r.denied
        assert "/private/target" in [d[1].replace(stub.base, "") for d in r.denied]                  # the redirect hop itself was refused
    finally:
        other.stop()


def test_transport_error_falls_back_to_the_browser_but_http_errors_do_not(chromium_ok, stub, tmp_path):
    stub.robots("").route("/board", body=JS_BOARD.replace(b'<img src="/private/track.png"><script src="/private/tracker.js"></script>', b""))
    calls = []

    class Legacy(board.BoardConnector):
        mode = "tls"

        def fetch_raw(self, url, kind="html", max_bytes=None):
            calls.append(url)
            if self.mode == "tls":
                raise FetchFailed("giving up") from httpx.ConnectError("[SSL: DH_KEY_TOO_SMALL] dh key too small")   # R2 legacy-TLS shape
            raise FetchFailed("HTTP 503") from None
    c = Legacy(fast(), LocalFsStore(tmp_path), org_for(stub), renderer_factory=renderer_factory)
    try:
        rows, rep = c.crawl()
        assert len(rows) == 1 and rep["rendered"] == 1
        c.mode = "http"
        rows, rep = c.crawl()
        assert rows == [] and rep["rendered"] == 0 and rep["blocked"][0]["reason"].startswith("FetchFailed")       # HTTP errors are not worked around
    finally:
        c.close()


def test_render_mode_playwright_goes_straight_to_the_browser(chromium_ok, stub, tmp_path):
    stub.robots("").route("/board", body=JS_BOARD.replace(b'<img src="/private/track.png"><script src="/private/tracker.js"></script>', b""))
    org = dict(org_for(stub), render_mode="playwright")
    c = board.BoardConnector(fast(), LocalFsStore(tmp_path), org, renderer_factory=renderer_factory)
    try:
        rows, rep = c.crawl()
    finally:
        c.close()
    assert len(rows) == 1 and rep["rendered"] == 1 and stub.paths().count("/board") == 1      # one document request: the browser's own


def test_tls_validation_is_never_disabled_in_the_renderer():
    import re
    from pathlib import Path
    src = (Path(board.__file__).parent / "render.py").read_text()
    assert re.findall(r"ignore_https_errors\s*=\s*(\w+)", src) == ["False"]


def test_xhr_redirects_iframes_and_popups_cannot_get_around_the_gate(chromium_ok, stub):
    stub.robots("User-agent: *\nDisallow: /private/\n")
    stub.route("/xhr-redirect", status=302, headers={"Location": "/private/via-xhr"}).route("/private/via-xhr", body=b"SECRET")
    stub.route("/frame", body=b"<html><body>iframe content</body></html>").route("/private/popup", body=b"SECRET")
    stub.route("/page", body=('<html><body>hi<script>fetch("/xhr-redirect").catch(()=>{});'
                              'var f=document.createElement("iframe");f.src="/frame";document.body.appendChild(f);'
                              'window.open("/private/popup");</script></body></html>').encode())
    manifest = board.OrgManifest.for_hosts([f"127.0.0.1:{stub.port}"])
    with PlaywrightRenderer(fast(), manifest, settle_ms=400) as r:
        assert r.render(stub.base + "/page").status == 200
    paths = stub.paths()
    assert "/private/via-xhr" not in paths and "/private/popup" not in paths                        # redirected XHR + popup never fetched
    assert "/frame" not in paths                                                                     # iframes are never loaded
    assert any(d[1].endswith("/private/via-xhr") for d in r.denied)


def test_render_rate_limits_redirect_hops(chromium_ok, stub):
    stub.robots("").route("/a", status=302, headers={"Location": "/b"}).route("/b", body=b"<html><body>done</body></html>")
    waits = []
    client = PoliteClient(rps=1000, sleep=lambda s: None)
    client.wait_turn = lambda url: waits.append(url)
    manifest = board.OrgManifest.for_hosts([f"127.0.0.1:{stub.port}"])
    with PlaywrightRenderer(client, manifest, settle_ms=100) as r:
        assert r.render(stub.base + "/a").final_url.endswith("/b")
    assert [w.replace(stub.base, "") for w in waits] == ["/a", "/b"]                                  # initial navigation + the redirect hop


def test_empty_rendered_board_degrades_to_deeplink_after_three_runs(conn, stub, tmp_path):
    """PM addendum (GRF): a board that loads but yields no classifiable items for 3 consecutive runs becomes a deeplink."""
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, homepage, host, career_urls, render_mode, robots_status, crawl_config)
                    VALUES ('grf-like','GRF-like','RS','faculty',%s,'127.0.0.1',%s,'httpx','allowed','{"empty_is_failure": true}')""",
                 (stub.base + "/", [stub.base + "/board"]))
    stub.route("/board", body=b"<html><body><main><p>No announcements yet.</p></main></body></html>")
    org = next(t for t in orgs.crawl_targets(conn) if t["slug"] == "grf-like")
    reps = [board.crawl_orgs(conn, fast(), LocalFsStore(tmp_path), [org], f"run-{i}")[0] for i in (1, 2, 3)]
    assert [r["degraded_to_deeplink"] for r in reps] == [False, False, True]
    assert "no listing rows" in conn.execute("SELECT last_error FROM crawl_state WHERE key=%s", (stub.base + "/board",)).fetchone()[0]
    # and an org WITHOUT the flag is left alone when its board is legitimately empty
    conn.execute("UPDATE org_master SET crawl_config='{}', render_mode='httpx' WHERE slug='grf-like'")
    conn.execute("DELETE FROM crawl_state")
    org = dict(next(t for t in orgs.crawl_targets(conn) if t["slug"] == "grf-like"))
    assert board.crawl_orgs(conn, fast(), LocalFsStore(tmp_path), [org], "run-9")[0]["blocked"] == []


def test_board_with_only_excluded_person_specific_notices_counts_as_empty(conn, stub, tmp_path):
    """PM addenda 2 (TMF): a board that only carries person-specific review reports has nothing open; after 3 runs -> deeplink."""
    reports = "".join(f'<article><h3><a href="/2026/09/{i:02d}/r{i}/">Извештај комисије о избору у звање кандидата број {i}</a></h3></article>' for i in range(1, 5))
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, homepage, host, career_urls, render_mode, robots_status, crawl_config)
                    VALUES ('tmf-like','TMF-like','RS','faculty',%s,'127.0.0.1',%s,'httpx','allowed','{"empty_is_failure": true}')""",
                 (stub.base + "/", [stub.base + "/board"]))
    stub.route("/board", body=f"<html><body><main>{reports}</main></body></html>".encode())
    org = next(t for t in orgs.crawl_targets(conn) if t["slug"] == "tmf-like")
    reps = [board.crawl_orgs(conn, fast(), LocalFsStore(tmp_path), [org], f"run-{i}")[0] for i in (1, 2, 3)]
    assert [r["items"] for r in reps] == [4, 4, 4] and [r["excluded_person_specific"] for r in reps] == [4, 4, 4] and all(r["rows"] == 0 for r in reps)
    assert [r["degraded_to_deeplink"] for r in reps] == [False, False, True]


# ---------------------------------------------------------------- detail-page deadline enrichment ------
_TODAY = datetime.now(timezone.utc).date().isoformat()   # anchor = today, so "+15 days" is always open regardless of when the suite runs
_EXPECTED_DEADLINE = (datetime(*map(int, _TODAY.split("-")), 23, 59, 59, tzinfo=ZoneInfo("Europe/Belgrade")) + timedelta(days=15)).astimezone(timezone.utc)
DETAIL_WITH_DEADLINE = ('<html><body><main><h1>Конкурс за избор у звање доцента за област Физика</h1>'
                        f'<time datetime="{_TODAY}">{_TODAY}</time>'
                        '<p>УСЛОВИ: докторат. Рок за подношење пријаве је 15 дана од дана објављивања.</p>'
                        '<p>Контакт: <a href="mailto:kadrovska@primer.invalid">kadrovska@primer.invalid</a></p>'
                        '</main></body></html>').encode()
DETAIL_NO_RULE = '<html><body><main><h1>Конкурс за избор</h1><p>Пријаве се примају у писарници факултета.</p></main></body></html>'.encode()


def board_page(item_url):
    return f'<html><body><main><article><h3><a href="{item_url}">Конкурс за избор у звање доцента за област Физика</a></h3></article></main></body></html>'.encode()


def test_deadline_enrichment_fetches_the_row_url_once_and_sets_deadline_and_status(stub, tmp_path):
    stub.robots("").route("/board", body=board_page("/detail/1")).route("/detail/1", body=DETAIL_WITH_DEADLINE)
    store = LocalFsStore(tmp_path)
    c = board.BoardConnector(fast(), store, org_for(stub))
    rows, rep = c.crawl()
    assert len(rows) == 1 and rows[0]["deadline"] == _EXPECTED_DEADLINE
    assert rows[0]["status"] == "open" and rep["deadlines_found"] == 1 and rep["deadline_fetch_errors"] == 0
    assert stub.paths().count("/detail/1") == 1                                      # exactly one detail fetch
    # T26 (packet 015): the same detail fetch also extracts a real contact, and apply_url is always set
    assert rows[0]["contact_emails"] == ["kadrovska@primer.invalid"]
    assert rows[0]["apply_url"] == rows[0]["url"]


def test_a_row_with_an_already_known_deadline_still_gets_contact_enrichment(stub, tmp_path):
    """rows_from_items() itself never sets row['deadline'] today (only _enrich_deadlines does) - but this
    function's own deadline-vs-contact skip logic is tested directly here regardless, as a real guard against
    a future regression where some OTHER step starts pre-populating a deadline: contacts are a separate
    concern from the deadline and must never be skipped just because the deadline happens to be known
    already."""
    stub.robots("").route("/detail/1", body=DETAIL_WITH_DEADLINE)
    c = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub))
    rows = [{"url": stub.base + "/detail/1", "kind": "employment", "extraction_family": "article",
            "deadline": _EXPECTED_DEADLINE, "raw_text": ""}]
    report = {}
    c._enrich_deadlines(rows, report)
    assert rows[0]["deadline"] == _EXPECTED_DEADLINE and report["deadlines_found"] == 0   # never overwritten
    assert rows[0]["contact_emails"] == ["kadrovska@primer.invalid"]                       # still extracted
    assert stub.paths().count("/detail/1") == 1


def test_no_rule_on_the_page_leaves_deadline_absent(stub, tmp_path):
    stub.robots("").route("/board", body=board_page("/detail/1")).route("/detail/1", body=DETAIL_NO_RULE)
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
    assert "deadline" not in rows[0] and rep["deadlines_found"] == 0


def test_detail_fetch_failure_does_not_fail_the_row_or_crawl(stub, tmp_path):
    stub.robots("").route("/board", body=board_page("/detail/1")).route("/detail/1", status=500)
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
    assert len(rows) == 1 and "deadline" not in rows[0] and rep["deadline_fetch_errors"] == 1


def test_detail_host_must_be_in_the_org_family_and_off_family_detail_is_never_fetched(stub, tmp_path):
    other = Stub(); other.robots("").route("/detail/1", body=DETAIL_WITH_DEADLINE)
    try:
        stub.robots("").route("/board", body=board_page(other.base + "/detail/1"))
        rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
        assert len(rows) == 1 and "deadline" not in rows[0] and rep["deadline_fetch_errors"] == 1 and other.hits == []
    finally:
        other.stop()


def test_document_rows_are_never_sent_to_deadline_enrichment_but_ARE_now_fetched_for_their_own_content(stub, tmp_path):
    """T31 (packet 019): a 'document' row is still never sent through _enrich_deadlines (that pass looks for
    an HTML detail page's own deadline sentence, which a PDF/DOCX attachment is not) - but IS now fetched
    exactly once for its own body text via the new _enrich_documents pass (the entire point of T31). Pre-T31
    this asserted the OPPOSITE ('no extra request at all for the document family') - deliberately superseded."""
    html = '<html><body><main><ul><li><a href="/docs/konkurs_16092026.pdf">Конкурс за избор у звање доцента</a></li></ul></main></body></html>'
    stub.robots("").route("/board", body=html.encode()).route("/docs/konkurs_16092026.pdf", body=b"%PDF-not-real-but-fetched")
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
    assert {r["extraction_family"] for r in rows} == {"document"} and rep["deadlines_found"] == 0
    assert rep["documents_attempted"] == 1
    assert "/docs/konkurs_16092026.pdf" in stub.paths()                      # the document IS fetched now (T31)
    assert not [p for p in stub.paths() if p not in ("/robots.txt", "/board", "/docs/konkurs_16092026.pdf")]


def test_anchor_rows_are_never_sent_to_detail_enrichment(stub, tmp_path):
    html = ('<html><body><main><p><a href="/oglasi/konkurs-za-izbor-docenta-fizika">Конкурс за избор у звање доцента за област X</a> '
            '<span>01.09.2026.</span></p></main></body></html>')
    stub.robots("").route("/board", body=html.encode())
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org_for(stub)).crawl()
    assert {r["extraction_family"] for r in rows} == {"anchor"} and rep["deadlines_found"] == 0
    assert not [p for p in stub.paths() if p not in ("/robots.txt", "/board")]        # no extra request for the anchor family


def test_row_on_a_detail_url_shared_by_two_board_pages_fetches_it_once(stub, tmp_path):
    """Two of the organisation's own board pages happen to link the same detail page (a common cross-post)."""
    item = lambda label: f'<html><body><main><article><h3><a href="/detail/1">Конкурс за избор у звање доцента за област Физика{label}</a></h3></article></main></body></html>'
    stub.robots("").route("/board", body=item("").encode()).route("/board2", body=item(" (2)").encode()).route("/detail/1", body=DETAIL_WITH_DEADLINE)
    org = dict(org_for(stub), career_urls=[stub.base + "/board", stub.base + "/board2"])
    rows, rep = board.BoardConnector(fast(), LocalFsStore(tmp_path), org).crawl()
    assert len(rows) == 2 and all(r["status"] == "open" for r in rows) and stub.paths().count("/detail/1") == 1


def test_deadline_from_detail_page_pure_function():
    assert board.deadline_from_detail_page(DETAIL_NO_RULE.decode()) is None
    d, status = board.deadline_from_detail_page(DETAIL_WITH_DEADLINE.decode(), now=datetime.now(timezone.utc))
    assert d == _EXPECTED_DEADLINE and status == "open"
    d, status = board.deadline_from_detail_page(DETAIL_WITH_DEADLINE.decode(), now=_EXPECTED_DEADLINE + timedelta(days=1))
    assert status == "expired"
    # a page with the deadline RULE but no date anywhere (no <time>, no fallback) cannot anchor it: None
    assert board.deadline_from_detail_page('<html><body><p>Рок за подношење пријаве је 15 дана од дана објављивања.</p></body></html>') is None
    fb = datetime(2026, 9, 1, tzinfo=timezone.utc)
    d, status = board.deadline_from_detail_page('<html><body><p>Рок за подношење пријаве је 15 дана од дана објављивања.</p></body></html>', fallback_posted=fb, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert d == datetime(2026, 9, 16, 21, 59, 59, tzinfo=timezone.utc) and status == "expired"


# ---------------------------------------------------------------- delayed-hydration wait (PM: agrif) -----------
DELAYED_BOARD = (
    '<html><body><div id="root"></div><script>'
    'setTimeout(function(){'
    'document.getElementById("root").innerHTML=\'<article><h3><a href="/a/1">Конкурс за избор сарадника ван радног односа</a></h3>'
    '<time datetime="2026-09-14">14.09.2026.</time></article>\';'
    '}, 900);'
    '</script></body></html>'
).encode()


def test_render_waits_past_a_fixed_short_settle_for_delayed_hydration(chromium_ok, stub):
    """Regression: agrif's board populated the DOM ~0.9-1.5s after 'load'; a fixed short sleep returned too early."""
    stub.robots("").route("/board", body=DELAYED_BOARD)
    manifest = board.OrgManifest.for_hosts([f"127.0.0.1:{stub.port}"])
    with PlaywrightRenderer(fast(), manifest, settle_ms=2000) as r:
        res = r.render(stub.base + "/board")
    items = board.parse_items(res.content.decode(), res.final_url)
    assert len(items) == 1 and "Конкурс за избор сарадника" in items[0].title


def test_render_hydration_wait_is_capped_and_never_blocks_forever(chromium_ok, stub):
    """A page that never stops growing (or never goes networkidle) must not hang the render past settle_ms."""
    stub.robots("").route("/board", body=(
        '<html><body><div id="root"></div><script>'
        'var i=0; setInterval(function(){document.getElementById("root").innerHTML += "x".repeat(50); i++;}, 50);'
        '</script></body></html>').encode())
    manifest = board.OrgManifest.for_hosts([f"127.0.0.1:{stub.port}"])
    t0 = time.time()
    with PlaywrightRenderer(fast(), manifest, settle_ms=700) as r:
        res = r.render(stub.base + "/board")
    assert res.status == 200 and time.time() - t0 < 5          # bounded, not stuck forever
