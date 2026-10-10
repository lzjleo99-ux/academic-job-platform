import json
import re
from urllib.parse import urlsplit

import pytest

from ajp import orgs

SEED = orgs.load_seed()
BY = {o["slug"]: o for o in SEED}


def test_seed_shape_and_uniqueness():
    assert len(SEED) == len(BY) == 101   # T25 (packet 016): +1, bg-razvojkarijere (type=career_centre)
    assert all(o.get("name_en") or o.get("name_native") for o in SEED)
    bg_fac = [o for o in SEED if o["type"] == "faculty" and o["parent_slug"] == "bg"]
    uns_fac = [o for o in SEED if o["type"] == "faculty" and o["parent_slug"] == "uns"]
    ni_fac = [o for o in SEED if o["type"] == "faculty" and o["parent_slug"] == "ni"]
    assert len(bg_fac) == 31 and all(o["name_en"] and o["name_native"] for o in bg_fac)    # official EN + SR names
    assert len(uns_fac) == 14 and all(o["name_en"] for o in uns_fac)                       # T8: EN names only so far
    assert len(ni_fac) == 14 and all(o["name_en"] for o in ni_fac)                         # T9: EN names only so far


def test_no_invented_or_out_of_scope_urls():
    for o in SEED:
        for u in [o.get("homepage")] + list(o.get("career_urls") or []):
            if u:
                assert re.match(r"https?://[^\s]+$", u), (o["slug"], u)
        assert not any(urlsplit(u).netloc.startswith("zaposleni.") for u in o.get("career_urls") or []), o["slug"]  # HR logins
    # inst-imi/inst-inep were PM-suggested URLs that did not verify at T4; both were later resolved (T7 closure):
    # inep got a working board, imi turned out to have an expired certificate (deeplink_only, never NULL/invented)
    assert BY["inst-imi"]["homepage"] == "https://www.imi.bg.ac.rs/" and BY["inst-imi"]["render_mode"] == "deeplink_only"
    assert BY["inst-inep"]["homepage"] == "https://www.inep.ac.rs/" and BY["inst-inep"]["career_urls"]


def test_fsfv_is_deeplink_only_with_no_crawlable_urls():
    f = BY["bg-fsfv"]
    assert f["render_mode"] == "deeplink_only" and f["robots_status"] == "disallowed" and f["career_urls"] == []


def test_ekof_is_not_special_cased_or_downgraded():
    assert BY["bg-ekof"]["robots_status"] in ("allowed", "allowed_no_robots") and BY["bg-ekof"]["render_mode"] == "httpx"


def test_razvojkarijere_has_exactly_one_channel_not_a_leftover_duplicate(conn):
    """The org used to be represented as a separate, ad-hoc standalone channel (bg_career_centre, retired) -
    now that it is a real org_master row, its OWN auto-generated deeplink channel must be the ONLY one; a
    stale 'bg_career_centre' source row left behind by an old install would be a real, visible duplicate on
    the /channels page."""
    orgs.seed_orgs(conn)
    ids = {r[0] for r in conn.execute(
        "SELECT id FROM source WHERE id LIKE '%razvojkarijere%' OR id = 'bg_career_centre'").fetchall()}
    assert ids == {"bg_razvojkarijere"}


def test_razvojkarijere_is_a_real_career_centre_org_under_bg_kept_deeplink_after_a_real_bounded_discovery():
    """T25 (packet 016 PM ruling, 2026-09-28): the University of Belgrade Career Development Centre - a
    genuinely new org TYPE this project had not needed before, not shoehorned into an existing one. A real
    bounded discovery WAS run (robots allows /posao and /praksa, real /node/NNNN pages exist) but ended in
    deeplink_only per the packet's own fallback: the shared classifier cannot reliably tell a real internship
    call apart from a stale job-fair announcement on this specific site (both contain 'praksa') - verified
    live, not assumed."""
    r = BY["bg-razvojkarijere"]
    assert r["type"] == "career_centre" and r["parent_slug"] == "bg"
    assert r["render_mode"] == "deeplink_only" and r["robots_status"] == "allowed"
    assert r["career_urls"] == []
    assert r["deeplink_url"] == "http://www.razvojkarijere.bg.ac.rs/praksa"


def test_seed_into_db_is_idempotent_and_keeps_enrichment(conn):
    assert orgs.seed_orgs(conn) == 101
    conn.execute("UPDATE org_master SET openalex_id='I123', qs_rank=801, accredited_rs=true WHERE slug='bg'")
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT count(*) FROM org_master").fetchone()[0] == 101
    assert conn.execute("SELECT openalex_id, qs_rank, accredited_rs FROM org_master WHERE slug='bg'").fetchone() == ("I123", 801, True)
    assert conn.execute("""SELECT count(*) FROM org_master f JOIN org_master u ON u.id=f.parent_org_id
                           WHERE f.type='faculty' AND u.slug='bg'""").fetchone()[0] == 31
    assert conn.execute("SELECT city FROM org_master WHERE slug='bg-tfbor'").fetchone()[0] == "Bor"


def test_crawl_targets_exclude_fsfv_and_anything_not_robots_clean(conn):
    orgs.seed_orgs(conn)
    slugs = {t["slug"] for t in orgs.crawl_targets(conn)}
    assert "bg-etf" in slugs and "bg-arh" in slugs
    assert "bg-fsfv" not in slugs                                           # disallowed + deeplink_only
    assert all(BY[s]["career_urls"] for s in slugs)
    conn.execute("UPDATE org_master SET robots_status='unavailable' WHERE slug='bg-etf'")
    assert "bg-etf" not in {t["slug"] for t in orgs.crawl_targets(conn)}
    conn.execute("UPDATE org_master SET render_mode='skip' WHERE slug='bg-arh'")
    assert "bg-arh" not in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_fsfv_channel_row_exists_and_cannot_hold_listings(conn):
    from psycopg import errors
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT kind, robots_ok FROM source WHERE id='bg_fsfv'").fetchone() == ("channel", False)
    (label, url), = conn.execute("SELECT label, url FROM channel_link WHERE source_id='bg_fsfv'").fetchall()
    assert "opens on www.fsfv.bg.ac.rs" in label and url == "https://www.fsfv.bg.ac.rs/"
    with pytest.raises(errors.CheckViolation):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1','bg_fsfv','u','u','t','RS','other')""")


