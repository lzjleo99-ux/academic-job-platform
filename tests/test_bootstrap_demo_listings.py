"""ajp.bootstrap.load_demo_listings: found live testing T23's own new remote demo row (2026-09-24) - a real,
pre-existing bug where a per-item "_comment" annotation already present in dev_listings_fixture.json (on the
cross-source-dedupe demo row) was never stripped before being handed to upsert_listing(), which rejects any
key it doesn't recognise. A genuinely fresh `ajp-init`/`tools/dev_up.py` run would have crashed the moment it
reached that row - this had never been exercised by the test suite (test_api.py builds its own fixture) nor,
apparently, by a truly fresh bootstrap run since that comment was added.
"""
import pytest

from ajp.bootstrap import ensure_sources, load_demo_listings
from ajp.rawstore import LocalFsStore


@pytest.fixture()
def store(conn, tmp_path):
    """Every demo row's own source_id must already exist in `source` (upsert_listing rejects an unknown
    one) - ensure_sources() is the real, sanctioned way every one of them gets registered on a fresh
    install; load_demo_listings() is never called without it in any real bootstrap path."""
    s = LocalFsStore(tmp_path)
    ensure_sources(conn, s)
    return s


def test_load_demo_listings_succeeds_despite_underscore_prefixed_annotation_keys(conn, store):
    """The real bug: an item-level "_comment" key in the fixture JSON must never reach upsert_listing()."""
    n = load_demo_listings(conn, store)
    assert n > 0


def test_load_demo_listings_is_idempotent(conn, store):
    n1 = load_demo_listings(conn, store)
    count1 = conn.execute("SELECT count(*) FROM listing").fetchone()[0]
    n2 = load_demo_listings(conn, store)
    count2 = conn.execute("SELECT count(*) FROM listing").fetchone()[0]
    assert n1 == n2
    assert count1 == count2                      # a re-run refreshes rows, never duplicates them


def test_the_remote_demo_row_persists_with_a_null_country_and_a_real_location_class(conn, store):
    """T23 (PM ruling R2): the demo fixture's own remote row - a real, deliberately labelled example of the
    R2 policy end to end, not just exercised via a synthetic unit-test row."""
    load_demo_listings(conn, store)
    row = conn.execute(
        "SELECT country_iso2, location_class, location_class_raw FROM listing WHERE title_raw LIKE '%fully remote%'"
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] == "remote" and row[2] == "fully remote"


def test_every_demo_listing_title_carries_the_sample_label(conn, store):
    load_demo_listings(conn, store)
    titles = [r[0] for r in conn.execute("SELECT title_raw FROM listing").fetchall()]
    assert titles and all(t.startswith("[SAMPLE] ") for t in titles)


def test_every_demo_listing_with_a_real_country_is_located_never_left_unknown(conn, store):
    """T26 (packet 015), found live re-verifying the fixture's own rendering: a demo row with a real, stated
    country_iso2 but no explicit location_class in the fixture JSON was defaulting to the DB column's own
    'unknown' - looking exactly like a real R2 remote/unknown-location listing despite always having a known
    country. Every real connector's own classify_location() would call this 'located'; the demo data must
    match, or it silently misrepresents what R2 actually does for a row like this."""
    load_demo_listings(conn, store)
    rows = conn.execute("SELECT title_raw, country_iso2, location_class FROM listing").fetchall()
    assert rows
    for title, country, location_class in rows:
        if country is not None:
            assert location_class == "located", f"{title!r} has country={country!r} but location_class={location_class!r}"
        else:
            assert location_class != "located"


def test_every_demo_listing_has_an_apply_url_never_left_null(conn, store):
    """T26/T27 (packet 015): every real connector's own row builder falls back to the ad's own canonical
    url when nothing more specific is stated, so EVERY real listing always has >=1 clickable link (guarded
    by the connector-level tests) - found live sweeping the real production DB (2026-09-28) that this demo
    fixture loader, which inserts each row's JSON verbatim, never got the same fallback: 5 real demo rows
    had a persisted NULL apply_url. Must never regress, since a fresh install's own demo data is what a new
    operator sees first."""
    load_demo_listings(conn, store)
    rows = conn.execute("SELECT title_raw, apply_url FROM listing").fetchall()
    assert rows
    for title, apply_url in rows:
        assert apply_url, f"{title!r} has no apply_url at all"


def test_the_phd_demo_listing_carries_real_contact_and_apply_url_fields():
    """T26 (packet 015): the packet's own user-facing complaint - a listing detail page must show clickable
    contacts, not just a name - is only real if the demo data (what a fresh install's own UI actually shows)
    carries them too, not just the connector-level unit tests."""
    import json
    from pathlib import Path

    fixture = json.loads((Path(__file__).parents[1] / "src" / "ajp" / "seeds" / "dev_listings_fixture.json")
                         .read_text(encoding="utf-8"))
    phd = next(r for r in fixture["listings"] if r["title_raw"] == "PhD Position in Machine Learning")
    assert phd["contact_emails"] and phd["contact_phones"] and phd["apply_url"]
