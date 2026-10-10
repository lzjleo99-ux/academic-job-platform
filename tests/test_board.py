"""Generic board connector on SYNTHETIC fixtures (three structural families) + governance guards."""
from datetime import date, datetime, timezone
from pathlib import Path

import json

import pytest

from ajp import orgs
from ajp.connectors import board
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.ingest import upsert_listing
from ajp.rawstore import LocalFsStore, put_raw

FX = Path(__file__).parent / "fixtures" / "synthetic"
VEST = (FX / "board_vest_synthetic.html").read_text()
WP = (FX / "board_wp_synthetic.html").read_text()
STATIC = (FX / "board_static_synthetic.html").read_text()
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)
ORG = {"id": None, "slug": "bg-example", "name_en": "Faculty Example", "name_native": "ПРИМЕР ФАКУЛТЕТ", "city": "Belgrade",
       "homepage": "https://www.example.invalid/", "career_urls": ["https://www.example.invalid/sr/konkursi"]}


# ---------------------------------------------------------------- item extraction --------
def test_article_family_reads_titles_dates_and_ignores_site_chrome():
    items = board.parse_items(VEST, "https://faculty.example.invalid/sr/konkursi")
    assert [i.family for i in items] == ["article"] * 5
    assert not any("менију" in i.title or "подножју" in i.title for i in items)            # nav + footer never yield items
    docent = items[0]
    assert docent.url == "https://faculty.example.invalid/sr/konkursi/2026/09/konkurs-za-izbor-docenta" or docent.url.endswith("/konkurs-za-izbor-docenta")
    assert docent.posted == date(2026, 9, 14)                                              # <time datetime>
    assert items[3].posted == date(2026, 8, 1) and items[4].posted == date(2026, 8, 20)      # text dd.mm.yyyy / date-only datetime


def test_wordpress_family_collapses_read_more_duplicates_and_skips_widgets():
    items = board.parse_items(WP, "https://faculty.example.invalid/category/konkursi/")
    titles = [i.title for i in items]
    assert items and all(i.family == "wp_permalink" for i in items)
    assert "Конкурс за избор наставника: 10.06.2026. године" in titles and not any("прочитај" in t.lower() for t in titles)
    assert not any("видџету" in t or "Мени" in t for t in titles)                          # sidebar widget + menu are chrome
    by_url = {i.url: i for i in items}
    assert len(by_url) == len(items)                                                       # one item per URL
    assert by_url["https://faculty.example.invalid/2026/06/16/konkurs-za-izbor-nastavnika-10-06-2026/"].posted == date(2026, 6, 16)
    assert by_url["https://faculty.example.invalid/2026/05/27/87772/"].posted == date(2026, 5, 27)      # from the permalink itself


def test_page_of_linked_documents_is_read_as_a_document_library():
    items = board.parse_items(STATIC, "https://faculty.example.invalid/sr/dokumenti/konkursi")
    assert [i.family for i in items] == ["document"] * 3 and all(i.posted is None for i in items)          # undated by design
    kept = [(i.title, board.keep(i)) for i in items]
    assert [k[0] for k in kept if k[1][0]] == ["Конкурс за избор у звање доцента за област Примерна математика", "Стручна пракса за студенте - позив"]
    assert [k[1][1] for k in kept if k[1][0]] == ["employment", "internship"]                                # the application form is dropped


def test_anchor_fallback_needs_a_date_or_a_post_like_url():
    html = ('<html><body><div class="content">'
            '<a href="/sr/konkursi">Конкурси за избор у звање наставника и сарадника</a>'                                     # bare section link
            '<p><a href="/sr/oglasi/konkurs-za-izbor-docenta-primerna-fizika">Конкурс за избор у звање доцента за област Физика</a></p>'   # long slug
            '<p><a href="/sr/oglasi/48213">Конкурс за избор у звање асистента за област Хемија</a></p>'                       # numeric id
            '<p><a href="/sr/oglasi/lista">Конкурс за избор у звање лектора за област Језик</a> <span>02.09.2026.</span></p>'  # dated
            '</div></body></html>')
    items = board.parse_items(html, "https://faculty.example.invalid/")
    assert {i.family for i in items} == {"anchor"} and len(items) == 4
    verdict = {i.title[:28]: board.keep(i)[0] for i in items}
    assert list(verdict.values()) == [False, True, True, True]                                                     # only the menu-like link is dropped
    assert board.post_like_url("https://x.invalid/wp-content/uploads/2019/03/a.docx") and board.post_like_url("https://x.invalid/2026/05/27/87772/")
    assert not board.post_like_url("https://x.invalid/sr/konkursi") and not board.post_like_url("https://x.invalid/fakultet/izbori-u-zvanja/konkursi/start")


# ---------------------------------------------------------------- keep rule / rows ------
def test_f10_rows_from_items_decodes_the_exact_real_pm_example_bcility_title():
    """T29/T29B (packet 017/019, PM's own exact named example - the real, live row's own verbatim title):
    'Плаћена пракса/запослење у компанији &quot;BCILITY&quot;'. Root cause: selectolax's own real HTML
    parser correctly decodes a normal, single-escaped entity - but the SOURCE SITE's own raw HTML was
    double-escaped ('&amp;quot;'), which any correct parser only unescapes ONE level, leaving a literal
    '&quot;' as real text content. rows_from_items() decodes once more, a safe no-op for every already-clean
    title."""
    it = board.Item(url="https://www.ftn.kg.ac.rs/docs/66991-56784-flajer.pdf",
                    title='Плаћена пракса/запослење у компанији &quot;BCILITY&quot;',
                    posted=None, teaser="", family="document")
    (row,), _ = board.rows_from_items([it], ORG, "k")
    assert row["title_raw"] == 'Плаћена пракса/запослење у компанији "BCILITY"'


def test_rows_keep_classifiable_items_only_and_never_invent_fields():
    items = board.parse_items(VEST, "https://faculty.example.invalid/sr/konkursi")
    rows, stats = board.rows_from_items(items, ORG, "raw/key")
    assert stats["items"] == 5 and stats["dropped_not_classifiable"] == 1                  # the student competition news item
    by = {r["title_raw"][:12]: r for r in rows}
    kinds = {r["ext_uid"]: r["kind"] for r in rows}
    assert sorted(kinds.values()) == ["employment", "employment", "enrollment", "grant_call"]
    r = next(r for r in rows if r["title_raw"].startswith("Асистент"))
    assert (r["position_kind"], r["contract"], r["duration_months_v"], r["duration_months_raw"]) == ("lecturer", "fixed", 36, "vreme od 3 godine")
    d = next(r for r in rows if "доцента" in r["title_raw"])
    assert (d["position_kind"], d["kind"], d["city"], d["org_raw"], d["ad_lang"]) == ("professor", "employment", "Belgrade", "ПРИМЕР ФАКУЛТЕТ", "sr-Cyrl")
    assert d["posted_at"].date() == date(2026, 9, 14)
    for row in rows:
        assert not any(k in row for k in ("deadline", "degree_required", "salary_min", "requirements_raw"))     # listing-level: unknown stays absent
        assert row["raw_text"] and row["country_iso2"] == "RS"
    assert len({r["ext_uid"] for r in rows}) == len(rows)


def test_position_only_title_counts_as_employment_and_news_does_not():
    it = lambda t: board.Item("https://x.invalid/a", t, None, t, "article")
    assert board.keep(it("Асистент за област Хемија")) == (True, "employment")
    assert board.keep(it("Конкурс за упис на докторске студије")) == (True, "enrollment")
    assert board.keep(it("Јубилеј факултета и свечана академија")) == (False, "other")


# ---------------------------------------------------------------- manifest / governance --
def test_org_manifest_is_the_host_family_and_refuses_hr_hosts():
    m = board.OrgManifest.for_hosts(["www.ff.bg.ac.rs", "ff.bg.ac.rs"])
    assert all(m.permits(u) for u in ["https://ff.bg.ac.rs/kategorija/konkursi/", "https://www.ff.bg.ac.rs/x?page=2", "http://www1.ff.bg.ac.rs/a"])
    assert not any(m.permits(u) for u in ["https://zaposleni.ff.bg.ac.rs/", "https://evil.ff.bg.ac.rs.example.com/", "https://other.bg.ac.rs/", "https://bg.ac.rs/ff"])
    for bad in ("zaposleni.med.bg.ac.rs", "servisi.pf.uns.ac.rs", "login.x.rs"):
        with pytest.raises(ValueError, match="R4"):
            board.OrgManifest.for_hosts(["www.ok.rs", bad])


def test_no_seeded_org_has_an_hr_host_or_would_be_refused_by_its_own_manifest():
    for o in orgs.load_seed():
        hosts = [__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(u).netloc for u in [o.get("homepage")] + list(o.get("career_urls") or []) if u]
        if hosts:
            m = board.OrgManifest.for_hosts(hosts)
            assert all(m.permits(u) for u in (o.get("career_urls") or [])), o["slug"]