def test_kind_column_defaults_to_hidden_other_and_is_constrained(conn):
    from psycopg import errors
    conn.execute("INSERT INTO source (id, kind, base_url) VALUES ('s','manual','https://s.invalid')")
    def add(**kw):
        row = dict(ext_uid="1", source_id="s", url="u", url_canon="u", title_raw="t", country_iso2="RS", position_kind="other"); row.update(kw)
        return conn.execute(f"INSERT INTO listing ({', '.join(row)}) VALUES ({', '.join('%(' + k + ')s' for k in row)}) RETURNING kind", row).fetchone()[0]
    assert add() == "other"                                                  # unclassified is never assumed to be employment
    assert add(ext_uid="2", kind="employment") == "employment"
    with pytest.raises(errors.CheckViolation):
        add(ext_uid="3", kind="job")


# ---------------------------------------------------------------- OpenAlex matching -------
OA = [
    {"id": "I1", "display_name": "University of Belgrade", "display_name_alternatives": ["Универзитет у Београду"]},
    {"id": "I2", "display_name": "University of Belgrade - Faculty of Mathematics", "display_name_alternatives": []},
    {"id": "I3", "display_name": "Institute of Physics Belgrade", "display_name_alternatives": ["Institut za fiziku Beograd"]},
    {"id": "I4", "display_name": "Institut za higijenu i tehnologiju mesa", "display_name_alternatives": []},
    {"id": "I5", "display_name": "Faculty of Mathematics", "display_name_alternatives": []},
]


def mk(slug, name, parent=None, **kw):
    return dict(slug=slug, name_en=name, parent_slug=parent, type="institute", **kw)


def test_match_is_conservative_and_one_to_one():
    seed = [mk("bg", "University of Belgrade"), mk("bg-matf", "Faculty of Mathematics", "bg"),
            mk("ipb", "Institute of Physics Belgrade"), mk("ihtm", "Institute of Chemistry, Technology and Metallurgy"),
            mk("ph", "Some Private Univ", notes="TODO: official name (placeholder = host)")]
    r = orgs.match_openalex(seed, OA)
    got = {m["slug"]: m["openalex_id"] for m in r["matches"]}
    assert got == {"bg": "I1", "bg-matf": "I2", "ipb": "I3"}      # parent+faculty context picks the Belgrade faculty, not I5
    assert [u["slug"] for u in r["unmatched"]] == ["ihtm"]        # look-alike ("Institut za higijenu...") is NOT a match
    assert "ph" not in got and all(u["slug"] != "ph" for u in r["unmatched"])   # placeholder names are never matched


def test_university_never_matches_its_own_faculty_and_duplicates_are_refused():
    r = orgs.match_openalex([mk("bg", "University of Belgrade")], [OA[1]])
    assert r["matches"] == []                                     # only the faculty exists in OpenAlex
    twins = [mk("a", "Institute of Physics Belgrade"), mk("b", "Institute of Physics Belgrade")]
    r = orgs.match_openalex(twins, [OA[2]])
    assert len(r["matches"]) == 1 and len(r["ambiguous"]) == 1    # one OpenAlex id can be claimed once


def test_apply_matches_never_overwrites_existing_ids(conn):
    orgs.seed_orgs(conn)
    conn.execute("UPDATE org_master SET openalex_id='I-manual' WHERE slug='bg'")
    n = orgs.apply_openalex_matches(conn, [{"slug": "bg", "openalex_id": "I1"}, {"slug": "uns", "openalex_id": "I9"}])
    assert n == 1
    assert conn.execute("SELECT openalex_id FROM org_master WHERE slug IN ('bg','uns') ORDER BY slug").fetchall() == [("I-manual",), ("I9",)]


def test_works_count_only_applies_where_a_match_already_exists(conn):
    """T12: openalex_works_count is enrichment ON TOP of a real match, never a number attached to an org
    OpenAlex was never actually matched to (migration 0013)."""
    orgs.seed_orgs(conn)
    n = orgs.apply_openalex_works_count(conn, {"bg": 12345, "bg-matf": 999})   # bg-matf has no openalex_id yet
    assert n == 0
    orgs.apply_openalex_matches(conn, [{"slug": "bg", "openalex_id": "I1"}])
    n = orgs.apply_openalex_works_count(conn, {"bg": 12345, "bg-matf": 999})
    assert n == 1
    assert conn.execute("SELECT openalex_works_count FROM org_master WHERE slug='bg'").fetchone()[0] == 12345
    assert conn.execute("SELECT openalex_works_count FROM org_master WHERE slug='bg-matf'").fetchone()[0] is None


@pytest.mark.parametrize("name,expected", [
    ("Oncology Institute of Vojvodina", True),
    ("National Health Insurance Fund", True),                # RFZO - named explicitly by PM alongside the clinical examples
    ("Klinicki centar Srbije", True),
    ("Institute of Economic Sciences", False),
    ("Astronomical Observatory", False),
])
def test_is_healthcare_facility(name, expected):
    assert orgs.is_healthcare_facility(name) is expected


def test_openalex_review_queue_excludes_matched_and_low_works_and_healthcare():
    seed = [mk("bg", "University of Belgrade")]
    oa_rows = [
        {"id": "I1", "display_name": "University of Belgrade", "display_name_alternatives": [], "works_count": 1000},
        {"id": "I2", "display_name": "Small Institute", "display_name_alternatives": [], "works_count": 10},   # below min_works
        {"id": "I3", "display_name": "General Hospital Nis", "display_name_alternatives": [], "works_count": 500},  # healthcare
        {"id": "I4", "display_name": "Institute of Forestry", "display_name_alternatives": [], "works_count": 2834},
    ]
    queue = orgs.openalex_review_queue(seed, oa_rows, matched_ids={"I1"}, min_works=50)
    assert [c["openalex_id"] for c in queue] == ["I4"]                        # only the real, unmatched, sizeable, non-healthcare one


