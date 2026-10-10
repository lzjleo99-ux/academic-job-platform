"""T25 acceptance: 'POST /v1/applicants (multipart), GET/PATCH/DELETE /v1/applicants/{id}, POST/GET
/v1/applicants/{id}/matches'. Exercises the real pipeline (store + profile_extract + matching) through the
HTTP layer, against the fixture DB and a tmp_path applicant store root - the same convention
test_api_thesis.py already established.
"""
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.applicants import store as applicant_store
from cv_fixtures.build_fixtures import build_docx, build_pdf


@pytest.fixture()
def client(conn, tmp_path, monkeypatch):
    conn.row_factory = dict_row
    monkeypatch.setattr(applicant_store, "DEFAULT_ROOT", tmp_path / "applicant_store")
    app = create_app()

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


def _upload(client, tmp_path, builder=build_docx, filename="cv.docx"):
    path = builder(tmp_path / filename)
    with open(path, "rb") as f:
        return client.post("/v1/applicants", files={"file": (filename, f)})


def test_upload_docx_end_to_end_extracts_a_real_profile(client, tmp_path):
    r = _upload(client, tmp_path)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["profile"]["degree"]["v"] == "postdoc"
    assert body["profile"]["skills"] == ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"]


def test_upload_pdf_extracts_the_identical_golden_profile(client, tmp_path):
    r = _upload(client, tmp_path, builder=build_pdf, filename="cv.pdf")
    assert r.status_code == 200
    assert r.json()["profile"]["skills"] == ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"]


def test_upload_rejects_an_unsupported_file_type(client):
    """T33 (packet 018, Q5): 415 Unsupported Media Type, not 413 (413 means the payload itself was too
    large - factually wrong for a small file of the wrong type)."""
    r = client.post("/v1/applicants", files={"file": ("cv.txt", b"not a real cv", "text/plain")})
    assert r.status_code == 415


def test_upload_still_rejects_a_genuinely_oversize_file_as_413(client):
    oversize = b"x" * (applicant_store.MAX_UPLOAD_BYTES + 1)
    r = client.post("/v1/applicants", files={"file": ("cv.docx", oversize)})
    assert r.status_code == 413


def test_get_applicant_by_id(client, tmp_path):
    applicant_id = _upload(client, tmp_path).json()["id"]
    r = client.get(f"/v1/applicants/{applicant_id}")
    assert r.status_code == 200 and r.json()["id"] == applicant_id


def test_get_unknown_applicant_returns_404(client):
    assert client.get("/v1/applicants/00000000-0000-0000-0000-000000000000").status_code == 404


def test_patch_merges_a_correction_into_the_profile_never_replacing_it(client, tmp_path):
    applicant_id = _upload(client, tmp_path).json()["id"]
    r = client.patch(f"/v1/applicants/{applicant_id}", json={"desired_countries": ["RS", "DE"]})
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["desired_countries"] == ["RS", "DE"]
    assert profile["skills"] == ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"]  # untouched


def test_patch_unknown_applicant_returns_404(client):
    r = client.patch("/v1/applicants/00000000-0000-0000-0000-000000000000", json={"x": 1})
    assert r.status_code == 404


def test_delete_erases_the_cv_and_is_idempotent(client, tmp_path):
    applicant_id = _upload(client, tmp_path).json()["id"]
    r = client.delete(f"/v1/applicants/{applicant_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/applicants/{applicant_id}").status_code == 404
    assert client.delete(f"/v1/applicants/{applicant_id}").status_code == 404   # already gone, not an error


def test_delete_unknown_applicant_returns_404(client):
    assert client.delete("/v1/applicants/00000000-0000-0000-0000-000000000000").status_code == 404