# ---------------------------------------------------------------- crawl flow -------------
class Offline(board.BoardConnector):
    pages = {}
    fetched = []

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        Offline.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError("HTTP 503 (canned)")
        body = self.pages[url].encode()
        res = FetchResult(url, url, 200, body, "text/html", NOW)
        return res, put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW, status=200,
                            content_type="text/html", robots_allowed=True)


def test_crawl_reports_blocked_boards_with_reasons_and_ingests_the_rest(conn, tmp_path):
    orgs.seed_orgs(conn)
    org = next(t for t in orgs.crawl_targets(conn) if t["slug"] == "bg-etf")
    org = dict(org, career_urls=["https://www.etf.bg.ac.rs/sr/konkursi", "https://www.etf.bg.ac.rs/dead-board"])
    Offline.pages, Offline.fetched = {"https://www.etf.bg.ac.rs/sr/konkursi": VEST}, []
    store = LocalFsStore(tmp_path)
    c = Offline(PoliteClient(rps=1000), store, org)
    board.BoardConnector.ensure_source(conn)
    rows, rep = c.crawl()
    assert rep["pages_fetched"] == 1 and rep["rows"] == 4 and len(rep["blocked"]) == 1
    assert rep["blocked"][0]["url"].endswith("/dead-board") and "503" in rep["blocked"][0]["reason"]      # reason kept, never silent
    assert all(upsert_listing(conn, r, store)[1] for r in rows)
    got = conn.execute("SELECT org_id IS NOT NULL, city, kind FROM listing ORDER BY kind, title_raw").fetchall()
    assert len(got) == 4 and all(g[0] and g[1] == "Belgrade" for g in got)
    assert conn.execute("SELECT count(*) FROM listing WHERE kind='employment'").fetchone()[0] == 2         # default view: employment only


def test_crawler_never_picks_fsfv_or_offhost_urls(conn, tmp_path):
    orgs.seed_orgs(conn)
    assert "bg-fsfv" not in {t["slug"] for t in orgs.crawl_targets(conn)}
    org = {"id": None, "slug": "x", "homepage": "https://www.etf.bg.ac.rs/", "career_urls": ["https://www.etf.bg.ac.rs/a"]}
    c = Offline(PoliteClient(rps=1000), LocalFsStore(tmp_path), org)
    Offline.fetched = []
    with pytest.raises(ManifestViolation):
        c.fetch_raw("https://zaposleni.etf.bg.ac.rs/login")
    assert Offline.fetched == []


def test_title_dates_never_become_the_posting_date_regression():
    # a session date inside the title ("... 10.06.2026.") must not beat the permalink / <time> / body date
    assert board._date_from("Конкурс 10.06.2026. Објављено 16.06.2026.", "", "https://x.invalid/2026/06/16/s/", "Конкурс 10.06.2026.") == date(2026, 6, 16)
    assert board._date_from("Конкурс 10.06.2026. Објављено 17.06.2026.", "", "https://x.invalid/s/", "Конкурс 10.06.2026.") == date(2026, 6, 17)
    assert board._date_from("Конкурс 10.06.2026.", "", "https://x.invalid/s/", "Конкурс 10.06.2026.") is None        # only the title has a date: unknown
    assert board._date_from("x", "2026-09-14T10:12:21+02:00", "https://x.invalid/2020/01/01/s/", "x") == date(2026, 9, 14)


# ---- regressions from the first live pass over 21 Belgrade faculty boards (12 returned zero items) ----------
def test_layout_classes_on_body_and_headers_inside_articles_are_not_chrome():
    html = ('<html><body class="archive has-sidebar sidebar-right"><div id="page" class="site sidebar-layout"><main>'
            '<article><header class="entry-header"><h2 class="entry-title"><a href="/2026/06/16/a/">Конкурс за избор наставника на факултету</a></h2></header></article>'
            '</main><aside class="sidebar"><article><h2><a href="/2026/06/17/b/">Конкурс за избор у видџету бочне траке</a></h2></article></aside></div></body></html>')
    titles = [i.title for i in board.parse_items(html, "https://x.invalid/")]
    assert titles == ["Конкурс за избор наставника на факултету"]


def test_chrome_tokens_are_exact_not_substrings():
    from selectolax.parser import HTMLParser
    t = HTMLParser('<div class="content-sidebar-wrap"><a id="x" href="/a">a</a></div><div class="widget-area"><a id="y" href="/b">b</a></div>'
                   '<div class="menu-item"><a id="z" href="/c">c</a></div><nav><a id="w" href="/d">d</a></nav>')
    flags = {a.attributes["id"]: board.in_chrome(a) for a in t.css("a")}
    assert flags == {"x": False, "y": True, "z": True, "w": True}


@pytest.mark.parametrize("title,kind", [
    ("Конкурс за летњу праксу", "internship"), ("Стручна пракса у иностранству", "internship"), ("Konkurs za studentsku praksu", "internship"),
    ("Конкурс за најбољи научно-истраживачки и стручни рад студената", "other"),
    ("Конкурс за полазнике пете генерације Радионице економског новинарства", "other"),
    ("Конкурс за доделу финансијске подршке студентима докторских студија", "grant_call"),
    ("Konkurs za dodelu stipendija", "grant_call"), ("Конкурс за избор сарадника у звање асистента", "employment"),
    ("Конкурс за избор у звање доцента", "employment"), ("Konkurs za radno mesto laboranta", "employment"),
])
def test_kind_rules_after_live_findings(title, kind):
    from ajp.extract import rs_text
    assert rs_text.classify_kind(title) == kind


@pytest.mark.parametrize("title,keep", [
    ("ETF Robotics - три истраживачке позиције", True), ("Позив за младе истраживаче у области енергетике", True),
    ("Отворени позив за истраживаче", True), ("Виши научни сарадник", True),
    ("Конкурс за најбољи научно-истраживачки рад студената", False), ("Научни скуп о физици", False),
])
def test_researcher_person_nouns_count_but_research_things_do_not(title, keep):
    assert board.keep(board.Item("https://x.invalid/a", title, None, title, "article"))[0] is keep


def test_project_announcements_are_grant_calls_per_packet_004_not_jobs():
    assert board.keep(board.Item("https://x.invalid/a", "Истраживачки пројекат: резултати", None, "", "article")) == (True, "grant_call")


def test_layout_sidebar_classes_vs_real_sidebars():
    from selectolax.parser import HTMLParser
    t = HTMLParser('<div class="sidebar-layout"><a id="a" href="/1">a</a></div><div class="sidebar-right"><a id="b" href="/2">b</a></div>'
                   '<div class="sidebar-primary"><a id="c" href="/3">c</a></div><div id="sidebar"><a id="d" href="/4">d</a></div>'
                   '<div class="site-footer"><a id="e" href="/5">e</a></div>')
    assert {a.attributes["id"]: board.in_chrome(a) for a in t.css("a")} == {"a": False, "b": False, "c": True, "d": True, "e": True}


# ---- PM ruling R7-refinement: person-specific procedure notices are not listings -----------------------------
NAMED = [
    "Покренут поступак стицања научног звања за Петра Петровића", "Pokrenut postupak sticanja istraživačkog zvanja Marija Jovanović",
    "Покренут поступак за стицање звања: Јован Јовић", "Реферат за избор у звање доцента Ана Аничић",
    "Referat za izbor u zvanje vanrednog profesora Milan Milanović", "Извештај комисије за избор Јелене Јелић",
    "Izveštaj komisije za Nikolu Nikolića", "Увид јавности: реферат кандидата Марка Марковића", "Uvid javnosti – Petar Petrović, docent",
    "Одлука о избору у звање ванредног професора Милице Милић", "Милица Милић стиче звање редовног професора",
    "Извештаји редовних професора о петогодишњем раду", "Реферати за изборе у звања наставника и сарадника", "Увид јавности",
]
OPEN = [
    "Расписан конкурс за избор у звање доцента за ужу научну област Физика", "Конкурс за избор наставника: 10.06.2026. године",
    "Oglašen konkurs za radno mesto istraživača-saradnika", "Конкурс за избор у звање асистента - Увид јавности у услове конкурса",
    "Konkurs za radno mesto laboranta", "Poziv za mlade istraživače u oblasti energetike",
]


@pytest.mark.parametrize("title", NAMED)
def test_procedure_notices_are_excluded_by_type(title):
    from ajp.extract import rs_text
    assert rs_text.person_specific_procedure(title) == "person_specific_title_procedure"
    assert board.keep(board.Item("https://x.invalid/a", title, None, "", "article")) == (False, "person_specific_title_procedure")


@pytest.mark.parametrize("title", OPEN)
def test_open_calls_are_kept(title):
    from ajp.extract import rs_text
    assert rs_text.person_specific_procedure(title) is None
    ok, kind = board.keep(board.Item("https://x.invalid/a", title, None, "", "article"))
    assert ok and kind == "employment"


