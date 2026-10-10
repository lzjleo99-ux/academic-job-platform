from datetime import datetime, timezone

import psycopg
import pytest
from psycopg import errors

from ajp.channels import euraxess
from ajp.connectors.base import Connector, PoliteClient, UrlManifest
from ajp.euraxess_normalize import normalize_detail
from ajp.ingest import LISTING_COLUMNS, IngestRefused, upsert_listing
from ajp.rawstore import LocalFsStore, put_raw
from stubserver import Stub
from conftest import FIXTURES

MSCA = (FIXTURES / "synthetic" / "euraxess_detail_synthetic_msca.html").read_text()
T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def add_source(conn, sid="euraxess_feed", kind="feed"):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES (%s,%s,'https://feed.invalid') ON CONFLICT DO NOTHING", (sid, kind))


def raw(store, sid="euraxess_feed", **kw):
    kw.setdefault("robots_allowed", True); kw.setdefault("status", 200)
    return put_raw(store, kind="html", source_id=sid, url="https://feed.invalid/1", content=MSCA.encode(), fetched_at=T, **kw).key


# ------------------------------------------------------------------ deeplink channel -----
def test_seed_is_idempotent_and_only_curated_links(conn):
    euraxess.seed(conn); euraxess.seed(conn)
    assert conn.execute("SELECT kind, robots_ok FROM source WHERE id='euraxess'").fetchone() == ("channel", False)
    rows = conn.execute("SELECT slug, url, country_iso2, opens_externally FROM channel_link "
                        "WHERE source_id='euraxess' ORDER BY slug").fetchall()
    assert rows == [("jobs-landing", "https://euraxess.ec.europa.eu/jobs", None, True),
                    ("serbia-jobs", "https://euraxess.ec.europa.eu/jobs/search?f%5B0%5D=job_country%3A786", "RS", True)]
    assert all("opens on EURAXESS" in l for (l,) in conn.execute("SELECT label FROM channel_link WHERE source_id='euraxess'"))


def test_channel_source_can_never_have_listings(conn):
    euraxess.seed(conn)
    with pytest.raises(errors.CheckViolation, match="deeplink channel"):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1','euraxess','u','u','t','RS','phd')""")


# ------------------------------------------------------------------ euraxess.rs (T19, need_input escalation) -----
def test_seed_also_registers_the_serbian_national_portal_as_its_own_deeplink_channel(conn):
    """euraxess.rs (a genuinely separate host from the EU-wide one above) - /jobs is Drupal Antibot
    bot-challenge-walled (live-verified 2026-09-23), never bypassed; kept in this same module so the
    governance guard's 'one canonical place for EURAXESS URLs' stays literally true."""
    euraxess.seed(conn); euraxess.seed(conn)
    assert conn.execute("SELECT kind, robots_ok, geo_scope FROM source WHERE id='euraxess_rs'").fetchone() == ("channel", False, "RS")
    rows = conn.execute("SELECT slug, url, country_iso2 FROM channel_link WHERE source_id='euraxess_rs' ORDER BY slug").fetchall()
    assert rows == [("serbia-jobs", "https://euraxess.rs/jobs/search/country/serbia-1097", "RS")]
    with pytest.raises(errors.CheckViolation, match="deeplink channel"):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1','euraxess_rs','u','u','t','RS','phd')""")


def test_channel_links_only_for_channel_sources(conn):
    add_source(conn)
    with pytest.raises(errors.CheckViolation, match="not a channel"):
        conn.execute("INSERT INTO channel_link (source_id, slug, label, url) VALUES ('euraxess_feed','x','l','https://x.invalid')")


def test_channel_connector_cannot_fetch():
    class C(Connector):
        source_id, kind = "euraxess", "channel"
    with pytest.raises(TypeError):
        C(PoliteClient(), None)


# ------------------------------------------------------------------ ingest gate ----------
def test_pgvector_migration_is_guarded(conn):
    have = conn.execute("SELECT count(*) FROM pg_available_extensions WHERE name='vector'").fetchone()[0]
    on = conn.execute("SELECT count(*) FROM pg_extension WHERE extname='vector'").fetchone()[0]
    assert on == have                              # enabled iff the server offers it; no vector columns yet


def test_listing_columns_cover_every_writable_column(conn):
    cols = {r[0] for r in conn.execute("""SELECT column_name FROM information_schema.columns
                                          WHERE table_name='listing' AND is_generated='NEVER'""")}
    managed = {"id", "first_seen", "last_seen", "updated_at", "sources"}   # T13: sources[] is upsert_listing_deduped's own
    # T39 (packet 022): inferred_disciplines/inferred_skill_terms/inferred_at are computed INSIDE
    # upsert_listing itself from the row's own title_raw/requirements_raw/raw_text (see its own comment) -
    # a connector never states them directly, same convention as sources[] above.
    managed |= {"inferred_disciplines", "inferred_skill_terms", "inferred_at"}
    assert set(LISTING_COLUMNS) == cols - managed