def test_compute_matches_against_real_open_listings_ranked_by_score(client, tmp_path, conn):
    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('src_match_test','feed','https://x.invalid',true)""")
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, country_iso2,
                                position_kind, degree_required, status, apply_url)
           VALUES ('1','src_match_test','https://x.invalid/1','https://x.invalid/1',
                   'Postdoc in Quantum Physics','physics research role','DE','postdoc','phd','open',
                   'https://x.invalid/1'),
                  ('2','src_match_test','https://x.invalid/2','https://x.invalid/2',
                   'Chef position','restaurant kitchen role','FR','other','none','open','https://x.invalid/2')""")
    applicant_id = _upload(client, tmp_path).json()["id"]
    r = client.post(f"/v1/applicants/{applicant_id}/matches")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    results = body["results"]
    scores = {row["title_raw"]: row["score"] for row in results}
    assert scores["Postdoc in Quantum Physics"] > scores["Chef position"]
    # every clickable link / contact field the packet's own complaint (#4) asked for is present on a match
    top = results[0]
    assert top["apply_url"] and "factors" in top and "counts" in top and "coverage" in top


def test_matches_are_paginated_with_a_real_total_count_never_dumped_unbounded(client, tmp_path, conn):
    """T33 (packet 018, Q4): the matches endpoint must never synchronously dump every open listing with no
    bound - a real total count alongside a bounded page, limit/offset honoured."""
    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('src_match_page','feed','https://x.invalid',true)""")
    conn.cursor().executemany(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, position_kind, status, apply_url)
           VALUES (%s,'src_match_page',%s,%s,%s,'','other','open',%s)""",
        [(str(i), f"https://p.invalid/{i}", f"https://p.invalid/{i}", f"Role {i}", f"https://p.invalid/{i}")
         for i in range(5)],
    )
    applicant_id = _upload(client, tmp_path).json()["id"]
    client.post(f"/v1/applicants/{applicant_id}/matches")
    r = client.get(f"/v1/applicants/{applicant_id}/matches", params={"limit": 2, "offset": 0})
    body = r.json()
    assert r.status_code == 200
    assert body["total"] >= 5
    assert len(body["results"]) == 2
    r2 = client.get(f"/v1/applicants/{applicant_id}/matches", params={"limit": 2, "offset": 2})
    assert {row["listing_id"] for row in r2.json()["results"]} != {row["listing_id"] for row in body["results"]}


def test_get_matches_returns_the_last_computed_scores_without_recomputing(client, tmp_path, conn):
    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('src_match_test2','feed','https://x.invalid',true)""")
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, country_iso2,
                                position_kind, status, apply_url)
           VALUES ('1','src_match_test2','https://y.invalid/1','https://y.invalid/1',
                   'Postdoc role','physics','DE','postdoc','open','https://y.invalid/1')""")
    applicant_id = _upload(client, tmp_path).json()["id"]
    client.post(f"/v1/applicants/{applicant_id}/matches")
    r = client.get(f"/v1/applicants/{applicant_id}/matches")
    assert r.status_code == 200 and r.json()["total"] >= 1 and len(r.json()["results"]) >= 1


def test_matches_for_unknown_applicant_return_404(client):
    assert client.post("/v1/applicants/00000000-0000-0000-0000-000000000000/matches").status_code == 404
    assert client.get("/v1/applicants/00000000-0000-0000-0000-000000000000/matches").status_code == 404