def test_named_notices_leave_no_trace_in_rows_or_db_but_the_raw_page_is_kept(conn, tmp_path):
    import psycopg
    from ajp.connectors import board as b
    from ajp.rawstore import LocalFsStore, get_raw
    items = "".join(f'<article><h3><a href="/2026/09/{i:02d}/x-{i}/">{t}</a></h3><time datetime="2026-09-{i + 1:02d}">x</time></article>'
                    for i, t in enumerate(["Покренут поступак стицања научног звања за Петра Петровића", "Реферат за избор у звање Марија Јовановић",
                                           "Извештај комисије за Јована Јовића", "Расписан конкурс за избор у звање доцента за област Физика"]))
    html = f"<html><body><main>{items}</main></body></html>"
    from stubserver import Stub
    s = Stub(); s.robots("").route("/board", body=html.encode())
    try:
        org = {"id": None, "slug": "v", "name_en": "V", "city": "Belgrade", "homepage": s.base + "/", "career_urls": [s.base + "/board"], "render_mode": "httpx"}
        store = LocalFsStore(tmp_path)
        c = b.BoardConnector(PoliteClient(rps=1000, sleep=lambda x: None), store, org)
        rows, rep = c.crawl()
    finally:
        s.stop()
    assert len(rows) == 1 and rows[0]["title_raw"].startswith("Расписан конкурс") and rep["excluded_person_specific"] == 3
    blob = json.dumps(rows, ensure_ascii=False, default=str) + json.dumps(rep, ensure_ascii=False, default=str)
    assert not any(name in blob for name in ("Петров", "Јовановић", "Јовић", "Petar"))          # nothing personal in rows or report
    body, meta = get_raw(store, rows[0]["raw_html_ref"])
    assert "Петра Петровића" in body.decode() and meta["robots_allowed"] is True                    # raw capture retained as collected


def test_url_shaped_link_text_is_not_a_title_regression_from_live_run():
    html = ('<html><body><main><article><h3><a href="/2026/09/01/a/">https://www.faculty.example.invalid/sr/obavestenja/konkursi-za-izbore-u-zvanja</a></h3></article>'
            '<article><h3><a href="/2026/09/02/b/">Конкурс за избор у звање доцента за област Физика</a></h3></article></main></body></html>')
    assert [i.title for i in board.parse_items(html, "https://faculty.example.invalid/")] == ["Конкурс за избор у звање доцента за област Физика"]
    assert not board._good_title("www.example.invalid/some/path") and board._good_title("Конкурс за избор у звање доцента")


def test_f10_the_exact_real_pm_date_sequence_archive_index_title_is_rejected():
    """T31 (packet 019): a monthly-archive index page's own anchor 'title' can be nothing but a bare list of
    dd.mm. tokens - PM's own real example. Never a real announcement title, whatever its length."""
    assert not board._good_title("23.09. 16.09. 22.07. 03.06. 12.05. 01.04.")


@pytest.mark.parametrize("title", [
    "Konkurs za izbor u zvanje docenta 10.06.2026. godine",         # one real date, a real sentence around it
    "Расписан конкурс за избор у звање доцента за ужу научну област Физика",
    "23.09.2026 16.09.2026",                                        # only 2 date-shaped tokens - below the guard's own threshold
])
def test_the_date_sequence_guard_never_overreaches_a_real_title(title):
    assert board._good_title(title)


# ---- PM batch 3: document library (EKOF-shaped), extraction_family, FON title, FSFV zero captures ----------
DOCS = """<html><body><main><h1>Конкурси за изборе у звања</h1>
<article><a href="/wp-content/uploads/2019/03/Konkurs-EF12.docx">Преузми</a></article>
<article><a href="/wp-content/uploads/2019/05/Konkurs-za-izbor-docenta.pdf">Конкурс за избор у звање доцента за област Економија</a></article>
<article><a href="/wp-content/uploads/2019/06/Referat-Petrovic-Petar.docx">Реферат</a></article>
<article><a href="/wp-content/uploads/2020/01/Konkurs-za-upis-studenata.pdf">Конкурс за упис студената</a></article>
<article><a href="/wp-content/uploads/2019/07/Obrazac-prijave.pdf">Образац</a></article>
</main><nav><a href="/wp-content/uploads/menu.pdf">Конкурс у менију</a></nav></body></html>"""
EKOF_LIKE = "https://faculty.example.invalid/o-fakultetu/konkursi-i-referati/konkursi-za-izbore-u-zvanja/"


def test_document_library_rows_are_undated_low_trust_and_link_only():
    items = board.parse_items(DOCS, EKOF_LIKE)
    assert [i.family for i in items] == ["document"] * 5 and not any("menu" in i.url for i in items)          # nav document excluded
    rows, stats = board.rows_from_items(items, ORG, "raw/key")
    by = {r["url"].rsplit("/", 1)[1]: r for r in rows}
    assert set(by) == {"Konkurs-EF12.docx", "Konkurs-za-izbor-docenta.pdf", "Konkurs-za-upis-studenata.pdf"}
    assert stats["excluded_person_specific"] == 1                                                              # 'Referat-Petrovic-Petar.docx' by FILENAME
    assert by["Konkurs-EF12.docx"]["title_raw"] == "Konkurs EF12" and by["Konkurs-EF12.docx"]["kind"] == "employment"   # 'Преузми' -> filename, board hint
    assert by["Konkurs-za-upis-studenata.pdf"]["kind"] == "enrollment"
    for r in rows:
        assert r["extraction_family"] == "document" and "posted_at" not in r and r["raw_html_ref"] == "raw/key"     # undated; provenance = board page
        assert r["status"] == "unknown"                                                                            # q2: undated never defaults to 'open'
        assert not any(k in r for k in ("deadline", "degree_required", "salary_min", "requirements_raw"))         # documents are NOT parsed
    assert "Petrovic" not in json.dumps(rows, ensure_ascii=False)


def test_document_words_alone_do_not_make_employment_on_a_non_employment_board():
    items = board.parse_items(DOCS, "https://faculty.example.invalid/studenti/dokumenta/")
    rows, _ = board.rows_from_items(items, ORG, "k")
    assert {r["url"].rsplit("/", 1)[1] for r in rows} == {"Konkurs-za-izbor-docenta.pdf", "Konkurs-za-upis-studenata.pdf"}   # bare 'Konkurs-EF12' needs the board hint


def test_rows_record_their_extraction_family():
    for html, fam in ((VEST, "article"), (WP, "wp_permalink"), (DOCS, "document")):
        rows, _ = board.rows_from_items(board.parse_items(html, EKOF_LIKE), ORG, "k")
        assert rows and {r["extraction_family"] for r in rows} == {fam}


def test_fon_uvid_javnosti_title_is_excluded_as_a_section_heading():
    from ajp.extract import rs_text
    for title in ("Увид јавности", "Uvid javnosti", "УВИД ЈАВНОСТИ"):                       # the exact title that survived live run 3
        assert board.keep(board.Item("https://fon.example.invalid/uvid-javnosti/", title, None, "", "anchor")) == (False, "person_specific_title_procedure")
    assert rs_text.person_specific_procedure("Конкурс за избор у звање доцента - увид јавности у услове") is None    # an open call survives


# ---- live finding: MAS board = archive of konkurs_mf_DDMMYYYY.pdf (2003-2026), 148 undated "open" rows ------
def test_filename_date_year_bound_is_2010_to_next_year():
    from datetime import date as _d
    assert board.FILENAME_YEAR_MIN == 2010
    assert board.date_from_filename("https://x.invalid/a/konkurs_16092009.pdf") is None      # 2009: before the bound
    assert board.date_from_filename("https://x.invalid/a/konkurs_16092010.pdf") == _d(2010, 9, 16)
    year_max = datetime.now(timezone.utc).year + 1
    assert board.date_from_filename(f"https://x.invalid/a/konkurs_1609{year_max}.pdf") == date(year_max, 9, 16)
    assert board.date_from_filename(f"https://x.invalid/a/konkurs_1609{year_max + 1}.pdf") is None


def test_document_posted_date_basis_and_conflict_guard():
    d, basis, conf, raw = board.document_posted_date("Anchor text with no date", "https://x.invalid/a/konkurs_16092026.pdf")
    assert (d, basis, conf, raw) == (date(2026, 9, 16), "filename", 0.4, "konkurs_16092026.pdf")
    assert board.document_posted_date("Text", "https://x.invalid/a/no-date-here.pdf") == (None, None, 0.0, None)
    # anchor text states a DIFFERENT date than the filename: unknown rather than a guess
    assert board.document_posted_date("Конкурс, објављено 01.01.2026.", "https://x.invalid/a/konkurs_16092026.pdf") == (None, None, 0.0, None)
    # anchor text states the SAME date: no conflict, filename basis still used
    d, basis, conf, raw = board.document_posted_date("Конкурс, објављено 16.09.2026.", "https://x.invalid/a/konkurs_16092026.pdf")
    assert (d, basis) == (date(2026, 9, 16), "filename")


def test_document_row_carries_posted_at_basis_conf_raw():
    items = board.parse_items(
        '<html><body><main><article><a href="/x/konkurs_16092026.pdf">Конкурс за избор у звање доцента</a></article></main></body></html>',
        "https://f.example.invalid/konkursi-za-izbore-u-zvanja/")
    (row,), _ = board.rows_from_items(items, ORG, "k")
    assert row["posted_at"].date() == date(2026, 9, 16)
    assert (row["posted_at_basis"], row["posted_at_conf"], row["posted_at_raw"]) == ("filename", 0.4, "konkurs_16092026.pdf")