def test_committed_openalex_result_is_consistent_with_seed():
    """T12: rebuilt against the real, properly-scoped 119 RS institutions (country_code:rs, type education|
    facility|nonprofit|government|archive) - the file this replaced had a stale 'openalex_total: 167' figure
    PM's own sizing addenda explicitly said was wrong (167 = every type including company/healthcare/other)."""
    res = json.loads((orgs.SEED_PATH.parent / "rs_orgs_openalex.json").read_text())
    ids = [m["openalex_id"] for m in res["matches"]]
    assert len(ids) == len(set(ids)) and all(m["slug"] in BY and m["score"] >= 90 for m in res["matches"])
    assert {"bg", "uns", "ni", "kg", "odbrane"} <= {m["slug"] for m in res["matches"]}
    assert "119" in res["fetched_from"]
    # works_count_by_slug is enrichment for MATCHED orgs only, never invented for an org OpenAlex wasn't matched to
    assert set(res["works_count_by_slug"]) == {m["slug"] for m in res["matches"]}
    # the review queue never includes a healthcare-named facility or anything already matched to a seed org
    for c in res["review_queue_new_org_candidates"]:
        assert not orgs.is_healthcare_facility(c["name"])
        assert c["openalex_id"] not in ids
        assert c["works_count"] >= 50


# ---------------------------------------------------------------- PM recon addenda (T5 gap-fill) -----
ADDENDA = [
    {"org": "Faculty of Civil Engineering Belgrade (GRF)", "career_urls": ["https://web1.grf.bg.ac.rs/vesti"],
     "notes": "JS shell -> Playwright render path then parser; /mojizbor is a LOGIN portal"},
    {"org": "Faculty of Mechanical Engineering Belgrade (MAS)", "career_urls": ["https://www.mas.bg.ac.rs/a/start", "https://zaposleni.mas.bg.ac.rs/x"],
     "exclude": ["zaposleni.mas.bg.ac.rs (HR portal, R4)"]},
    {"org": "Faculty of Sport and Physical Education Belgrade (FSFV)", "decision": "DEEPLINK ONLY"},
    {"org": "Faculty of Architecture Belgrade (ARH)", "host": "www.arh.bg.ac.rs", "parser_note": "category /category/vesti/konkursi/ returns 200 with no article"},
    {"org": "Somewhere Else (NOPE)"},
]


def fresh():
    return [dict(o) for o in orgs.load_seed()]


def test_addenda_apply_with_robots_gate_hr_rejection_and_render_mode():
    seed = fresh()
    log = orgs.apply_board_addenda(seed, ADDENDA, allowed=lambda u: "denied" not in u)
    by = {o["slug"]: o for o in seed}
    assert set(log["applied"]) == {"bg-grf", "bg-mas", "bg-fsfv", "bg-arh"} and log["unknown"] == ["Somewhere Else (NOPE)"]
    assert (by["bg-grf"]["render_mode"], by["bg-grf"]["career_urls"], by["bg-grf"]["crawl_config"]) == ("playwright", ["https://web1.grf.bg.ac.rs/vesti"], {"empty_is_failure": True})
    assert by["bg-mas"]["career_urls"] == ["https://www.mas.bg.ac.rs/a/start"] and ("bg-mas", "https://zaposleni.mas.bg.ac.rs/x", "R4 HR/login host") in log["dropped_urls"]
    assert "https://www.arh.bg.ac.rs/category/vesti/konkursi/" in by["bg-arh"]["career_urls"] and len(by["bg-arh"]["career_urls"]) == 2
    f = by["bg-fsfv"]
    assert (f["render_mode"], f["career_urls"], f["robots_status"]) == ("deeplink_only", [], "disallowed")


def test_addenda_never_keep_a_robots_denied_url():
    seed = fresh()
    log = orgs.apply_board_addenda(seed, [ADDENDA[1]], allowed=lambda u: False)
    assert {o["slug"]: o for o in seed}["bg-mas"]["career_urls"] == [] and len(log["dropped_urls"]) == 2


def test_committed_seed_reflects_the_pm_addenda():
    by = {o["slug"]: o for o in orgs.load_seed()}
    assert by["bg-grf"]["render_mode"] == "playwright" and by["bg-grf"]["crawl_config"] == {"empty_is_failure": True}
    assert by["bg-fsfv"]["render_mode"] == "deeplink_only" and by["bg-fsfv"]["career_urls"] == []
    for slug in ("bg-mas", "bg-fon", "bg-ekof", "bg-ius", "bg-grf"):
        assert by[slug]["career_urls"], slug
    from urllib.parse import urlsplit
    urls = [u for o in by.values() for u in o["career_urls"]]
    assert not any(re.match(r"^(zaposleni|servisi|login|sso)\.", urlsplit(u).netloc) for u in urls)      # R4: HR/login HOSTS
    assert not any(re.search(r"/(mojizbor|login|prijava)\b", urlsplit(u).path, re.I) for u in urls)       # authenticated areas


def test_fsfv_can_never_be_fetched_by_any_path(conn, tmp_path):
    from ajp.connectors import board
    from ajp.connectors.base import PoliteClient
    from ajp.rawstore import LocalFsStore
    orgs.seed_orgs(conn)
    assert "bg-fsfv" not in {t["slug"] for t in orgs.crawl_targets(conn)}                    # selection
    row = conn.execute("SELECT slug, homepage, career_urls, render_mode FROM org_master WHERE slug='bg-fsfv'").fetchone()
    org = dict(zip(("slug", "homepage", "career_urls", "render_mode"), row))
    with pytest.raises(TypeError, match="never fetched"):                                      # construction, even if someone bypasses selection
        board.BoardConnector(PoliteClient(rps=1000), LocalFsStore(tmp_path), org)
    with pytest.raises(TypeError):
        board.BoardConnector(PoliteClient(rps=1000), LocalFsStore(tmp_path), dict(org, render_mode="skip"))
    assert conn.execute("SELECT kind FROM source WHERE id='bg_fsfv'").fetchone() == ("channel",)  # marked as channel_link
    assert conn.execute("SELECT count(*) FROM channel_link WHERE source_id='bg_fsfv'").fetchone()[0] == 1