def test_compute_matches_query_count_is_bounded_never_one_per_listing(client, tmp_path, conn, monkeypatch):
    """T33 (packet 018, Q4): 'prove it with a query-count or timing test' - the N+1 guard packet 016 already
    required (one listing query for the whole matches request, never one per listing) is proven here
    directly: the real number of DB round-trips compute_matches makes must be the SAME small constant whether
    scoring against 3 listings or 60, never growing with the listing count."""
    import psycopg
    from ajp.applicants import pipeline

    real_execute = psycopg.Connection.execute
    call_count = {"n": 0}

    def counting_execute(self, *args, **kwargs):
        call_count["n"] += 1
        return real_execute(self, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", counting_execute)

    def _seed_listings(source_id, n):
        conn.execute(f"""INSERT INTO source (id, kind, base_url, robots_ok)
                         VALUES ('{source_id}','feed','https://x.invalid',true) ON CONFLICT DO NOTHING""")
        conn.cursor().executemany(
            f"""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, position_kind, status, apply_url)
               VALUES (%s,'{source_id}',%s,%s,%s,'','other','open',%s)""",
            [(str(i), f"https://q.invalid/{source_id}/{i}", f"https://q.invalid/{source_id}/{i}", f"Role {i}",
             f"https://q.invalid/{source_id}/{i}") for i in range(n)],
        )

    applicant_id = _upload(client, tmp_path).json()["id"]

    _seed_listings("src_qcount_a", 3)
    call_count["n"] = 0
    pipeline.compute_matches(conn, applicant_id)
    small_n_calls = call_count["n"]

    _seed_listings("src_qcount_b", 60)
    call_count["n"] = 0
    pipeline.compute_matches(conn, applicant_id)
    large_n_calls = call_count["n"]

    assert small_n_calls == large_n_calls
    assert small_n_calls <= 5


def test_recomputing_matches_replaces_stale_scores_not_accumulates(client, tmp_path, conn):
    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok) VALUES ('src_match_test3','feed','https://x.invalid',true)""")
    conn.execute(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, country_iso2,
                                position_kind, status, apply_url)
           VALUES ('1','src_match_test3','https://z.invalid/1','https://z.invalid/1',
                   'Postdoc role','physics','DE','postdoc','open','https://z.invalid/1')""")
    applicant_id = _upload(client, tmp_path).json()["id"]
    client.post(f"/v1/applicants/{applicant_id}/matches")
    client.post(f"/v1/applicants/{applicant_id}/matches")
    r = client.get(f"/v1/applicants/{applicant_id}/matches")
    listing_ids = [row["listing_id"] for row in r.json()["results"]]
    assert len(listing_ids) == len(set(listing_ids))     # never duplicated by a second compute call


def test_tied_scores_break_deterministically_by_posted_at_then_listing_id(client, tmp_path, conn):
    """T37 (packet 022, R4): PM's own explicit instruction - 'identical requests must return identical
    order', with a real, deterministic precedence on an exact score tie (score, then skill_relevance points,
    then the positive-factor count, then posted_at desc, then listing id). Three listings here are
    deliberately IDENTICAL in every scored field (all genuinely sparse: no title/body content, no country,
    no salary - score_listing leaves every one of the 7 criteria 'unknown', a real, verified exact 50.0 tie
    for all three) so the ENTIRE tie-break must fall through to posted_at, then (for the two sharing even
    that) to the listing id itself."""
    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok)
                    VALUES ('src_tiebreak','feed','https://x.invalid',true)""")
    conn.cursor().executemany(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, position_kind,
                                status, apply_url, posted_at)
           VALUES (%s,'src_tiebreak',%s,%s,'Some role','','other','open',%s,%s)""",
        [
            ("tie-no-date-1", "https://tb.invalid/1", "https://tb.invalid/1", "https://tb.invalid/1", None),
            ("tie-no-date-2", "https://tb.invalid/2", "https://tb.invalid/2", "https://tb.invalid/2", None),
            ("tie-older", "https://tb.invalid/3", "https://tb.invalid/3", "https://tb.invalid/3", "2024-01-01T00:00:00+00:00"),
            ("tie-newer", "https://tb.invalid/4", "https://tb.invalid/4", "https://tb.invalid/4", "2024-06-01T00:00:00+00:00"),
        ],
    )
    applicant_id = _upload(client, tmp_path).json()["id"]
    client.post(f"/v1/applicants/{applicant_id}/matches")
    r1 = client.get(f"/v1/applicants/{applicant_id}/matches")
    r2 = client.get(f"/v1/applicants/{applicant_id}/matches")
    ids1 = [row["listing_id"] for row in r1.json()["results"]]
    ids2 = [row["listing_id"] for row in r2.json()["results"]]
    assert ids1 == ids2                                           # identical requests, identical order
    scores = {row["listing_id"]: row["score"] for row in r1.json()["results"]}
    tied_ids = {r["ext_uid"]: r["id"] for r in conn.execute(
        "SELECT id, ext_uid FROM listing WHERE source_id='src_tiebreak'").fetchall()
        if isinstance(r, dict)} or {r[1]: r[0] for r in conn.execute(
        "SELECT id, ext_uid FROM listing WHERE source_id='src_tiebreak'").fetchall()}
    for ext_uid, listing_id in tied_ids.items():
        assert scores[str(listing_id)] == 50.0                    # every one of the 4 is a real, exact tie
    # posted_at desc: the more recently-posted of the two dated rows outranks the older one.
    newer_rank = ids1.index(str(tied_ids["tie-newer"]))
    older_rank = ids1.index(str(tied_ids["tie-older"]))
    assert newer_rank < older_rank
    # a NULL posted_at never wins a tie against a real stated date - both dated rows outrank both undated ones.
    no_date_ranks = [ids1.index(str(tied_ids[k])) for k in ("tie-no-date-1", "tie-no-date-2")]
    assert older_rank < min(no_date_ranks)
    # the two undated rows, tied on everything including posted_at (both NULL), fall through to listing id.
    expected_no_date_order = sorted((str(tied_ids["tie-no-date-1"]), str(tied_ids["tie-no-date-2"])))
    actual_no_date_order = [ids1[i] for i in sorted(no_date_ranks)]
    assert actual_no_date_order == expected_no_date_order


