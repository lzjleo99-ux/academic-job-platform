"""NSZ parsers and connector, exercised on SYNTHETIC fixtures only."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ajp.connectors import nsz
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.extract import rs_text
from ajp.ingest import upsert_listing
from ajp.rawstore import LocalFsStore, put_raw

FX = Path(__file__).parent / "fixtures" / "synthetic"
LISTING = (FX / "nsz_listing_synthetic.html").read_text()
ACAD = (FX / "nsz_detail_synthetic_academic.html").read_text()
HOSP = (FX / "nsz_detail_synthetic_hospital.html").read_text()
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- listing ---------------
def test_listing_cards_parse_and_reject_foreign_links():
    cards = nsz.parse_listing(LISTING, "https://www.nsz.gov.rs/sadrzaji/poslovi/279")
    assert [c.ext_id for c in cards] == ["90001", "90002"]                 # off-site link dropped
    assert cards[0].title == "ПРИМЕР ФАКУЛТЕТ" and cards[0].posted == date(2026, 9, 21)
    assert cards[1].url == "https://www.nsz.gov.rs/sadrzaj/bolnica-primer/90002"   # relative href resolved
    assert nsz.last_page(LISTING) == 3 and nsz.last_page("<html></html>") == 1
    assert nsz.parse_date("99.99.2026.") is None


# ---------------------------------------------------------------- detail ----------------
def test_detail_splits_ads_and_reads_employer_date_address():
    d = nsz.parse_detail(ACAD)
    assert d.employer == "ПРИМЕР ФАКУЛТЕТ" and d.posted == date(2026, 9, 21) and d.address == "21000 Нови Сад, Примерова 1"
    assert [a.position for a in d.ads] == ["Асистент за ужу научну област Примерна физика", "Истраживач-сарадник за област Примерна хемија"]
    assert d.ads[0].conditions.startswith("УСЛОВИ:")
    assert nsz.city_from_address(d.address) == "Нови Сад"


def test_rows_carry_only_stated_facts_with_raw_excerpts():
    card = nsz.Card("https://www.nsz.gov.rs/sadrzaj/fakultet-primer/90001", "90001", "ПРИМЕР ФАКУЛТЕТ", "Послови", date(2026, 9, 21), "")
    rows = nsz.rows_from_detail(card, nsz.parse_detail(ACAD), "raw/key", now=NOW, html=ACAD)
    a, b = rows
    assert (a["ext_uid"], b["ext_uid"]) == ("90001-1", "90001-2")
    assert a["position_kind"] == "lecturer" and a["kind"] == "employment" and a["ad_lang"] == "sr-Cyrl"
    assert a["degree_required"] == "phd" and a["contract"] == "fixed"
    assert (a["duration_months_v"], a["duration_months_raw"]) == (12, "do 12 meseci")
    assert a["city"] == "Нови Сад" and a["country_iso2"] == "RS" and a["foreigner_eligible"] == "unknown"
    assert a["deadline"] == datetime(2026, 10, 6, 21, 59, 59, tzinfo=timezone.utc) and a["status"] == "open"   # 21.09 + 15 d, 23:59:59 CEST
    assert b["status"] == "open"                                                # 29.09 deadline is still ahead of NOW (22.09)
    assert b["deadline"] == datetime(2026, 9, 29, 21, 59, 59, tzinfo=timezone.utc)
    assert b["contract"] == "permanent" and b["degree_required"] == "msc" and "duration_months_v" not in b
    assert (b["position_kind"], b["rs_research_title"]) == ("researcher_title", "istrazivac_saradnik")   # RS title, hyphen form
    for r in rows:
        assert not any(k.startswith("salary") for k in r)                      # nothing stated -> nothing stored
        assert r["raw_text"] and r["requirements_raw"].startswith("УСЛОВИ")
        # T26 (packet 015): the SAME employer-page contact is shared by both ads on this page (a single
        # detail page carries the mailto: link once, not per-ad)
        assert r["contact_emails"] == ["kadrovska@primer-fakultet.invalid"]
        assert r["apply_url"] == card.url


# ---- T35b (packet 021, PM-verified defect, 12 real open rows): a formal ministry "ЈАВНИ КОНКУРС" notice's
# own duties paragraph ('Опис послова: ...') got mistaken for the position's own short title - never a real
# short title at all, whatever its own individual characters look like. ---------------------------------
def test_f10_an_oversized_duties_paragraph_falls_back_to_its_own_first_sentence_never_the_whole_wall():
    long_duties = ("Опис послова: Припрема делове стратешких докумената у процесу приступања Европској "
                  "унији у области транспортне политике. Пружа стручна упутства и консултације органима "
                  "државне управе у току преговора о чланству Србије у Европској унији. Прати реформе.")
    assert len(long_duties) > 200
    card = nsz.Card("https://www.nsz.gov.rs/sadrzaj/ministarstvo/89620", "89620", "МИНИСТАРСТВО", "Послови", date(2026, 9, 21), "")
    det = nsz.Detail(employer="МИНИСТАРСТВО ЗА ЕВРОПСКЕ ИНТЕГРАЦИЈЕ", posted=date(2026, 9, 21), address=None,
                     ads=[nsz.Ad(position=long_duties, body=long_duties, conditions="УСЛОВИ: ...")])
    row = nsz.rows_from_detail(card, det, "raw/key", now=NOW)[0]
    assert len(row["title_raw"]) < 200
    assert row["title_raw"] == "Опис послова: Припрема делове стратешких докумената у процесу приступања Европској унији у области транспортне политике"
    assert row["raw_text"] == f"{long_duties} УСЛОВИ: ..."   # raw_text (full) is never shortened, only title_raw


def test_f10_an_oversized_title_with_no_short_first_sentence_either_falls_back_to_the_employer_name():
    """No period at all across the whole thing (a real risk for a badly-formatted notice) - the generic,
    honest employer-named fallback, never a truncated mid-word wall of text."""
    long_no_periods = "Опис послова " * 40   # well over 200 chars, zero sentence boundaries
    assert len(long_no_periods) > 200
    card = nsz.Card("https://www.nsz.gov.rs/sadrzaj/ministarstvo/1", "1", "М", "Послови", None, "")
    det = nsz.Detail(employer="МИНИСТАРСТВО ПРИМЕР", posted=None, address=None,
                     ads=[nsz.Ad(position=long_no_periods, body=long_no_periods, conditions="УСЛОВИ: ...")])
    row = nsz.rows_from_detail(card, det, "raw/key", now=NOW)[0]
    assert row["title_raw"] == "Positions announced by МИНИСТАРСТВО ПРИМЕР"


def test_f10_a_normal_short_position_title_is_never_touched_by_the_oversized_guard():
    card = nsz.Card("https://www.nsz.gov.rs/sadrzaj/fakultet-primer/90001", "90001", "ПРИМЕР ФАКУЛТЕТ", "Послови", date(2026, 9, 21), "")
    rows = nsz.rows_from_detail(card, nsz.parse_detail(ACAD), "raw/key", now=NOW, html=ACAD)
    assert rows[0]["title_raw"] == "Асистент за ужу научну област Примерна физика"


def test_passed_deadline_marks_expired_but_keeps_row():
    card = nsz.Card("https://www.nsz.gov.rs/sadrzaj/x/1", "1", "T", "Послови", date(2026, 9, 21), "")
    rows = nsz.rows_from_detail(card, nsz.parse_detail(ACAD), "k", now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert [r["status"] for r in rows] == ["expired", "expired"]


def test_hospital_ad_is_not_academic_and_doctor_of_medicine_is_not_a_phd():
    d = nsz.parse_detail(HOSP)
    text = " ".join(a.body + " " + a.conditions for a in d.ads)
    assert not rs_text.is_academic(d.ads[0].body)                 # 'medicinski fakultet' appears only in conditions
    assert rs_text.is_academic(text)                              # ...and DOES trip the keyword filter: caller must use the position text
    row = nsz.rows_from_detail(nsz.Card("https://www.nsz.gov.rs/sadrzaj/b/2", "2", "B", "Послови", None, ""), d, "k", now=NOW)[0]
    assert row["kind"] == "other"                                 # a job, but not an academic one: hidden from the default view
    assert "degree_required" not in row and row["duration_months_v"] == 6 and row["duration_months_conf"] == 0.6   # 'do 6' = upper bound


# ---------------------------------------------------------------- rs_text -----------------
@pytest.mark.parametrize("text,kind", [
    ("Конкурс за упис студената у прву годину", "enrollment"), ("Konkurs za upis na master studije", "enrollment"),
    ("Јавни позив за доделу стипендија", "grant_call"), ("Stručna praksa za studente", "internship"),
    ("Konkurs za izbor u zvanje docenta", "employment"), ("Referat комисије за избор у звање", "employment"),   # kind alone; excluded upstream by person_specific_procedure
    ("Uvid javnosti", "other"), ("Konkurs za radno mesto asistenta", "employment"),
    ("Обавештење о раду библиотеке", "other"), ("Demonstrator za vežbe", "internship"),
])
def test_kind_rules_incl_packet_004_005_taxonomy(text, kind):
    assert rs_text.classify_kind(text) == kind


def test_cyrillic_and_latin_give_identical_extraction():
    cyr, lat = "Виши научни сарадник, на одређено до 24 месеца", "Viši naučni saradnik, na određeno do 24 meseca"
    assert rs_text.position_kind(cyr) == rs_text.position_kind(lat) == ("researcher_title", "visi_naucni_saradnik")
    assert rs_text.duration_months(cyr) == rs_text.duration_months(lat)
    assert rs_text.script_of(cyr) == "sr-Cyrl" and rs_text.script_of(lat) == "sr-Latn" and rs_text.script_of("123") == "und"


@pytest.mark.parametrize("title,expect", [
    ("Редовни професор за област X", ("professor", None)), ("Docent", ("professor", None)),
    ("Saradnik u nastavi", ("lecturer", None)), ("Istraživač-pripravnik", ("researcher_title", "istrazivac_pripravnik")),
    ("Naučni savetnik", ("researcher_title", "naucni_savetnik")), ("Naučni saradnik", ("researcher_title", "naucni_saradnik")),
    ("Laborant", ("technician", None)), ("Kuvar", ("other", None)),
])
def test_position_kind(title, expect):
    assert rs_text.position_kind(title) == expect


@pytest.mark.parametrize("text,days", [
    ("Рок за подношење пријава је 15 дана и почиње да тече наредног дана од дана објављивања јавног конкурса у листу Послови", 15),
    ("рок за подношење пријаве је 15 дана и почиње да тече наредног дана од дана оглашавања конкурса", 15),
    ("Пријаве доставити на адресу, канцеларија 121, у року од 15 дана од дана објављивања.", 15),
    ("Рок за подношење пријава је 8 дана од дана објављивања.", 8),
    # look-alikes that are NOT the application deadline:
    ("Подносиоци пријаве се обавештавају о шифри у року од три дана од дана пријема пријаве", None),
    ("кандидатима у року од 5 (пет) радних дана од дана пријема обавештења доставе доказе", None),
    ("сматра се извршеном истеком 15 дана од дана објављивања решења на веб презентацији", None),
    ("приговор у року од 8 дана од дана објављивања решења", None),
])
def test_deadline_rule_variants_from_real_wording_and_lookalikes(text, days):
    got = rs_text.deadline_days(text)
    assert (got["days"] if got else None) == days


def test_deadline_and_duration_need_explicit_statements():
    assert rs_text.deadline_days("Рок за подношење пријаве је 30 дана од дана објављивања")["days"] == 30
    assert rs_text.deadline_days("Rok je do kraja meseca") is None
    assert rs_text.duration_months("na period od 2 godine")["v"] == 24
    assert rs_text.duration_months("на одређено време од 3 године")["v"] == 36
    assert rs_text.duration_months("stalni radni odnos") is None


# ---------------------------------------------------------------- manifest ---------------
def test_manifest_allows_only_the_known_board_urls():
    ok = ["https://www.nsz.gov.rs/sadrzaji/poslovi/279", "https://www.nsz.gov.rs/sadrzaji/poslovi/279?page=12",
          "https://www.nsz.gov.rs/sadrzaji/ostali-konkursi/277", "https://www.nsz.gov.rs/sadrzaj/fakultet-primer/90001",
          "https://www.nsz.gov.rs/sadrzaj/pdf-arhiva-poslova/4108",
          "https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/2026/Poslovi%2C%201213.pdf"]
    bad = ["https://www.nsz.gov.rs/sadrzaji/poslovi/279?page=x", "https://www.nsz.gov.rs/sadrzaji/poslovi/279?q=1",
           "https://www.nsz.gov.rs/admin/", "https://evil.example/sadrzaj/x/1", "https://www.nsz.gov.rs/nsz/oglasi-sa-portala-infostud/9843",
           "https://www.nsz.gov.rs/filemanager/Files/Dokumenta/x.xls",                       # other files: not in scope
           "https://www.nsz.gov.rs/filemanager/Files/PDF_Arhiva_poslova/2026/a.pdf?x=1"]     # no query strings
    assert all(nsz.MANIFEST.permits(u) for u in ok) and not any(nsz.MANIFEST.permits(u) for u in bad)


# ---------------------------------------------------------------- connector flow ---------
class Offline(nsz.NszConnector):
    """Serves fixtures through the real fetch_raw storage path (envelope included) without network."""
    pages = {}

    def fetch_raw(self, url, kind="html"):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/html", robots_allowed=True)
        Offline.fetched.append(url)
        return res, ref
    fetched = []


def test_crawl_section_to_db_end_to_end(conn, tmp_path):
    Offline.fetched = []
    Offline.pages = {"https://www.nsz.gov.rs/sadrzaji/poslovi/279": LISTING,
                     "https://www.nsz.gov.rs/sadrzaj/fakultet-primer/90001": ACAD,
                     "https://www.nsz.gov.rs/sadrzaj/bolnica-primer/90002": HOSP}
    store = LocalFsStore(tmp_path)
    c = Offline(PoliteClient(rps=1000), store)
    c.ensure_source(conn); c.ensure_source(conn)
    rows = list(c.crawl_section("poslovi", max_pages=1))
    assert len(rows) == 3 and Offline.fetched == list(Offline.pages)          # list page + exactly the 2 valid cards
    for r in rows:
        assert upsert_listing(conn, dict(r, kind=r["kind"]), store)[1] is True
    assert dict(conn.execute("SELECT kind, count(*) FROM listing GROUP BY 1").fetchall()) == {"employment": 2, "other": 1}
    assert conn.execute("SELECT count(*) FROM listing WHERE salary_min IS NOT NULL").fetchone()[0] == 0
    assert list(c.crawl_section("poslovi", max_pages=1, max_details=1)) and Offline.fetched[-2:] == [
        "https://www.nsz.gov.rs/sadrzaji/poslovi/279", "https://www.nsz.gov.rs/sadrzaj/fakultet-primer/90001"]


@pytest.mark.parametrize("position,expect", [
    ("Асистент за ужу научну област Физика", True), ("Доцент", True), ("Виши научни сарадник", True), ("Истраживач-приправник", True),
    ("Сарадник у настави", True), ("Редован професор", True), ("Postdoc istraživač", True),
    ("Кувар", False), ("Чистачица", False), ("Доктор медицине", False), ("Референт за финансије", False),
    ("Педагошки асистент на одређено време", False), ("Педагошки (ромски) асистент", False), ("Асистент за Роме", False),
    ("Здравствени асистент", False), ("Наставник предметне наставе", False),
])
def test_academic_position_gate_ignores_employer_and_school_support_roles(position, expect):
    assert rs_text.academic_ad(position) is expect


@pytest.mark.parametrize("employer,position,expect", [
    ("ОСНОВНА ШКОЛА „ХЕРОЈ РАДМИЛА ШИШКОВИЋ”", "Андрагошки асистент", False), ("ОШ „ЈУНИОР”", "Асистент у настави - особа са инвалидитетом", False),
    ("СРЕДЊА СТРУЧНА ШКОЛА", "Педагошки асистент", False), ("ГИМНАЗИЈА ПРИМЕР", "Асистент", False), ("Предшколска установа Пример", "Асистент", False),
    ("ПРИРОДНО-МАТЕМАТИЧКИ ФАКУЛТЕТ", "Асистент за научну област Математика", True), ("АКАДЕМИЈА СТРУКОВНИХ СТУДИЈА", "Сарадник у настави", True),
    ("ВИША ТЕХНИЧКА ШКОЛА", "Предавач", True),                       # 'viša škola' is higher education
])
def test_k12_employers_are_never_academic(employer, position, expect):
    assert rs_text.academic_ad(position, employer) is expect


def test_enrolled_in_phd_studies_is_not_a_phd_degree():
    assert rs_text.degree_required("УСЛОВИ: студент докторских студија; уписан на докторске студије") is None
    assert rs_text.degree_required("УСЛОВИ: научни степен доктора наука из области физике") == "phd"
    assert rs_text.degree_required("УСЛОВИ: докторат из области физике") == "phd"