# ---- T34 (packet 018/020, PM-verified defect): scanned-PDF title mojibake, 33 real rs_org_boards rows ------
def test_f10_the_real_root_cause_a_declared_non_utf8_charset_is_respected_not_assumed_utf8():
    """PM's own real named example (medf.kg.ac.rs, Konkursi_od_03062026_POSLOVI.pdf): the server's own real
    Content-Type header states 'charset=windows-1251' - decoding those same bytes as utf-8 (this module's
    OLD hardcoded behaviour) produces a stream of U+FFFD, which is exactly what ended up stored as
    title_raw/raw_text. The real root cause, not the PDF-extraction path (content_scanned was already
    correctly True on every affected row - the anchor text on the LISTING page was already garbled before
    any document was ever opened)."""
    cyrillic = "Пријава на конкурс".encode("windows-1251")
    assert board._decode_html(cyrillic, "text/html; charset=windows-1251") == "Пријава на конкурс"
    # decoding the SAME bytes as utf-8 (the old bug) would have produced U+FFFD garbage - confirms this
    # fixture genuinely exercises the real defect, not a no-op.
    assert "�" in cyrillic.decode("utf-8", "replace")


def test_f10_no_declared_charset_or_an_unknown_one_falls_back_to_utf8_unchanged():
    utf8_bytes = "Конкурс за избор".encode("utf-8")
    assert board._decode_html(utf8_bytes, "text/html") == "Конкурс за избор"
    assert board._decode_html(utf8_bytes, None) == "Конкурс за избор"
    assert board._decode_html(utf8_bytes, "text/html; charset=not-a-real-codec") == "Конкурс за избор"


def test_f10_a_garbled_anchor_title_falls_back_to_the_humanized_filename_never_stores_the_garbage():
    """The defensive backstop, on top of the root-cause charset fix: even if some OTHER server's own
    mis-decoded bytes ever slip a U+FFFD-laden title through, it must never be stored - PM's own required
    title resolution order (anchor text -> humanized filename -> generic placeholder)."""
    garbled_title = "��� � � ��� �� � ."
    it = board.Item("https://medf.kg.ac.rs/informacije/konkursi/oglasna_tabla/Konkursi_od_03062026_POSLOVI.pdf",
                    garbled_title, None, garbled_title, "document")
    (row,), _ = board.rows_from_items([it], ORG, "k")
    assert "�" not in row["title_raw"]
    assert row["title_raw"] == "Konkursi od 03062026 POSLOVI"     # the real filename-humanized fallback
    assert "�" not in (row["raw_text"] or "")


def test_f10_a_clean_title_is_never_touched_by_the_garbage_guard():
    it = board.Item("https://x.invalid/a.pdf", "Конкурс за избор у звање доцента", None, "teaser text", "document")
    (row,), _ = board.rows_from_items([it], ORG, "k")
    assert row["title_raw"] == "Конкурс за избор у звање доцента"
    assert row["raw_text"] == "teaser text"


def test_stale_after_days_is_overridable_per_org():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    it = board.Item("https://x.invalid/2026/01/01/a/", "Конкурс за избор у звање доцента за област Физика", date(2026, 6, 1), "", "article")
    (r1,), _ = board.rows_from_items([it], ORG, "k", now=now)          # 113 days: within the module default (120)
    assert "status" not in r1
    (r2,), _ = board.rows_from_items([it], dict(ORG, crawl_config={"stale_after_days": 30}), "k", now=now)
    assert r2["status"] == "expired"


@pytest.mark.parametrize("url,expected", [
    ("https://x.invalid/konkursi/2026/konkurs__mf_16092026.pdf", date(2026, 9, 16)),
    ("https://x.invalid/a/Konkurs-2026-09-16.pdf", date(2026, 9, 16)), ("https://x.invalid/a/konkurs_16.09.2026.docx", date(2026, 9, 16)),
    ("https://x.invalid/wp-content/uploads/2019/03/Konkurs-EF-31.docx", None),          # directory year/month is not a filename date
    ("https://x.invalid/a/konkurs_31132026.pdf", None),                                 # month 13: not a date
    ("https://x.invalid/a/konkurs_16092099.pdf", None),                                 # future / implausible
    ("https://x.invalid/a/Konkurs%20EF%2012.docx", None),
])
def test_date_from_filename_only_when_a_full_valid_date_is_written(url, expected):
    assert board.date_from_filename(url) == expected


def test_archive_documents_get_their_stated_date_and_old_ones_are_stored_expired():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    html = ('<html><body><main>' + "".join(f'<article><a href="/izbori/konkursi/{y}/konkurs_mf_{d}.pdf">Конкурс {d}</a></article>'
            for y, d in ((2026, "16092026"), (2026, "24062026"), (2015, "21102015"))) +
            '<article><a href="/wp-content/uploads/2019/05/Konkurs-EF-31.docx">Конкурс за избор у звање</a></article></main></body></html>')
    rows, _ = board.rows_from_items(board.parse_items(html, "https://f.example.invalid/izbori-u-zvanja/konkursi/"), ORG, "k", now=now)
    by = {r["url"].rsplit("/", 1)[1]: r for r in rows}
    assert by["konkurs_mf_16092026.pdf"]["posted_at"].date() == date(2026, 9, 16) and "status" not in by["konkurs_mf_16092026.pdf"]   # open (default)
    assert "status" not in by["konkurs_mf_24062026.pdf"]                                       # 90 days old: within the 120-day horizon
    assert by["konkurs_mf_21102015.pdf"]["status"] == "expired"                                 # a 2015 archive entry is not an open position
    assert by["Konkurs-EF-31.docx"]["status"] == "unknown" and "posted_at" not in by["Konkurs-EF-31.docx"]   # no date anywhere: undated -> unknown, never guessed


def test_stale_boundary_is_exactly_120_days():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    mk = lambda d: board.Item("https://x.invalid/2026/01/01/a/", "Конкурс за избор у звање доцента за област Физика", d, "", "article")
    edge, over = date(2026, 5, 25), date(2026, 5, 24)                # 120 and 121 days before now
    assert (now.date() - edge).days == 120 and (now.date() - over).days == 121
    (r1,), _ = board.rows_from_items([mk(edge)], ORG, "k", now=now)
    (r2,), _ = board.rows_from_items([mk(over)], ORG, "k", now=now)
    assert "status" not in r1 and r2["status"] == "expired"


# ---- accordion-style boards: the real title sits in a sibling heading, not the (generic) document link ------
ACCORDION = ('<html><body><main><h1>Конкурси</h1>'
            '<ul class="accordion">'
            '<a class="toggle"><h3>Универзитет – Факултет расписује конкурс за избор доцента за област Физика</h3></a>'
            '<div class="inner"><div class="li"><p>[<a href="/wp-content/uploads/2026/09/09.09.2026.Konkurs.pdf" target="_blank">Текст конкурса</a>]</p></div></div>'
            '</ul></main></body></html>')


def test_accordion_document_link_uses_the_sibling_heading_not_the_generic_label():
    items = board.parse_items(ACCORDION, "https://f.example.invalid/konkursi/")
    assert len(items) == 1 and items[0].family == "document"
    assert items[0].title == "Универзитет – Факултет расписује конкурс за избор доцента за област Физика"
    assert board.keep(items[0]) == (True, "employment")


def test_wp_uploads_document_url_is_never_treated_as_a_post_permalink():
    """Regression: /wp-content/uploads/YYYY/MM/file.pdf matches the /YYYY/MM/ shape of a post permalink but is an
    attachment - it must fall through to the 'document' family (with its generic-label + nearby-heading handling),
    never the 'wp_permalink' family (which would keep 'Текст конкурса' verbatim as the title)."""
    items = board.parse_items(ACCORDION, "https://f.example.invalid/konkursi/")
    assert items[0].family == "document" and items[0].title != "Текст конкурса"


def test_nearby_heading_never_picks_up_a_shared_page_h1_or_an_unrelated_sibling():
    html = ('<html><body><main><h1>Конкурси за изборе у звања</h1>'                          # page h1: excluded on purpose
            '<article><a href="/wp-content/uploads/2019/03/Konkurs-EF12.docx">Преузми</a></article>'   # generic label, no h2-h6 nearby
            '<article><a href="/wp-content/uploads/2019/07/Obrazac-prijave.pdf">Образац</a></article>'  # generic-ish label, prev sibling is a substantive <article>, not a heading
            '</main></body></html>')
    items = board.parse_items(html, "https://f.example.invalid/dokumenta/")
    titles = [i.title for i in items]
    assert "Конкурси за изборе у звања" not in titles                                        # never inherited from the page h1
    assert titles == ["Konkurs EF12", "Obrazac prijave"]                                      # fall back to filename, as before