# ---- T43 (packet 025): bulk multi-row INSERT replaces the old row-by-row executemany --------------------
def test_t43_compute_matches_persists_via_exactly_one_copy_operation_regardless_of_row_count(client, tmp_path, conn, monkeypatch):
    """T43's own core deliverable: ONE bulk COPY operation (streaming every row in a single `COPY ... FROM
    STDIN`), never one write per listing - proven directly by counting how many times `Cursor.copy()` is
    actually invoked, for both a small and a much larger listing count. (A single multi-row `INSERT ...
    VALUES (...),(...),...` was tried FIRST, per PM's own suggestion, and measured SLOWER than the
    original row-by-row `executemany` for this real data - COPY is what was actually shipped; see
    pm/outbox/t43_report.md for the full, real timing comparison of all three approaches.)"""
    import psycopg
    from ajp.applicants import pipeline

    real_copy = psycopg.Cursor.copy
    copy_count = {"n": 0}

    def counting_copy(self, *args, **kwargs):
        copy_count["n"] += 1
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "copy", counting_copy)

    def _seed_listings(source_id, n):
        conn.execute(f"""INSERT INTO source (id, kind, base_url, robots_ok)
                         VALUES ('{source_id}','feed','https://x.invalid',true) ON CONFLICT DO NOTHING""")
        conn.cursor().executemany(
            f"""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, position_kind, status, apply_url)
               VALUES (%s,'{source_id}',%s,%s,%s,'','other','open',%s)""",
            [(str(i), f"https://t43.invalid/{source_id}/{i}", f"https://t43.invalid/{source_id}/{i}", f"Role {i}",
             f"https://t43.invalid/{source_id}/{i}") for i in range(n)],
        )

    applicant_id = _upload(client, tmp_path).json()["id"]

    _seed_listings("src_t43_small", 3)
    copy_count["n"] = 0
    results_small = pipeline.compute_matches(conn, applicant_id)
    assert copy_count["n"] == 1                               # ONE copy operation, not 3
    stored_small = conn.execute("SELECT count(*) AS n FROM applicant_match WHERE applicant_id=%s", (applicant_id,)).fetchone()["n"]
    assert stored_small == len(results_small)

    _seed_listings("src_t43_large", 80)
    copy_count["n"] = 0
    results_large = pipeline.compute_matches(conn, applicant_id)
    assert copy_count["n"] == 1                               # STILL one copy operation, not 83
    stored_large = conn.execute("SELECT count(*) AS n FROM applicant_match WHERE applicant_id=%s", (applicant_id,)).fetchone()["n"]
    assert stored_large == len(results_large) == 83