def test_fsfv_emits_only_a_channel_link_and_never_a_raw_capture(conn, tmp_path):
    """PM batch 3: for a robots-disallowed deeplink org the ONLY artifact is the channel_link; no raw capture, no listing, no state."""
    from ajp.connectors import board
    from ajp.connectors.base import PoliteClient
    from ajp.rawstore import LocalFsStore
    orgs.seed_orgs(conn)
    row = conn.execute("SELECT id, slug, homepage, career_urls, render_mode FROM org_master WHERE slug='bg-fsfv'").fetchone()
    org = dict(zip(("id", "slug", "homepage", "career_urls", "render_mode"), row))
    store = LocalFsStore(tmp_path)
    with pytest.raises(TypeError):                                                     # even when a caller passes it explicitly
        board.crawl_orgs(conn, PoliteClient(rps=1000), store, [org], "run-x")
    assert list(tmp_path.rglob("*")) == []                                             # zero raw captures (no file at all)
    assert conn.execute("SELECT count(*) FROM listing WHERE org_id=%s", (org["id"],)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM crawl_state").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM channel_link WHERE source_id='bg_fsfv'").fetchone()[0] == 1


def test_addenda2_note_driven_rules():
    seed = fresh()
    add = [
        {"org": "Faculty of Transport and Traffic Engineering (SF)", "host": "www.sf.bg.ac.rs", "career_urls": ["https://www.sf.bg.ac.rs/x/388-konkursi"], "notes": "Joomla; use www"},
        {"org": "Faculty of Technology and Metallurgy (TMF)", "career_urls": [], "notes": "Run discovery; deeplink to homepage if nothing open found after 3 runs"},
        {"org": "Faculty of Mining and Geology (RGF)", "notes": "deeplink after 3 empty runs"},
        {"org": "Faculty of Dentistry (STOMF)", "career_urls": ["https://www.stomf.bg.ac.rs/nastava/lista-zvanja/"], "notes": "discover staff open-call board, else deeplink"},
        {"org": "Teacher Education Faculty (UF)", "notes": "staff side is HR portal (R4 excluded); likely deeplink-only for employment"},
        {"org": "Faculty of Civil Engineering and Architecture Nis (GAF) and TF Leskovac and MASFAK Nis", "note": "unreachable from PM vantage"},
    ]
    log = orgs.apply_board_addenda(seed, add, allowed=lambda u: True)
    by = {o["slug"]: o for o in seed}
    assert by["bg-sf"]["career_urls"] == ["https://www.sf.bg.ac.rs/x/388-konkursi"]
    assert by["bg-tmf"]["crawl_config"] == {"empty_is_failure": True} and by["bg-tmf"]["render_mode"] == "httpx"      # keeps crawling, degrades after 3 empty runs
    assert by["bg-stomf"]["crawl_config"] == {"empty_is_failure": True}
    assert by["bg-uf"]["render_mode"] == "deeplink_only" and by["bg-uf"]["career_urls"] == []
    assert log["unknown"] == ["Faculty of Civil Engineering and Architecture Nis (GAF) and TF Leskovac and MASFAK Nis"]      # Nis: T9, not a bg- org


def test_crawl_config_entries_are_validated_and_merged():
    seed = fresh()
    assert orgs.apply_crawl_config(seed, [{"slug": "bg-sf", "crawl_config": {"max_pages": 3}}]) == ["bg-sf"]
    assert {o["slug"]: o for o in seed}["bg-sf"]["crawl_config"]["max_pages"] == 3
    for bad in ({"slug": "bg-sf", "crawl_config": {"max_pages": 0}}, {"slug": "bg-sf", "crawl_config": {"max_pages": 11}},
                {"slug": "bg-sf", "crawl_config": {"max_pages": "3"}}, {"slug": "nope", "crawl_config": {}}):
        with pytest.raises(ValueError):
            orgs.apply_crawl_config(fresh(), [bad])


def test_committed_seed_carries_the_sf_paging_and_tmf_stomf_empty_rules():
    by = {o["slug"]: o for o in orgs.load_seed()}
    assert by["bg-sf"]["crawl_config"]["max_pages"] == 3
    assert by["bg-tmf"]["crawl_config"]["empty_is_failure"] and by["bg-stomf"]["crawl_config"]["empty_is_failure"]
    assert by["bg-uf"]["render_mode"] == "deeplink_only" and not by["bg-rgf"]["career_urls"]


# ---- T5 closure rulings: deeplink_url override, standalone channel, crawl_state pruning ------------------------
def test_deeplink_url_overrides_homepage_for_the_channel_link(conn):
    seed = [dict(slug="x-hr", type="faculty", name_en="X Faculty", country="RS",
                 homepage="https://x.bg.ac.rs/", deeplink_url="http://zaposleni.x.bg.ac.rs", render_mode="deeplink_only", career_urls=[])]
    orgs.seed_orgs(conn, seed)
    url, label = conn.execute("SELECT url, label FROM channel_link WHERE source_id='x_hr'").fetchone()
    assert url == "http://zaposleni.x.bg.ac.rs" and "zaposleni.x.bg.ac.rs" in label
    assert conn.execute("SELECT homepage FROM org_master WHERE slug='x-hr'").fetchone()[0] == "https://x.bg.ac.rs/"   # org's own homepage untouched