def test_nearby_heading_stops_at_the_first_substantive_sibling():
    html = ('<html><body><main>'
            '<h2>Right heading for the next item</h2>'
            '<div class="unrelated-block">some other content, not a heading, but substantial enough to block the search</div>'
            '<div class="wrapper"><a href="/wp-content/uploads/2026/01/Konkurs-x.pdf">Преузми</a></div>'
            '</main></body></html>')
    items = board.parse_items(html, "https://f.example.invalid/konkursi/")
    assert items[0].title == "Konkurs x"                                                     # the unrelated block blocks the h2 beyond it, so falls back to filename


# ---- PM ruling: malformed dates (agrif source typo) must never crash or coerce ------------------------------
@pytest.mark.parametrize("text", [
    "Наслов конкурса ... 23.109.2024. неки текст",     # PM's exact string: month has 3 digits, does not even match the pattern
    "рок 32.13.2024.", "истиче 29.02.2023.",           # matches the dd.mm.yyyy shape but day/month/leap-year are impossible
    "датум 31.04.2026.", "датум 00.05.2026.",          # April has 30 days; day 0 does not exist
])
def test_malformed_dates_never_crash_and_are_never_coerced(text):
    assert board._date_from(text) is None                # unknown, never guessed at a "closest" valid date


def test_document_posted_date_conflict_check_survives_a_malformed_anchor_date():
    d, basis, conf, raw = board.document_posted_date("објављено 32.13.2024.", "https://x.invalid/a/konkurs_16092026.pdf")
    assert (d, basis) == (date(2026, 9, 16), "filename")   # the malformed text date cannot conflict-check; filename date still used


def test_mk_rejects_every_invalid_calendar_date():
    for y, mo, d in [(2024, 109, 23), (2024, 13, 32), (2023, 2, 29), (2026, 4, 31), (2026, 5, 0), (2026, 0, 5)]:
        assert board._mk(y, mo, d) is None


# ---- PM ruling: agrif's real pattern is DATE-<p> + TITLE-<p ending in the generic link>, not an accordion -----
AGRIF_LIKE = ('<html><body><main><div class="strana-body">'
             '<p>08.07.2026.</p>'
             '<p>УНИВЕРЗИТЕТ У ПРИМЕРУ ФАКУЛТЕТ расписује конкурс за избор сарадника ван радног односа '
             '<a href="/uploads/files/strane/Konkursi/Konkurs%20(4).pdf">Текст конкурса</a></p>'
             '<p>10.06.2026.</p>'
             '<p>УНИВЕРЗИТЕТ У ПРИМЕРУ ФАКУЛТЕТ расписује конкурс за избор у звање и заснивање радног односа '
             '<a href="/uploads/files/strane/Konkursi/Konkurs%20(5).pdf">Текст конкурса</a></p>'
             '</div></main></body></html>')


def test_own_paragraph_text_is_used_when_the_link_sits_inside_a_descriptive_paragraph():
    items = board.parse_items(AGRIF_LIKE, "https://f.example.invalid/konkursi")
    assert len(items) == 2 and all(i.family == "document" for i in items)
    assert items[0].title == "УНИВЕРЗИТЕТ У ПРИМЕРУ ФАКУЛТЕТ расписује конкурс за избор сарадника ван радног односа"
    assert items[1].title == "УНИВЕРЗИТЕТ У ПРИМЕРУ ФАКУЛТЕТ расписује конкурс за избор у звање и заснивање радног односа"
    assert items[0].url != items[1].url                                          # distinct URLs -> distinct rows, no dedupe collision
    assert all(board.keep(i) == (True, "employment") for i in items)
    rows, _ = board.rows_from_items(items, ORG, "k")
    assert len({r["ext_uid"] for r in rows}) == 2
    # parity ruling (2026-09-22): the lone-date sibling paragraph IS now a legitimate source, on par with a filename date
    assert [r["posted_at"].date() for r in rows] == [date(2026, 7, 8), date(2026, 6, 10)]
    assert [(r["posted_at_basis"], r["posted_at_conf"], r["posted_at_raw"]) for r in rows] == \
        [("visible_text", 0.4, "08.07.2026."), ("visible_text", 0.4, "10.06.2026.")]


def test_visible_sibling_date_never_crosses_into_the_previous_items_own_content():
    # the sibling BEFORE the date paragraph is a full title+link of the PREVIOUS item - a single hop stops right
    # at the date paragraph and must not be fooled into reading further back or picking up that other item's text.
    html = ('<html><body><main><div class="strana-body">'
            '<p>Претходна ставка нема сопствени датум, само наслов и линк '
            '<a href="/x/prev.pdf">Текст конкурса</a></p>'
            '<p>08.07.2026.</p>'
            '<p>Стварни наслов ове ставке '
            '<a href="/x/current.pdf">Текст конкурса</a></p>'
            '</div></main></body></html>')
    items = board.parse_items(html, "https://f.example.invalid/konkursi")
    by = {i.url.rsplit("/", 1)[1]: i for i in items}
    assert by["current.pdf"].posted == date(2026, 7, 8) and by["current.pdf"].date_basis == "visible_text"
    assert by["prev.pdf"].posted is None                        # no date paragraph precedes IT; never inherits the other item's date


def test_visible_sibling_date_requires_the_whole_sibling_to_be_only_a_date():
    html = ('<html><body><main><div class="strana-body">'
            '<p>Датум објаве: 08.07.2026. (напомена)</p>'                       # extra text alongside the date: not a lone-date paragraph
            '<p>Наслов конкурса овде <a href="/x/a.pdf">Текст конкурса</a></p>'
            '</div></main></body></html>')
    items = board.parse_items(html, "https://f.example.invalid/konkursi")
    assert items[0].posted is None and items[0].date_basis is None


def test_visible_sibling_date_respects_the_same_year_bound_and_rejects_invalid_calendar_dates():
    from ajp.connectors.board import visible_sibling_date
    from selectolax.parser import HTMLParser
    def item(date_text):
        html = f'<p>{date_text}</p><p>Наслов <a href="/x/a.pdf">Текст конкурса</a></p>'
        return HTMLParser(html).css_first("a")
    assert visible_sibling_date(item("08.07.2026.")) == (date(2026, 7, 8), "08.07.2026.")
    assert visible_sibling_date(item("08.07.2009.")) is None       # before FILENAME_YEAR_MIN
    assert visible_sibling_date(item("32.13.2024.")) is None       # impossible calendar date
    assert visible_sibling_date(item("not a date at all")) is None


def test_own_block_title_requires_meaningfully_more_text_than_the_label_alone():
    from selectolax.parser import HTMLParser
    # only a couple of stray characters beyond the generic label: not treated as a real title
    a = HTMLParser('<p>· <a href="/x/konkurs_16092026.pdf">Текст конкурса</a></p>').css_first("a")
    assert board._own_block_title(a, "Текст конкурса") is None
    # a real sentence beyond the label: used
    a = HTMLParser('<p>Конкурс за избор у звање доцента за област Физика <a href="/x/k.pdf">Текст конкурса</a></p>').css_first("a")
    assert board._own_block_title(a, "Текст конкурса") == "Конкурс за избор у звање доцента за област Физика"


# ---- np.ac.rs (DUNP): a document link sits ALONE in its own <td> ('PDF' button, nothing else - _own_block_title
# finds no extra text there), but a sibling <td> in the same <tr> holds the real title, alongside a short
# type-badge cell ('ОГЛАС') that _good_title's length floor already excludes as a candidate. --------------------
def test_table_row_title_picks_the_sibling_cell_not_the_badge():
    html = ('<table><tr>'
            '<td><span class="badge">ОГЛАС</span></td>'
            '<td>Оглас за избор демонстратора</td>'
            '<td><a href="/assets/docs/14.pdf">PDF</a></td>'
            '</tr></table>')
    items = board.parse_items(html, "https://np.example.invalid/studenti/konkursi-univerziteta.html")
    assert items[0].title == "Оглас за избор демонстратора"


def test_table_row_title_is_none_outside_a_tr_td_structure():
    from selectolax.parser import HTMLParser
    a = HTMLParser('<div><a href="/x/k.pdf">PDF</a></div>').css_first("a")
    assert board._table_row_title(a) is None


# ---- R5: va.mod.gov.rs (military) defaults foreigner_eligible='no'. A DB-sourced org dict (crawl_targets())
# always HAS the 'foreigner_eligible_default' key, just with value None (SQL NULL) for every ordinary org -
# a two-arg dict.get() default would NOT catch that (the key is present), only 'or' does; caught live via the
# full offline suite's DB-backed crawl test, not this module's own dict-literal ORG fixture. -------------------
def test_foreigner_eligible_default_handles_both_a_missing_key_and_a_present_none_value():
    item = board.Item("https://va.example.invalid/konkurs-1", "Konkurs za izbor nastavnika", None, "", "sitemap")
    rows, _ = board.rows_from_items([item], {**ORG, "foreigner_eligible_default": "no"}, "k")
    assert rows[0]["foreigner_eligible"] == "no"
    rows, _ = board.rows_from_items([item], {**ORG, "foreigner_eligible_default": None}, "k")   # DB NULL, key present
    assert rows[0]["foreigner_eligible"] == "unknown"
    rows, _ = board.rows_from_items([item], ORG, "k")                                            # key absent entirely
    assert rows[0]["foreigner_eligible"] == "unknown"