def test_t43_bulk_insert_checksum_matches_a_fixed_value_for_a_synthetic_fixture(client, tmp_path, conn):
    """T43's own identity-neutral requirement, as a committed, offline (no live-DB dependency) regression:
    a fixed, small synthetic listing set scored against the real uploaded golden-shaped CV must persist to
    the SAME checksum every time - this exact value was computed by actually running this test against the
    real code (never hand-derived), and the live, one-off before/after comparison against the real ~/.ajp
    DB (all 4,065 real open rows, both real golden CVs) is reported separately in pm/outbox/t43_report.md."""
    import hashlib

    from ajp.applicants import pipeline

    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok)
                    VALUES ('src_t43_checksum','feed','https://x.invalid',true)""")
    conn.cursor().executemany(
        """INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text, country_iso2,
                                position_kind, degree_required, status, apply_url)
           VALUES (%s,'src_t43_checksum',%s,%s,%s,%s,%s,'other',%s,'open',%s)""",
        [
            ("1", "https://t43c.invalid/1", "https://t43c.invalid/1", "Postdoc in Quantum Physics",
             "physics research role", "DE", "phd", "https://t43c.invalid/1"),
            ("2", "https://t43c.invalid/2", "https://t43c.invalid/2", "Chef position",
             "restaurant kitchen role", "FR", "none", "https://t43c.invalid/2"),
            ("3", "https://t43c.invalid/3", "https://t43c.invalid/3", "Some role", "", None, "unknown",
             "https://t43c.invalid/3"),
        ],
    )
    applicant_id = _upload(client, tmp_path).json()["id"]
    pipeline.compute_matches(conn, applicant_id)
    # T43, found live building this exact test: `listing.id` is a server-generated random UUID, not a
    # stable identity - sorting/hashing by it made the checksum spuriously "non-deterministic" across runs
    # (same real scores/factors every time, just reshuffled row order) purely because a DIFFERENT random
    # UUID happened to sort first. Joined against the listing's own real, fixture-controlled `ext_uid`
    # ('1'/'2'/'3' above) instead - a real stable identity, not an artifact of insertion order.
    rows = conn.execute(
        """SELECT l.ext_uid, am.score, am.factors::text AS factors FROM applicant_match am
           JOIN listing l ON l.id = am.listing_id
           WHERE am.applicant_id=%s ORDER BY l.ext_uid""",
        (applicant_id,),
    ).fetchall()
    assert len(rows) == 3
    blob = "\n".join(f"{r['ext_uid']}|{r['score']}|{r['factors']}" for r in rows)
    checksum = hashlib.md5(blob.encode()).hexdigest()
    assert checksum == "91c060c3beedc7e065ba472b9663de30"


def test_t43_a_failed_copy_rolls_back_the_delete_too_never_leaving_zero_rows(client, tmp_path, conn, monkeypatch):
    """T43's own explicit atomicity requirement: 'a failure must not leave zero/partial rows' - DELETE +
    the bulk COPY are one real transaction now (`with conn.transaction():`), so a failure injected into
    the COPY step must leave the ORIGINAL rows from the previous successful compute still in place, never
    an empty table. (Found live building this exact task: under this project's own `autocommit=True`
    connection, this was NOT true before T43 - each statement committed on its own.)"""
    import psycopg
    from ajp.applicants import pipeline

    conn.execute("""INSERT INTO source (id, kind, base_url, robots_ok)
                    VALUES ('src_t43_atomic','feed','https://x.invalid',true)""")
    conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, raw_text,
                                         position_kind, status, apply_url)
                    VALUES ('1','src_t43_atomic','https://t43a.invalid/1','https://t43a.invalid/1',
                            'Postdoc role','physics','other','open','https://t43a.invalid/1')""")
    applicant_id = _upload(client, tmp_path).json()["id"]
    pipeline.compute_matches(conn, applicant_id)
    before = conn.execute("SELECT count(*) AS n FROM applicant_match WHERE applicant_id=%s", (applicant_id,)).fetchone()["n"]
    assert before == 1

    def failing_copy(self, *args, **kwargs):
        raise RuntimeError("simulated COPY failure")

    monkeypatch.setattr(psycopg.Cursor, "copy", failing_copy)
    with pytest.raises(RuntimeError):
        pipeline.compute_matches(conn, applicant_id)
    monkeypatch.undo()

    after = conn.execute("SELECT count(*) AS n FROM applicant_match WHERE applicant_id=%s", (applicant_id,)).fetchone()["n"]
    assert after == 1                      # the pre-existing row survived - never left at 0 by a half-done recompute