def test_add_standalone_channel_is_never_crawlable(conn):
    """A generic, fictional example (x_guidance_portal) - not any real org's own slug. The real University of
    Belgrade Career Development Centre used to be represented exactly this way (ajp/channels/bg_career_centre.py,
    T5 closure ruling) until T25 (packet 016) gave it a real org_master row instead (slug=bg-razvojkarijere) -
    retired in favour of that one canonical place, not left as a second, parallel representation."""
    from psycopg import errors
    orgs.add_standalone_channel(conn, "x_guidance_portal",
                                [{"slug": "posao", "label": "X Guidance Portal: jobs (opens on x.example.rs)",
                                  "url": "http://x.example.rs/posao", "description": "guidance portal, not part of our database"}],
                                notes="Guidance portal; public sections currently empty; employer side login-gated.")
    assert conn.execute("SELECT kind FROM source WHERE id='x_guidance_portal'").fetchone() == ("channel",)
    assert conn.execute("SELECT count(*) FROM channel_link WHERE source_id='x_guidance_portal'").fetchone()[0] == 1
    with pytest.raises(errors.CheckViolation):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1','x_guidance_portal','u','u','t','RS','other')""")
    orgs.add_standalone_channel(conn, "x_guidance_portal", [{"slug": "posao", "label": "updated", "url": "http://x.example.rs/posao"}])
    assert conn.execute("SELECT count(*) FROM channel_link WHERE source_id='x_guidance_portal'").fetchone()[0] == 1   # idempotent, not duplicated