# ---- itn.sanu.ac.rs: 'Прилози (PDF)' (plural, g->z stem alternation + format annotation) is generic too -------
@pytest.mark.parametrize("label", ["Прилози (PDF)", "Прилог (PDF)", "prilozi", "Prilog (Word)", "Dokumenti (PDF)", "документа."])
def test_generic_doc_label_covers_plurals_and_format_annotations(label):
    assert board._GENERIC_DOC_LABEL.match(label.strip())


def test_dossier_attachment_filenames_are_excluded_as_person_specific():
    from ajp.extract import rs_text
    for fn in ["Izborni-materijal-JZ-naucni-saradnik.pdf", "Dokumentacija-izbor-za-istrazivaca-saradnika-AA-ISR-2020.pdf"]:
        assert rs_text.person_specific_procedure(board.filename_title("https://x/" + fn)) == "person_specific_title_procedure"


def test_prilozi_pdf_falls_back_to_filename_title_not_the_generic_label_itself():
    html = ('<html><body><main><article>'
            '<a href="/images/naucno_vece/izbor_u_zvanje-DK.pdf">Прилози (PDF)</a>'
            '</article></main></body></html>')
    items = board.parse_items(html, "https://itn.example.invalid/novosti/obavestenja-naucnog-veca")
    assert items[0].title == "izbor u zvanje DK"                    # never the bare generic label, whatever its length
    # KNOWN RESIDUAL LIMITATION (disclosed, not silently accepted): 'izbor u zvanje <initials>' matches the same
    # classify_kind employment rule as a genuine open call ('izbor u zvanje docenta'), and nothing here can tell a
    # position/discipline noun from a candidate's initials without guessing at names - a small number of itn.sanu's
    # bare-initial dossier attachments may still surface as kind=employment. Most of that board's real content
    # (report/materijal/dokumentacija-labelled attachments) IS correctly excluded - see the two tests above.
    assert board.keep(items[0]) == (True, "employment")


# ---- vinca.rs: Joomla mega-menu chrome + a Joomla category LIST-VIEW table family ------------------------------
JOOMLA_MEGAMENU_PAGE = (
    '<html><body>'
    '<div class="jf_mm_pro_wrapper"><ul class="jf_mm_pro"><li class="item-1"><ul class="jf_mm_pro_subm">'
    '<li><a href="/files/brosura.pdf">Institute Brochure</a></li></ul></li></ul></div>'
    '<form id="adminForm"><table class="com-content-category__table">'
    '<tr class="cat-list-row0"><th class="list-title" scope="row">'
    '<a href="/o-institutu/naucno-vece/izbori-u-zvanja/pokrenut-postupak-za-nekog">'
    'Покренут поступак стицања научног звања ВИШИ НАУЧНИ САРАДНИК за др Некога'
    '</a></th><td class="list-date small">10.09.2026</td></tr>'
    '<tr class="cat-list-row1"><th class="list-title" scope="row">'
    '<a href="/o-institutu/naucno-vece/izbori-u-zvanja/raspisan-konkurs-za-izbor-docenta">'
    'Расписан конкурс за избор у звање доцента за област Физика'
    '</a></th><td class="list-date small">08.09.2026</td></tr>'
    '</table>'
    '<a href="/o-institutu/naucno-vece/izbori-u-zvanja?start=10">2</a></form></body></html>'
)


def test_joomla_megamenu_brochure_link_is_chrome_not_content():
    from selectolax.parser import HTMLParser
    t = HTMLParser(JOOMLA_MEGAMENU_PAGE)
    a = t.css_first('a[href*="brosura"]')
    assert board.in_chrome(a)


def test_joomla_category_list_table_is_a_real_family_and_the_menu_never_preempts_it():
    items = board.parse_items(JOOMLA_MEGAMENU_PAGE, "https://institute.example.invalid/izbori-u-zvanja")
    assert [i.family for i in items] == ["table", "table"]           # not 'document' (the menu's brochure link never counts)
    assert items[0].posted == date(2026, 9, 10) and items[1].posted == date(2026, 9, 8)
    verdicts = [board.keep(i) for i in items]
    assert verdicts[0] == (False, "person_specific_title_procedure")
    assert verdicts[1] == (True, "employment")                        # a genuine open call in the same table is still kept
    assert board.next_page_url(JOOMLA_MEGAMENU_PAGE, "https://institute.example.invalid/izbori-u-zvanja", 1) == \
        "https://institute.example.invalid/o-institutu/naucno-vece/izbori-u-zvanja?start=10"


def test_table_family_never_touches_unrelated_tables_without_a_list_title_cell():
    html = ('<html><body><table class="staff-directory">'
           '<tr><th>Name</th><th>Role</th></tr>'
           '<tr><td>Jovan Jovic</td><td>Technician</td></tr>'
           '</table></body></html>')
    assert board.parse_items(html, "https://x.invalid/") == []


# ---- iit.ac.rs: news narrating that a researcher DID something is not a job opening ----------------------------
@pytest.mark.parametrize("title,expect_keep", [
    ("Истраживачица Института учествовала на летњој школи InnoThyroGen", False),
    ("Учешће истраживача Института на Београдској конференцији", False),
    ("Научни допринос истраживача Института представљен на скупу European Muscle Conference", False),
    ("Истраживачи са Института учесници семинара о дигитализацији", False),
    ("Истраживачи Института освојили прву награду на конгресу", False),
    ("Институт је био домаћин kick-off састанка пројекта", False),
    ("Обележено 10 година рада Института", False),
    ("Позив за младе истраживаче у области енергетике", True),          # a genuine call still passes
    ("Отворени позив за истраживаче", True),
    ("Отворен позив за учешће у пројекту SPARK Британског савета у Србији", True),   # filum.kg.ac.rs: 'ucesce' as
])                                                                       # the OBJECT of a call, not a narrative
def test_narrative_news_about_researchers_is_not_a_job_ad(title, expect_keep):
    it = board.Item("https://x.invalid/a", title, None, title, "article")
    assert board.keep(it)[0] is expect_keep


@pytest.mark.parametrize("title", ["Konkurs za dodelu stipendija", "Јавни позив за доделу стипендија студентима докторских студија",
                                   "Свечана додела признања пројектима подржаним у оквиру Центра за промоцију науке"])
def test_dodela_stays_untouched_by_the_narrative_guard_scholarship_calls_still_kept(title):
    # 'dodela' is tense-neutral in Serbian and was deliberately excluded from the narrative-past guard; the
    # scholarship-call cases must still be kept even though the ceremonial-award case is a known residual gap
    from ajp.extract import rs_text
    assert not rs_text._NARRATIVE_PAST.search(rs_text.normalize_title(title))


# ---- imgge.bg.ac.rs: YOOtheme '.el-item' cards (heading + separate generic link) + month-name dates -----------
YOOTHEME_CARD_PAGE = (
    '<html><body><main>'
    '<div class="el-item uk-panel"><div class="uk-grid-medium">'
    '<div class="uk-width-1-3@m"><img src="/x.jpg"></div>'
    '<div class="uk-width-expand"><h3 class="el-title">Otvorene pozicije u IMGGI</h3>'
    '<div class="el-meta uk-text-meta">21 Avgust 2026</div>'
    '<div><a href="/lat/novosti/otvorene-pozicije-u-imggi" class="el-link uk-button-text">Pročitaj više...</a></div>'
    '</div></div></div>'
    '<div class="el-item uk-panel"><div class="uk-grid-medium">'
    '<div class="uk-width-1-3@m"><img src="/y.jpg"></div>'
    '<div class="uk-width-expand"><h3 class="el-title">Noć istraživača 2026</h3>'
    '<div class="el-meta uk-text-meta">18 Septembar 2026</div>'
    '<div><a href="/lat/novosti/noc-istrazivaca" class="el-link uk-button-text">Pročitaj više...</a></div>'
    '</div></div></div>'
    '</main>'
    '<div class="fs-loadmore__button-container"><a href="/lat/novosti?start=12" class="fs-loadmore__button" '
    'data-fs-loadmore-button><span>Učitaj više</span></a></div>'
    '</body></html>'
)


def test_card_family_reads_the_heading_not_the_generic_readmore_link():
    items = board.parse_items(YOOTHEME_CARD_PAGE, "https://imgge.example.invalid/lat/novosti")
    assert [i.family for i in items] == ["card", "card"]
    assert items[0].title == "Otvorene pozicije u IMGGI" and items[0].posted == date(2026, 8, 21)
    assert items[0].date_basis == "visible_text" and items[0].date_conf == 0.4 and items[0].date_raw == "21 Avgust 2026"
    assert items[1].title == "Noć istraživača 2026" and items[1].posted == date(2026, 9, 18)
    assert items[0].url.endswith("/otvorene-pozicije-u-imggi") and items[1].url.endswith("/noc-istrazivaca")


def test_card_family_classification_open_positions_kept_researchers_night_excluded():
    items = board.parse_items(YOOTHEME_CARD_PAGE, "https://imgge.example.invalid/lat/novosti")
    assert board.keep(items[0]) == (True, "employment")               # 'Otvorene pozicije' - a real open-positions post
    assert board.keep(items[1]) == (False, "other")                   # 'Noć istraživača' - a public-outreach event, not a job