def test_upsert_listing_populates_the_inferred_signal_columns_from_the_shared_functions(conn, tmp_path):
    """T39 (packet 022): every connector's own row, once it reaches the ONE shared upsert_listing path,
    gets inferred_disciplines/inferred_skill_terms/inferred_at populated automatically - never a per-
    connector edit, and never a value a connector could hand-state itself (see LISTING_COLUMNS' own
    comment)."""
    add_source(conn); store = LocalFsStore(tmp_path)
    row = dict(ext_uid="t39-1", source_id="euraxess_feed", url="https://feed.invalid/t39",
              title_raw="PhD Position in Mechanical Engineering and Robotics",
              requirements_raw="Experience with SolidWorks required", raw_text="finite element analysis work",
              position_kind="phd", raw_html_ref=raw(store))
    lid, inserted = upsert_listing(conn, row, store)
    assert inserted
    disc, skills, inferred_at = conn.execute(
        "SELECT inferred_disciplines, inferred_skill_terms, inferred_at FROM listing WHERE id=%s", (lid,)
    ).fetchone()
    # "robotics" is a keyword in BOTH the Natural sciences and Engineering and technology buckets.
    assert set(disc["buckets"]) == {"Natural sciences", "Engineering and technology"}
    assert disc["zone"] == "title"
    assert {"term": "mechanical engineering", "zone": "title"} in skills
    assert {"term": "robotics", "zone": "title"} in skills
    assert {"term": "solidworks", "zone": "requirements"} in skills
    assert {"term": "finite element", "zone": "body"} in skills
    assert inferred_at is not None
    # a re-crawl/refresh recomputes them too, from the NEW text - never a stale value left behind.
    row2 = dict(row, title_raw="PhD Position in Experimental Psychology")
    lid2, inserted2 = upsert_listing(conn, row2, store)
    assert lid2 == lid and not inserted2
    disc2 = conn.execute("SELECT inferred_disciplines FROM listing WHERE id=%s", (lid,)).fetchone()[0]
    assert disc2 == {"buckets": ["Social sciences"], "zone": "title"}


def test_normalized_synthetic_row_ingests_and_reingest_updates(conn, tmp_path):
    add_source(conn); store = LocalFsStore(tmp_path)
    n = normalize_detail(MSCA, ext_uid="SYN-1", url="https://feed.invalid/1", raw_html_ref=raw(store), now=T)
    lid, inserted = upsert_listing(conn, n.row, store)
    assert inserted
    row = dict(n.row, title_raw="Doctoral Fellowship (updated)")
    lid2, inserted2 = upsert_listing(conn, row, store)
    assert lid2 == lid and not inserted2
    t, fs, ls, salary, sig = conn.execute("SELECT title_raw, first_seen, last_seen, salary_min, salary_sig FROM listing WHERE id=%s", (lid,)).fetchone()
    assert t.endswith("(updated)") and ls >= fs and salary == 43000 and sig.startswith("43000.00|43000.00|EUR|year")
    assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1
    assert conn.execute("SELECT url_canon, title_norm_hash FROM listing WHERE id=%s", (lid,)).fetchone()[0] == "https://feed.invalid/1"


def base_row(ref, **kw):
    return dict(ext_uid="1", source_id="euraxess_feed", url="https://feed.invalid/1", title_raw="T",
                country_iso2="RS", position_kind="phd", raw_html_ref=ref, **kw)


def test_ingest_refuses_bad_envelopes(conn, tmp_path):
    add_source(conn); store = LocalFsStore(tmp_path)
    for label, ref in [("no robots flag", raw(store, robots_allowed=None)), ("robots false", raw(store, robots_allowed=False)),
                       ("404", raw(store, status=404)), ("no status", raw(store, status=None)),
                       ("wrong source", raw(store, sid="other")), ("missing object", "raw_html/v1/euraxess_feed/2026/09/22/nope.html.gz")]:
        with pytest.raises(IngestRefused):
            upsert_listing(conn, base_row(ref), store)
    assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 0


def test_ingest_refuses_missing_fields_unknown_columns_channels_and_unknown_sources(conn, tmp_path):
    add_source(conn); euraxess.seed(conn); store = LocalFsStore(tmp_path)
    ok = raw(store)
    with pytest.raises(IngestRefused, match="missing required"):
        upsert_listing(conn, {k: v for k, v in base_row(ok).items() if k != "raw_html_ref"}, store)
    with pytest.raises(IngestRefused, match="unknown or database-managed"):
        upsert_listing(conn, base_row(ok, salary_sig="x"), store)
    with pytest.raises(IngestRefused, match="deeplink channel"):
        upsert_listing(conn, dict(base_row(raw(store, sid="euraxess")), source_id="euraxess"), store)
    with pytest.raises(IngestRefused, match="unknown source"):
        upsert_listing(conn, dict(base_row(ok), source_id="ghost"), store)


def test_end_to_end_stub_fetch_then_ingest(conn, tmp_path):
    """Connector fetch (robots-gated, envelope written) -> ingest, against a real local HTTP server."""
    stub = Stub()
    try:
        stub.robots("").route("/ad/1", body=MSCA.encode())
        add_source(conn, "demo_board", "scrape_html")

        class Demo(Connector):
            source_id, kind = "demo_board", "scrape_html"
            manifest = UrlManifest.exact(stub.base + "/ad/1")
        store = LocalFsStore(tmp_path)
        res, ref = Demo(PoliteClient(rps=1000), store).fetch_raw(stub.base + "/ad/1")
        n = normalize_detail(res.content.decode(), ext_uid="e2e", url=stub.base + "/ad/1", raw_html_ref=ref.key,
                             source_id="demo_board", now=T)
        assert upsert_listing(conn, n.row, store)[1] is True
    finally:
        stub.stop()