def test_prune_crawl_state_removes_only_orphaned_urls(conn):
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, career_urls, render_mode, robots_status)
                    VALUES ('y-org', 'Y', 'RS', 'faculty', ARRAY['https://y.invalid/kept'], 'httpx', 'allowed')""")
    conn.execute("INSERT INTO crawl_state (source_id, key, consecutive_failures) VALUES "
                 "('rs_org_boards', 'https://y.invalid/kept', 2), ('rs_org_boards', 'https://y.invalid/stale-removed-board', 3)")
    n = orgs.prune_crawl_state(conn)
    assert n == 1
    left = {r[0] for r in conn.execute("SELECT key FROM crawl_state").fetchall()}
    assert left == {"https://y.invalid/kept"}


# ---- T5 closure rulings applied to the committed seed --------------------------------------------------------
def test_closure_fasper_deeplinks_to_its_hr_host():
    f = BY["bg-fasper"]
    assert f["render_mode"] == "deeplink_only" and f["career_urls"] == [] and f["deeplink_url"] == "http://zaposleni.fasper.bg.ac.rs"


def test_closure_agrif_hand_added_playwright_boards():
    a = BY["bg-agrif"]
    assert a["render_mode"] == "playwright" and a["crawl_config"] == {"empty_is_failure": True}
    assert a["career_urls"] == ["https://agrif.bg.ac.rs/sr/mesto-susreta-tradicije-i-inovacija/konkursi",
                                "https://agrif.bg.ac.rs/sr/mesto-susreta-tradicije-i-inovacija/stipendije-praksa-posao"]


def test_closure_gef_deeplinks_to_its_vesti_category_no_empty_is_failure():
    g = BY["bg-gef"]
    assert g["render_mode"] == "deeplink_only" and g["career_urls"] == [] and g["crawl_config"] == {}
    assert g["deeplink_url"] == "https://gef.bg.ac.rs/category/vesti/"


def test_closure_fasper_and_gef_channel_links_point_at_the_ruled_url_not_the_homepage(conn):
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT url FROM channel_link WHERE source_id='bg_fasper'").fetchone()[0] == "http://zaposleni.fasper.bg.ac.rs"
    assert conn.execute("SELECT url FROM channel_link WHERE source_id='bg_gef'").fetchone()[0] == "https://gef.bg.ac.rs/category/vesti/"
    assert BY["bg-fasper"]["homepage"] and BY["bg-gef"]["homepage"]                            # org's own homepage untouched


def test_closure_agrif_and_gef_and_fasper_never_reuse_their_retired_state(conn):
    """A pruned/retired board URL leaves no leftover crawl_state row, and agrif/gef/fasper behave per their new mode."""
    orgs.seed_orgs(conn)
    conn.execute("INSERT INTO crawl_state (source_id, key, consecutive_failures) VALUES "
                 "('rs_org_boards', 'https://agrif.bg.ac.rs/fakultet/konkursi-stipendije.php', 2)")   # the old, now-retired URL
    assert orgs.prune_crawl_state(conn) == 1
    targets = {t["slug"]: t for t in orgs.crawl_targets(conn)}
    assert "bg-agrif" in targets and targets["bg-agrif"]["render_mode"] == "playwright"
    assert "bg-fasper" not in targets and "bg-gef" not in targets


# ---- T5 addenda4 (BG discovery sweep #2) --------------------------------------------------------------------
def test_addenda4_sfb_uses_the_deep_konkursi_page():
    assert BY["bg-sfb"]["career_urls"] == ["https://www.sfb.bg.ac.rs/fakultet/oglasna-tabla/uvidi-javnosti/izbori-u-zvanja/konkursi/"]


def test_addenda4_f_keeps_enrollment_board_and_adds_generic_vesti():
    f = BY["bg-f"]
    assert f["career_urls"] == ["https://www.f.bg.ac.rs/konkursi", "https://www.f.bg.ac.rs/vesti"]
    assert f["crawl_config"] == {"max_pages": 3} and "empty_is_failure" not in f["crawl_config"]     # enrollment board must never auto-degrade the org


@pytest.mark.parametrize("slug,homepage", [("bg-chem", "https://www.chem.bg.ac.rs/"), ("bg-tfbor", "https://www.tfbor.bg.ac.rs/"),
                                           ("bg-bfspc", "https://bfspc.bg.ac.rs/")])
def test_addenda4_none_found_orgs_are_deeplink_only_to_their_homepage(slug, homepage, conn):
    o = BY[slug]
    assert o["render_mode"] == "deeplink_only" and o["career_urls"] == [] and o["crawl_config"] == {}
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT url FROM channel_link WHERE source_id=%s", (slug.replace("-", "_"),)).fetchone()[0] == homepage


def test_addenda4_rgf_is_unchanged_still_pending_discovery():
    assert BY["bg-rgf"]["render_mode"] == "httpx" and BY["bg-rgf"]["career_urls"] == []


def test_expected_post_addenda4_bg_counts():
    fac = [o for o in SEED if o["type"] == "faculty" and o["parent_slug"] == "bg"]
    active = [o for o in fac if o["render_mode"] in ("httpx", "playwright") and o["career_urls"]]
    deeplink_or_none = [o for o in fac if o["render_mode"] == "deeplink_only"]
    pending = [o for o in fac if o["render_mode"] == "httpx" and not o["career_urls"]]
    assert len(fac) == 31
    assert {o["slug"] for o in deeplink_or_none} == {"bg-fsfv", "bg-uf", "bg-fasper", "bg-gef", "bg-chem", "bg-tfbor", "bg-bfspc"}
    assert {o["slug"] for o in pending} == {"bg-rgf"}
    assert len(active) == 23                                                          # 21 pre-existing + agrif's 1 org (2 urls) + sfb's fix


# ---- T7 institutes: render_mode gap fix + real boards + new ITN SANU org --------------------------------------
def test_institutes_have_a_render_mode_now(conn):
    """Regression: no institute ever had render_mode set by the initial build script, so crawl_targets() (which
    requires render_mode IN ('httpx','playwright')) could never select ANY institute regardless of career_urls."""
    orgs.seed_orgs(conn)
    for slug in ("inst-ipb", "inst-ibiss", "inst-ihtm", "inst-pupin", "inst-imsi", "inst-itn-sanu"):
        row = conn.execute("SELECT render_mode, career_urls FROM org_master WHERE slug=%s", (slug,)).fetchone()
        assert row[0] in ("httpx", "playwright"), slug
        assert row[1], slug                                                        # each has at least one board URL


def test_institutes_are_now_real_crawl_targets(conn):
    orgs.seed_orgs(conn)
    slugs = {t["slug"] for t in orgs.crawl_targets(conn)}
    assert {"inst-ipb", "inst-ibiss", "inst-ihtm", "inst-pupin", "inst-imsi", "inst-itn-sanu",
            "inst-vinca", "inst-imgge", "inst-iit-kg", "inst-inep"} <= slugs        # T7 closure: all now have real boards
    assert "inst-imi" not in slugs                                                 # deeplink_only (expired certificate)


def test_ibiss_and_ihtm_no_longer_carry_a_contact_page_as_a_board():
    assert BY["inst-ibiss"]["career_urls"] == ["https://www.ibiss.bg.ac.rs/sr-yu/oglasna-tabla"]
    assert BY["inst-ihtm"]["career_urls"] == ["https://www.ihtm.bg.ac.rs/sr/zaposleni-ihtm"]


def test_itn_sanu_org_is_seeded_with_two_board_candidates():
    o = BY["inst-itn-sanu"]
    assert o["render_mode"] == "httpx" and o["crawl_config"] == {"max_pages": 2}
    assert o["career_urls"] == ["https://itn.sanu.ac.rs/index.php/sr-cyrl-rs/novosti/obavestenja-naucnog-veca",
                                "https://itn.sanu.ac.rs/index.php/sr-cyrl-rs/novosti/vesti"]


def test_institut_rs_pulmonary_clinic_was_never_added_out_of_scope():
    assert not any("institut.rs" in (o.get("homepage") or "") for o in SEED)
    assert "inst-plucne" not in BY and "inst-pulmonary" not in BY


def test_vinca_and_imgge_now_have_their_pm_verified_boards():
    for slug in ("inst-vinca", "inst-imgge"):
        assert BY[slug]["render_mode"] == "httpx" and BY[slug]["career_urls"]         # T7 closure: board found, no longer pending


# ---- T7 closure addenda: vinca/imgge boards, new iit-kg, inep fix, imi expired-cert deeplink -------------------
def test_closure_vinca_keeps_its_board_url_imgge_gets_the_corrected_one():
    # T7 gap resolution: vinca's URL was already right (a parser bug hid its content); imgge's URL was wrong
    assert BY["inst-vinca"]["career_urls"] == ["https://www.vinca.rs/o-institutu/naucno-vece/izbori-u-zvanja"]
    assert BY["inst-vinca"]["crawl_config"] == {"max_pages": 3}
    assert BY["inst-imgge"]["career_urls"] == ["https://www.imgge.bg.ac.rs/lat/novosti"]
    assert BY["inst-vinca"]["render_mode"] == BY["inst-imgge"]["render_mode"] == "httpx"


def test_closure_iit_kg_added_as_new_org_with_canonical_host():
    o = BY["inst-iit-kg"]
    assert o["homepage"] == "https://iit.ac.rs/" and o["host"] == "iit.ac.rs"
    # T7 gap resolution: the original URL was a single ad's own page, not a list; corrected to the news category
    assert o["career_urls"] == ["https://iit.ac.rs/category/vesti/"] and o["render_mode"] == "httpx"
    assert "iit.kg.ac.rs" not in (o.get("notes") or "") or "alias" in (o.get("notes") or "").lower()   # alias noted, not a separate link


def test_closure_inep_now_has_a_working_homepage_and_board():
    o = BY["inst-inep"]
    assert o["homepage"] == "https://www.inep.ac.rs/" and o["career_urls"] == ["https://www.inep.ac.rs/oglasna-tabla/"]
    assert o["render_mode"] == "httpx" and "TODO" not in (o.get("notes") or "")


def test_closure_imi_is_deeplink_only_due_to_expired_certificate(conn):
    o = BY["inst-imi"]
    assert o["render_mode"] == "deeplink_only" and o["career_urls"] == [] and o["deeplink_url"] == "https://www.imi.bg.ac.rs/"
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT url FROM channel_link WHERE source_id='inst_imi'").fetchone()[0] == "https://www.imi.bg.ac.rs/"
    assert "inst-imi" not in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_imi_org_rs_the_unrelated_management_institute_is_never_used_as_a_url():
    o = BY["inst-imi"]
    for field in ("homepage", "host", "deeplink_url"):
        assert "imi.org.rs" not in (o.get(field) or "")
    assert all("imi.org.rs" not in u for u in o.get("career_urls") or [])
    assert "imi.org.rs" in (o.get("notes") or "")                      # named ONLY as a warning not to confuse it, never as a link


def test_no_connector_ever_disables_tls_for_imi_or_anyone_else():
    """PM-required guard: inst-imi's expired certificate must never be worked around by disabling verification."""
    import re
    from pathlib import Path
    from ajp import connectors
    src_dir = Path(connectors.__file__).parent
    pat = re.compile(r"verify\s*=\s*False|ignore_https_errors\s*=\s*True|CERT_NONE|--insecure|_create_unverified", re.I)
    offenders = [str(p) for p in src_dir.rglob("*.py") if pat.search(p.read_text())]
    assert offenders == []


