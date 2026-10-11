"""T13 acceptance: the same position may appear on multiple aggregators (Nature + jobs.ac.uk + an employer's
own board) - merge by (a) exact canonical URL or (b) normalized title+employer+location within +/-5 days, one
listing with sources[] appended, the canonical row's own fields never overwritten by a later merge."""
from datetime import datetime, timedelta, timezone

from ajp.ingest import find_cross_source_duplicate, upsert_listing, upsert_listing_deduped
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def add_source(conn, sid):
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES (%s,'feed','https://x.invalid') ON CONFLICT DO NOTHING", (sid,))


def raw(store, sid, path="/1", **kw):
    kw.setdefault("robots_allowed", True); kw.setdefault("status", 200)
    return put_raw(store, kind="html", source_id=sid, url=f"https://x.invalid{path}", content=b"x", fetched_at=NOW, **kw).key


def row(sid, ref, **kw):
    kw.setdefault("ext_uid", "1")
    kw.setdefault("url", f"https://x.invalid/{sid}")
    kw.setdefault("title_raw", "PhD Position in Robotics")
    return dict(source_id=sid, country_iso2="NL", position_kind="phd", raw_html_ref=ref, **kw)


def test_exact_url_canon_match_across_sources_merges_into_one_listing(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    same_url = "https://employer.invalid/jobs/123"
    id1, action1 = upsert_listing_deduped(conn, row("a", raw(store, "a"), url=same_url, org_raw="TU Delft"), store)
    id2, action2 = upsert_listing_deduped(conn, row("b", raw(store, "b"), url=same_url, org_raw="TU Delft"), store)
    assert action1 == "inserted" and action2 == "merged" and id1 == id2
    assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1
    sources = conn.execute("SELECT sources FROM listing WHERE id=%s", (id1,)).fetchone()[0]
    assert {s["source"] for s in sources} == {"a", "b"}


def test_fuzzy_match_same_title_employer_city_within_5_days_merges(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    id1, _ = upsert_listing_deduped(conn, row("a", raw(store, "a"), org_raw="TU Delft", city="Delft",
                                              posted_at=NOW), store)
    id2, action = upsert_listing_deduped(conn, row("b", raw(store, "b"), org_raw="TU Delft", city="Delft",
                                                   posted_at=NOW + timedelta(days=3)), store)
    assert action == "merged" and id1 == id2


def test_outside_5_day_window_or_different_employer_is_not_merged(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    id1, _ = upsert_listing_deduped(conn, row("a", raw(store, "a"), org_raw="TU Delft", city="Delft", posted_at=NOW), store)
    id2, action = upsert_listing_deduped(conn, row("b", raw(store, "b"), org_raw="TU Delft", city="Delft",
                                                   posted_at=NOW + timedelta(days=10)), store)
    assert action == "inserted" and id1 != id2
    id3, action3 = upsert_listing_deduped(conn, row("b", raw(store, "b", path="/3"), ext_uid="3", org_raw="Leiden University",
                                                    city="Delft", posted_at=NOW), store)
    assert action3 == "inserted" and id3 not in (id1, id2)


def test_recrawling_a_row_whose_sources_array_is_empty_seeds_it_instead_of_crashing(conn, tmp_path):
    """F14 (found 2026-10-11): the re-crawl branch set
    `sources = (SELECT jsonb_agg(...) FROM jsonb_array_elements(sources) s)` - a bare aggregate, which is
    NULL over an EMPTY array (jsonb_array_elements('[]') yields no rows at all). That UPDATE then violated
    listing.sources' own NOT NULL constraint (0014) with a NotNullViolation, aborting the ENTIRE source run
    mid-crawl (every remaining row in it lost) - and three such runs block the source altogether.
    Such rows really exist: every listing predating migration 0014 (its DEFAULT '[]', with no backfill),
    and every row board.crawl_orgs() wrote through plain upsert_listing (fixed in the same sweep).
    Reproduced against the old code: psycopg.errors.NotNullViolation on the second call below."""
    add_source(conn, "a")
    store = LocalFsStore(tmp_path)
    lid, _ = upsert_listing(conn, row("a", raw(store, "a")), store)      # plain upsert: sources stays '[]'
    assert conn.execute("SELECT sources FROM listing WHERE id=%s", (lid,)).fetchone()[0] == []
    lid2, action = upsert_listing_deduped(conn, row("a", raw(store, "a", "/2")), store)   # same (source, ext_uid)
    assert (lid2, action) == (lid, "refreshed")
    sources = conn.execute("SELECT sources FROM listing WHERE id=%s", (lid,)).fetchone()[0]
    assert [s["source"] for s in sources] == ["a"]        # seeded with its own entry - never NULL


def test_merge_never_overwrites_the_canonical_rows_own_fields(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    same_url = "https://employer.invalid/jobs/999"
    id1, _ = upsert_listing_deduped(conn, row("a", raw(store, "a"), url=same_url, org_raw="TU Delft",
                                              title_raw="Original Title From Source A"), store)
    upsert_listing_deduped(conn, row("b", raw(store, "b"), url=same_url, org_raw="TU Delft",
                                     title_raw="A Totally Different Title From Source B"), store)
    title = conn.execute("SELECT title_raw FROM listing WHERE id=%s", (id1,)).fetchone()[0]
    assert title == "Original Title From Source A"          # never overwritten by the merge


def test_recrawl_from_the_same_source_does_not_duplicate_the_sources_entry(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    same_url = "https://employer.invalid/jobs/1"
    id1, _ = upsert_listing_deduped(conn, row("a", raw(store, "a"), url=same_url, org_raw="TU Delft"), store)
    upsert_listing_deduped(conn, row("b", raw(store, "b"), url=same_url, org_raw="TU Delft"), store)
    # source 'b' merges again (e.g. a later scheduled crawl re-finding the same posting) - no duplicate entry
    upsert_listing_deduped(conn, row("b", raw(store, "b", path="/1b"), url=same_url, org_raw="TU Delft"), store)
    sources = conn.execute("SELECT sources FROM listing WHERE id=%s", (id1,)).fetchone()[0]
    assert len(sources) == 2 and {s["source"] for s in sources} == {"a", "b"}


def test_cross_source_triple_all_three_merge_into_one_listing(conn, tmp_path):
    """The exact scenario T13's own acceptance criteria names: the same position on Nature + jobs.ac.uk + the
    employer's own board (here standing in for 3 arbitrary sources 'nature'/'jau'/'employer') - one listing,
    sources[] holding all three, in whatever order they were seen."""
    for sid in ("nature", "jau", "employer"):
        add_source(conn, sid)
    store = LocalFsStore(tmp_path)
    same_url = "https://employer.invalid/jobs/postdoc-42"
    id_a, action_a = upsert_listing_deduped(conn, row("nature", raw(store, "nature"), url=same_url, org_raw="MIT"), store)
    id_b, action_b = upsert_listing_deduped(conn, row("jau", raw(store, "jau"), url=same_url, org_raw="MIT"), store)
    id_c, action_c = upsert_listing_deduped(conn, row("employer", raw(store, "employer"), url=same_url, org_raw="MIT"), store)
    assert action_a == "inserted" and action_b == action_c == "merged"
    assert id_a == id_b == id_c
    assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1
    sources = conn.execute("SELECT sources FROM listing WHERE id=%s", (id_a,)).fetchone()[0]
    assert {s["source"] for s in sources} == {"nature", "jau", "employer"}
    assert all({"source", "url", "first_seen"} <= set(s) for s in sources)


def test_each_sources_entry_tracks_its_own_first_seen_and_last_seen(conn, tmp_path):
    """T14 needs a real per-source last_seen (API sources[]={source_id,url,first_seen,last_seen}), not just the
    listing row's own last_seen - a re-crawl (by either the original source or a later cross-source merge partner)
    must move only that entry's last_seen forward, never its first_seen, and never touch the other entries."""
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    same_url = "https://employer.invalid/jobs/777"
    id1, _ = upsert_listing_deduped(conn, row("a", raw(store, "a"), url=same_url, org_raw="TU Delft"), store)
    id2, _ = upsert_listing_deduped(conn, row("b", raw(store, "b"), url=same_url, org_raw="TU Delft"), store)
    assert id1 == id2
    before = {s["source"]: s for s in conn.execute("SELECT sources FROM listing WHERE id=%s", (id1,)).fetchone()[0]}
    assert before["a"]["first_seen"] == before["a"]["last_seen"]
    # source 'a' is seen again (a later scheduled crawl re-finding the same posting)
    upsert_listing_deduped(conn, row("a", raw(store, "a", path="/1b"), url=same_url, org_raw="TU Delft"), store)
    after = {s["source"]: s for s in conn.execute("SELECT sources FROM listing WHERE id=%s", (id1,)).fetchone()[0]}
    assert after["a"]["first_seen"] == before["a"]["first_seen"]          # never moved
    assert after["a"]["last_seen"] >= before["a"]["last_seen"]            # moved forward (>= - same-second re-run)
    assert after["b"] == before["b"]                                     # untouched by 'a's re-crawl


def test_find_cross_source_duplicate_needs_both_employer_and_a_date_for_the_fuzzy_path(conn, tmp_path):
    add_source(conn, "a"); add_source(conn, "b"); store = LocalFsStore(tmp_path)
    upsert_listing_deduped(conn, row("a", raw(store, "a"), org_raw="TU Delft", city="Delft", posted_at=NOW), store)
    # no date at all on the candidate row - never fuzzy-matched, even with the same title+employer+city
    dup = find_cross_source_duplicate(conn, row("b", "irrelevant", ext_uid="2", url="https://x.invalid/2",
                                                org_raw="TU Delft", city="Delft"))
    assert dup is None