def test_card_family_never_crosses_into_a_different_card():
    two_headings_one_ancestor = (
        '<html><body><div class="shared-wrapper">'
        '<h3>First real title long enough to pass</h3>'
        '<h3>Second real title also long enough</h3>'
        '<a href="/x">Pročitaj više</a>'
        '</div></body></html>')
    assert board.parse_items(two_headings_one_ancestor, "https://x.invalid/") == []   # ambiguous: 2 headings, never guessed


def test_load_more_button_pagination_regardless_of_its_own_label():
    nxt = board.next_page_url(YOOTHEME_CARD_PAGE, "https://imgge.example.invalid/lat/novosti", 1)
    assert nxt == "https://imgge.example.invalid/lat/novosti?start=12"


@pytest.mark.parametrize("text,expected", [
    ("21 Avgust 2026", date(2026, 8, 21)), ("21. avgusta 2026.", date(2026, 8, 21)),
    ("07 Septembar 2026", date(2026, 9, 7)), ("18 sep 2026", date(2026, 9, 18)),
    ("21 август 2026", date(2026, 8, 21)), ("21. авг 2026.", date(2026, 8, 21)),
    ("31 April 2026", None), ("21 Avgust 2009", None), ("not a date", None),
])
def test_parse_month_name_date(text, expected):
    assert board.parse_month_name_date(text) == expected


def test_looks_like_read_more_vs_a_real_title():
    assert board._looks_like_read_more("Pročitaj više...") and board._looks_like_read_more("Saznajte više")
    assert board._looks_like_read_more("Преузми (PDF)") and board._looks_like_read_more("Read more")
    assert not board._looks_like_read_more("Konkurs za izbor u zvanje docenta za oblast Fizika")


# ---- gf.uns.ac.rs: the whole document link is wrapped in nothing but a bracketed call-to-action ('[Погледајте
# конкурс]', "[See the call]") - a generic label of the same class as 'read more', just a verb this project hadn't
# seen yet. Falls back to the descriptive filename, exactly like every other generic-label document link. ------
def test_pogledajte_konkurs_is_a_generic_label_falls_back_to_filename_title():
    html = ('<html><body><main><article>'
            '<a href="/wp-content/uploads/2026/09/Konkurs-za-upis-u-sk.-2026-2027-OAS-II-produzeni-konkursni-rok.pdf">'
            '[Погледајте конкурс]</a></article></main></body></html>')
    items = board.parse_items(html, "https://gf.example.invalid/oglasna-tabla/")
    assert items[0].title == "Konkurs za upis u sk. 2026 2027 OAS II produzeni konkursni rok"
    ok, kind = board.keep(items[0])
    assert (ok, kind) == (True, "enrollment")


# ---- tfn.uns.ac.rs: a standing fee schedule for the promotion PROCESS's costs states the same 'izbor u zvanje'
# phrase a genuine ad does, but it is a price list, not a call to action and not about a specific candidate either
# (person_specific_procedure doesn't fire on it) - a distinct false-positive class from both existing guards. ----
def test_fee_schedule_document_is_not_an_employment_ad():
    from ajp.extract import rs_text
    title = "Cenovnik troškova izbora u naučna i istraživačka zvanja na TFNS"
    assert rs_text.classify_kind(title) == "other"
    assert not rs_text.academic_ad(title)
    ok, kind = board.keep(board.Item("https://tf.example.invalid/cenovnik.pdf", title, None, title, "document",
                                      "https://tf.example.invalid/izbori-u-zvanja/"))
    assert (ok, kind) == (False, "other")


# ---- akademija.uns.ac.rs: a standing rulebook or a blank application-form TEMPLATE for the promotion process
# ('Pravilnik o blizim uslovima za izbor u zvanje', 'Biografski podaci o kandidatima za izbor u zvanje ...
# (obrazac 1A)') states the same phrase too, but is reusable across every future opening, not one call. -------
@pytest.mark.parametrize("title", [
    "Odluka o primeni Pravilnika i Pravilnik o blizim uslovima za izbor u zvanje nastavnika",
    "Biografski podaci o kandidatima za izbor u zvanje nastavnika – Univerziteta (obrazac 1A NAUKA)",
])
def test_rulebook_and_blank_form_template_are_not_employment_ads(title):
    from ajp.extract import rs_text
    assert rs_text.classify_kind(title) == "other"
    assert rs_text.is_standing_procedure_document(title)


# ---- elfak.ni.ac.rs: a REAL open call's full body routinely cites 'Pravilnika o blizim uslovima...' and asks
# candidates to submit a 'spisak radova' (list of publications) and a filled 'obrazac' - found live, silently
# hiding a genuine open position, when pravilnik/obrazac/spisak were searched for anywhere in arbitrary-length
# text. They must only gate a SHORT, title-shaped string (the actual false positives above, all <150 chars),
# never a multi-paragraph ad body that merely cites one of these words once among many other things. -----------
def test_pravilnik_obrazac_spisak_never_hide_a_real_long_form_ad():
    from ajp.extract import rs_text
    real_ad = (
        "Dekan Elektronskog fakulteta u Nisu objavljuje KONKURS za izbor u zvanje i zasnivanje radnog odnosa sa "
        "punim radnim vremenom jednog nastavnika u zvanje docent za uzu naucnu oblast Teorijska elektrotehnika. "
        "Uslovi za izbor u zvanje docent predvidjeni su clanom 74 Zakona o visokom obrazovanju, clanom 165 "
        "Statuta Univerziteta u Nisu, i odredbama Pravilnika o blizim kriterijumima za izbor u zvanja nastavnika. "
        "Uz prijavu na konkurs kandidati za izbor prilazu: overenu fotokopiju diplome doktora nauka, biografiju, "
        "spisak radova i radove, popunjen, odstampan i potpisan obrazac o ispunjenosti uslova za izbor u zvanje."
    )
    assert len(real_ad) > rs_text._STANDING_DOCUMENT_TITLE_MAX_LEN
    assert not rs_text.is_standing_procedure_document(real_ad)
    assert rs_text.classify_kind(real_ad) == "employment"


# ---- akademija.uns.ac.rs 'Konkursi' page: a static WordPress PAGE's whole body is ALSO one top-level <article>
# (class 'post-126 page type-page'), not a one-item card - it must never collapse a page holding SEVERAL
# unrelated document links onto just the first one found. Family 3 (document) must see every one of them. -----
def test_article_wrapping_multiple_unrelated_documents_is_not_collapsed_to_one():
    html = ('<html><body><main><article class="post-126 page type-page">'
            '<a href="/uploads/2019/10/statut.pdf"></a>'                                      # generic/empty label
            '<a href="/uploads/2019/10/obrazac-1a-nauka.pdf">Biografski podaci o kandidatima za izbor u zvanje nastavnika</a>'
            '<a href="/uploads/2019/10/pravilnik-izbor-u-zvanje.pdf">Pravilnik o blizim uslovima za izbor u zvanje</a>'
            '</article></main></body></html>')
    items = board.parse_items(html, "https://akademija.example.invalid/konkursi/")
    assert [i.family for i in items] == ["document"] * 3        # all three, not just the first document link found
    assert {i.url.rsplit("/", 1)[1] for i in items} == {"statut.pdf", "obrazac-1a-nauka.pdf", "pravilnik-izbor-u-zvanje.pdf"}


# ---- gf.uns.ac.rs: a single unrelated .xlsx ('Raspored nadoknada nastave', a class-makeup timetable, not a
# document extension _DOC_EXT recognised) sits under the same /YYYY/MM/ upload path shape as a real WP post
# permalink - family 2 (wp_permalink) claimed it, produced exactly one (correctly excluded) item, and that alone
# short-circuited the WHOLE cascade before family 3 ever saw the board's ~90 real .pdf announcements. -----------
def test_unrecognised_document_extension_never_masks_the_real_document_family():
    html = ('<html><body><main><article class="post-1 page type-page">'
            '<a href="/wp-content/uploads/2025/08/raspored-nadoknada.xlsx">Raspored nadoknade nastave</a>'
            '<a href="/wp-content/uploads/2026/09/Konkurs-za-upis-u-sk-2026-2027.pdf">Konkurs za upis u sk. 2026 2027 OAS</a>'
            '</article></main></body></html>')
    items = board.parse_items(html, "https://gf.example.invalid/oglasna-tabla/")
    assert [i.family for i in items] == ["document"] * 2
    rows, _ = board.rows_from_items(items, ORG, "k")
    assert {r["url"].rsplit("/", 1)[1] for r in rows} == {"Konkurs-za-upis-u-sk-2026-2027.pdf"}