# ---- T8: University of Novi Sad, 14 faculties ------------------------------------------------------------------
def test_uns_has_14_faculties_with_a_render_mode():
    fac = [o for o in SEED if o.get("parent_slug") == "uns"]
    assert len(fac) == 14
    assert all(o["render_mode"] in ("httpx", "deeplink_only") for o in fac)
    assert all(o["name_en"] for o in fac)


def test_uns_university_org_itself_is_not_crawled(conn):
    assert BY["uns"]["render_mode"] is None
    orgs.seed_orgs(conn)
    assert "uns" not in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_uns_addenda_corrections_applied():
    assert BY["uns-gf"]["host"] == "www.gf.uns.ac.rs"                                 # apex has no A record (addenda4)
    assert BY["uns-mf"]["career_urls"] == ["https://www.mf.uns.ac.rs/KonkursiiUvidi.php"]
    assert BY["uns-pmf"]["career_urls"] == ["https://www.pmf.uns.ac.rs/vesti/za-zaposlene-stipendije/"]
    assert not any("konkursi/" in u and "vesti" not in u for u in BY["uns-pmf"]["career_urls"])  # NOT the document-library URL
    assert BY["uns-tfzr"]["crawl_config"] == {"max_pages": 2}
    assert BY["uns-fsfv"]["career_urls"] == ["https://fsfvns.edu.rs/izbor-saradnika/"]  # the demonstrably-working host
    assert BY["uns-tf"]["host"] == "www.tf.uns.ac.rs"
    assert BY["uns-ef"]["render_mode"] == "deeplink_only" and BY["uns-ef"]["career_urls"] == []


def test_uns_ff_zaposleni_path_is_not_mistaken_for_an_hr_host(conn):
    """R4 excludes HOSTS like zaposleni.*, never a path segment; /sr/zaposleni/konkursi must still be crawled."""
    orgs.seed_orgs(conn)
    assert "uns-ff" in {t["slug"] for t in orgs.crawl_targets(conn)}
    assert BY["uns-ff"]["career_urls"] == ["https://www.ff.uns.ac.rs/sr/zaposleni/konkursi"]


def test_uns_gaps_resolved_by_addenda5():
    """polj/pravni/magister were disclosed gaps after the first T8 pass (addenda1-4); addenda5's later live probes
    resolved all three - polj DOES have a board (HTTP-only, no TLS at all), pravni/magister are CONFIRMED
    deeplink_only (an R4 HR portal; a Hungarian-language board the RS classifier cannot read), not missing data."""
    assert BY["uns-polj"]["career_urls"] == ["http://polj.uns.ac.rs/"] and BY["uns-polj"]["host"] == "polj.uns.ac.rs"
    assert BY["uns-polj"]["crawl_config"]["crawl_delay_seconds"] == 10                 # its robots.txt states Crawl-delay: 10
    for slug in ("uns-pravni", "uns-magister"):
        o = BY[slug]
        assert o["render_mode"] == "deeplink_only" and o["career_urls"] == []


def test_nis_has_14_faculties_with_a_render_mode():
    fac = [o for o in SEED if o.get("parent_slug") == "ni"]
    assert len(fac) == 14
    assert all(o["render_mode"] in ("httpx", "deeplink_only") for o in fac)
    assert all(o["name_en"] for o in fac)


def test_nis_university_org_itself_is_not_crawled(conn):
    assert BY["ni"].get("render_mode") is None
    orgs.seed_orgs(conn)
    assert "ni" not in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_nis_addenda_corrections_applied():
    assert BY["nis-elfak"]["host"] == "www.elfak.ni.ac.rs"                            # never www.elfak.rs (cert SAN mismatch)
    assert BY["nis-filfak"]["host"] == "www.filfak.ni.ac.rs"                          # apex http returns 403
    assert BY["nis-artf"]["career_urls"] == ["http://www.artf.ni.ac.rs/konkursi-i-stipendije/"]  # port 443 refused, plain http
    assert BY["nis-tf"]["career_urls"] == ["https://tf.ni.ac.rs/konkursi/"]           # addenda3: the real board, not the bare homepage
    assert BY["nis-pfvr"]["career_urls"] == ["https://pfvr.ni.ac.rs/konkursi/", "https://pfvr.ni.ac.rs/konkursni-postupci/"]


def test_nis_eknfak_and_znrfak_are_deeplink_only_for_stated_reasons(conn):
    """Both look like config errors at a glance (0 career_urls); addenda2 confirms neither is a gap - eknfak has an
    expired/mismatched certificate (IMI class), znrfak genuinely has no employment board at all."""
    eknfak = BY["nis-eknfak"]
    assert eknfak["render_mode"] == "deeplink_only" and eknfak["career_urls"] == []
    assert eknfak["deeplink_url"] == "https://www.eknfak.ni.ac.rs/srl/Konkursi.php"
    assert "expired" in eknfak["notes"].lower()
    znrfak = BY["nis-znrfak"]
    assert znrfak["render_mode"] == "deeplink_only" and znrfak["career_urls"] == []
    assert "no employment board" in znrfak["notes"].lower()
    orgs.seed_orgs(conn)
    targets = {t["slug"] for t in orgs.crawl_targets(conn)}
    assert "nis-eknfak" not in targets and "nis-znrfak" not in targets


def test_nis_masfak_and_gaf_get_real_r1_not_a_preemptive_deeplink(conn):
    """Both are TCP-unreachable (network-level block, reproduced independently, not a vantage quirk) - addenda1/3
    say to let the existing R1 run-counter degrade them after 3 failed scheduled runs, never hard-code deeplink_only
    in the seed itself."""
    for slug in ("nis-masfak", "nis-gaf"):
        o = BY[slug]
        assert o["render_mode"] == "httpx" and o["career_urls"]
    orgs.seed_orgs(conn)
    targets = {t["slug"] for t in orgs.crawl_targets(conn)}
    assert "nis-masfak" in targets and "nis-gaf" in targets                            # real crawl targets, R1 handles the rest


def test_nis_fsfv_disclosed_missing_parser_family_not_invented_as_working():
    """Independently discovered (not in PM's addenda) - its only listing page uses a THIRD Joomla layout this
    project hasn't built a family for; disclosed as a gap rather than configured as if it worked."""
    o = BY["nis-fsfv"]
    assert o["render_mode"] == "deeplink_only" and o["career_urls"] == []
    assert "com-content-category-blog__item" in o["notes"]
    assert BY["uns-magister"]["deeplink_url"].startswith("https://magister.uns.ac.rs/")


def test_kg_has_12_faculties_with_a_render_mode():
    fac = [o for o in SEED if o.get("parent_slug") == "kg"]
    assert len(fac) == 12
    assert all(o["render_mode"] in ("httpx", "deeplink_only") for o in fac)
    assert all(o["name_en"] for o in fac)


def test_kg_university_org_itself_is_not_crawled(conn):
    assert BY["kg"].get("render_mode") is None
    orgs.seed_orgs(conn)
    assert "kg" not in {t["slug"] for t in orgs.crawl_targets(conn)}
    assert BY["kg"]["career_urls"] == ["https://www.kg.ac.rs/izbor_nastavnika_konkurs.php"]  # discovered this task


def test_kg_ftn_broken_script_replaced_not_r1_burned():
    """addenda2: konkursi.php is a permanently-dead 500 legacy script - dropped outright, not R1-retried."""
    o = BY["kg-ftn"]
    assert o["career_urls"] == ["https://ftn.kg.ac.rs/izbori", "https://ftn.kg.ac.rs/prakseikonkursi"]
    assert "konkursi.php" not in " ".join(o["career_urls"])


def test_kg_addenda_host_corrections_applied():
    assert BY["kg-fin"]["host"] == "fin.kg.ac.rs" and "fink.rs" not in BY["kg-fin"]["homepage"]   # alias, never primary
    assert BY["kg-jura"]["host"] == "www.jura.kg.ac.rs"                               # apex has no A record
    assert BY["kg-mfkv"]["host"] == "mfkv.rs"
    assert BY["kg-hitvb"]["career_urls"] == [
        "https://www.hit-vb.kg.ac.rs/index.php/nastavno-osoblje/konkursi-za-izbor/tekst-konkursa-za-izbor-nastavnika-i-saradnika"]


def test_kg_filum_and_pfu_discovered_independently_not_stale_article_ids():
    """Neither was in PM's addenda - the matrix only had 3 ageing hardcoded Joomla article ids for filum and
    nothing at all for pfu; both discovered live this session via their real category/taxonomy structure."""
    filum = BY["kg-filum"]
    assert "id=20" in filum["career_urls"][0] and "2312" not in filum["career_urls"][0]
    pfu = BY["kg-pfu"]
    assert pfu["career_urls"] == ["https://pfu.kg.ac.rs/category/обавештења/"]
    # the per-title categories (docenti/asistenti/...) are STAFF DIRECTORIES, not boards - never configured
    assert not any("docenti" in u or "asistenti" in u for u in pfu["career_urls"])


def test_kpu_google_drive_route_is_dead_and_skipped_never_crawled():
    """T27 (packet 015): re-verified 2026-09-28 the Drive folder now 302s straight to accounts.google.com
    (login-gated) - a dead deeplink is worse than none, so this moved from deeplink_only to skip and the
    dead deeplink_url was removed, rather than leaving a link that never actually works for a visitor."""
    kpu = BY["kpu"]
    assert kpu["render_mode"] == "skip" and kpu["career_urls"] == []
    assert "deeplink_url" not in kpu


def test_va_uses_sitemap_discovery_with_r5_default(conn):
    va = BY["va"]
    assert va["parent_slug"] == "odbrane" and va["render_mode"] == "httpx"
    assert va["career_urls"] == ["https://www.va.mod.gov.rs/sitemap.xml"]
    assert va["crawl_config"]["sitemap_include"] == ["konkurs"]
    assert "interni-konkurs" in va["crawl_config"]["sitemap_exclude"] and "uvid-javnosti" in va["crawl_config"]["sitemap_exclude"]
    assert va["foreigner_eligible_default"] == "no"
    orgs.seed_orgs(conn)
    assert conn.execute("SELECT foreigner_eligible_default FROM org_master WHERE slug='va'").fetchone()[0] == "no"
    assert "va" in {t["slug"] for t in orgs.crawl_targets(conn)}


def test_pr_dead_shell_replaced_and_np_dunp_real_board_used():
    pr = BY["pr"]
    assert pr["career_urls"] == [
        "https://pr.ac.rs/category/izbori-u-zvanja/", "https://pr.ac.rs/category/konkursi/",
        "https://pr.ac.rs/category/konkursi/konkursi-i-stipendije-medjunarodni/"]
    assert "nastavnici/konkursi" not in " ".join(pr["career_urls"])           # the confirmed-empty client-side shell
    np = BY["np"]
    assert np["career_urls"] == ["https://np.ac.rs/studenti/konkursi-univerziteta.html"]