# ---- tfn.uns.ac.rs: scheduling the DATE of a specific candidate's qualifying lecture names that candidate -
# 'Odluka o utvrdjivanju/promeni termina pristupnog predavanja za kandidat(kinj)u X' - a person-specific notice
# the existing 'referat/izvestaj/pokrenut postupak' markers don't cover (it names no procedure STAGE at all). ---
@pytest.mark.parametrize("title", [
    "Odluka o utvrđivanju termina pristupnog predavanja za kandidatkinju Tanju Lužaić",
    "Одлука о промени термина приступног предавања за кандидата Марка Петровића",
])
def test_qualifying_lecture_scheduling_decision_is_person_specific(title):
    from ajp.extract import rs_text
    assert rs_text.person_specific_procedure(title) == rs_text.EXCLUDED_PERSON_SPECIFIC


# ---- pfu.kg.ac.rs: the qualifying lecture NOTICE itself ('Pristupno(a) predavanje/predavanja u postupku izbora
# u zvanje docent') is always one candidate's own procedural event even when the title never names them - the
# same PM framing already applied at the URL level (hit-vb's '/pristupna-predavanja' sibling path, R7), now also
# caught in free text mixed into a general 'Obavestenja' notices category rather than its own dedicated section.
@pytest.mark.parametrize("title", [
    "Приступна предавања у поступку избора у звање доцент (ужа научна област Педагогија)",
    "Pristupno predavanje",
])
def test_qualifying_lecture_notice_itself_is_person_specific(title):
    from ajp.extract import rs_text
    assert rs_text.person_specific_procedure(title) == rs_text.EXCLUDED_PERSON_SPECIFIC


# ---- tfn.uns.ac.rs: 'Praksa - spisak zakljucenih ugovora' (a roster of ALREADY-signed internship contracts,
# an administrative record) trips the bare 'praksa' internship marker the same way a price list trips
# employment's - it is a standing document about students who already have a placement, not a call for one. ---
def test_contract_roster_document_is_not_an_internship_ad():
    from ajp.extract import rs_text
    title = "Praksa - spisak zaključenih ugovora"
    assert rs_text.classify_kind(title) == "other"
    assert rs_text.is_standing_procedure_document(title)


# ---- polj.uns.ac.rs: a content-rich Drupal homepage with ONE isolated external 'read more'-style link (a
# conference site, nothing to do with the board) tripped the card family and masked 9 real document-family
# candidates (tenders, a staff-notice PDF) elsewhere on the same page - the same lesson as the multi-document
# <article> fix: a family's match must show a genuine repeating LAYOUT before it is trusted over the rest. -----
def test_single_isolated_card_link_does_not_mask_the_document_family():
    html = ('<html><body><main>'
            '<p>Some unrelated conference announcement text. <a href="https://conf.example.invalid/">Pročitaj više</a></p>'
            '<a href="/sites/default/files/upload/Konkurs-za-izbor-docenta.pdf">Konkurs za izbor docenta za oblast Fizika</a>'
            '</main></body></html>')
    items = board.parse_items(html, "https://polj.example.invalid/")
    assert [i.family for i in items] == ["document"]
    assert items[0].title == "Konkurs za izbor docenta za oblast Fizika"


# ---- elfak.ni.ac.rs: 'Konkurs je objavljen u Narodnim novinama' / 'Tekst konkursa je objavljen u ... novinama
# dd.mm.yyyy. godine' names WHERE the ad appeared, not what it's for - the real content is only in the filename.
# The live text mixes scripts within one sentence ('Конкурс je објављен...' - Cyrillic word, Latin 'je'), so the
# fix must work on normalize_title()'s output, not a Cyrillic/Latin pair like the rest of _GENERIC_DOC_LABEL. ----
@pytest.mark.parametrize("anchor_text,filename,expected_title", [
    ("Конкурс je објављен у Народим новинама", "saradnik-u-nastavi-konkurs-09-01-2026.pdf", "saradnik u nastavi konkurs 09 01 2026"),
    ("Текст конкурса је објављен у Нишким народним новинама 02.02.2023. године", "konkurs-02-02-2023-docent.pdf", "konkurs 02 02 2023 docent"),
])
def test_publication_location_notice_falls_back_to_filename_title(anchor_text, filename, expected_title):
    html = f'<html><body><main><article><a href="/x/{filename}">{anchor_text}</a></article></main></body></html>'
    items = board.parse_items(html, "https://elfak.example.invalid/informacije/konkursi-posao")
    assert items[0].title == expected_title


# ---- medf.kg.ac.rs: a VIEWING-INSTRUCTION sentence in whatever word order ('Objavljeni tekst konkursa u
# oglasnim novinama POSLOVI mozete pogledati', 'Kompletan tekst konkursa mozete pogledati.') names neither the
# position nor where it appeared - it just tells the reader they CAN look at it. Falls back to the filename,
# which is where medf's own naming convention ('Konkursi_od_DDMMYYYY_POSLOVI.pdf') actually carries the signal.
@pytest.mark.parametrize("anchor_text", [
    "Objavljeni tekst konkursa u oglasnim novinama POSLOVI mozete pogledati",
    "Kompletan tekst konkursa mozete pogledati.",
])
def test_viewing_instruction_sentence_is_a_generic_label(anchor_text):
    assert board._is_generic_label(anchor_text)


def test_konkurs_and_poslovi_co_occurring_is_an_employment_marker():
    """medf.kg.ac.rs tags its employment-related competitions 'POSLOVI' (jobs) in the filename itself, distinct
    from OTHER (non-job) 'konkurs' documents on the same board - a bare 'posao/poslovi' alone stays untrusted
    (too common/ambiguous a word), only counted together with 'konkurs' nearby."""
    from ajp.extract import rs_text
    assert rs_text.classify_kind("Konkursi od 09092026 POSLOVI") == "employment"
    assert rs_text.classify_kind("Godisnji izvestaj o poslovanju fakulteta") == "other"      # bare 'poslovanju', no konkurs nearby


# ---- filum.kg.ac.rs / fsfv.ni.ac.rs: Joomla's category-blog view wraps each post in a plain <div>, never
# <article> - two different class conventions for the SAME structure ('item column-N' vs 'blog-item'/
# 'com-content-category-blog__item'). The article family's own logic (heading+link or document, in_chrome, a
# good title) now also runs on these containers, not just <article> tags - closes fsfv's disclosed T9 gap too.
def test_joomla_div_item_blog_layout_uses_the_article_familys_logic():
    html = (
        '<html><body><main>'
        '<div class="item column-1"><h2><a href="/vesti/otvorene-pozicije">Otvorene pozicije u FILUM-u za docenta</a></h2>'
        '<p>Fakultet raspisuje konkurs.</p></div>'
        '<div class="blog-item"><h3><a href="/vesti/koncert">Koncert dr um. Aleksandra Ruzicica</a></h3></div>'
        '</main></body></html>')
    items = board.parse_items(html, "https://filum.example.invalid/vesti/")
    assert [i.family for i in items] == ["article", "article"]
    assert items[0].title == "Otvorene pozicije u FILUM-u za docenta"
    assert board.keep(items[0]) == (True, "employment")
    assert board.keep(items[1]) == (False, "other")                          # a concert announcement, not a job


# ---- filum.kg.ac.rs: a Joomla category page often has a SECOND container - 'items-more' (an "more articles"
# sidebar list, no excerpt) - holding posts absent from the main column (a real grant_call mobility notice was
# found live only here). Merged into family 1's results, URL-deduped; its own 'ol.nav.nav-tabs.nav-stacked'
# classes (a Bootstrap tab-list widget) must NOT be mistaken for this project's site-navigation chrome. ---------
def test_items_more_sidebar_list_is_merged_and_its_nav_classes_are_not_chrome():
    html = (
        '<html><body><main>'
        '<div class="item column-1"><h2><a href="/vesti/koncert">Koncert dr um. Aleksandra Ruzicica u sali</a></h2></div>'
        '<div class="items-more"><ol class="nav nav-tabs nav-stacked">'
        '<li><a href="/vesti/mobilnost-nastavnika">Konkursi za mobilnost nastavnog kadra na Univerzitetu</a></li>'
        '</ol></div>'
        '</main></body></html>')
    items = board.parse_items(html, "https://filum.example.invalid/vesti/")
    assert {i.title for i in items} == {"Koncert dr um. Aleksandra Ruzicica u sali", "Konkursi za mobilnost nastavnog kadra na Univerzitetu"}
    urls = {i.url for i in items}
    assert len(urls) == 2                                                    # no accidental collapse/duplication
    mobility = next(i for i in items if "mobilnost" in i.url)
    assert board.keep(mobility) == (True, "grant_call")


# ---- pr.ac.rs: 'Javni poziv za solisticki nastup sa Simfonijskim orkestrom' is a PERFORMER AUDITION (student/
# extracurricular), not an academic grant, despite the same bare 'javni poziv' a real grant call uses. PM ruling
# 2026-09-22: scoped to co-occurrence within the title, so a genuine call using 'nastupno predavanje' (an
# inaugural LECTURE as part of a title-election procedure) is untouched. -----------------------------------------
def test_performance_audition_call_is_not_a_grant_call():
    from ajp.extract import rs_text
    assert rs_text.classify_kind("Јавни позив за солистички наступ са Симфонијским оркестром Народног позоришта") == "other"
    assert rs_text.classify_kind("Konkurs za nastupno predavanje kandidata za izbor u zvanje docenta") == "employment"
