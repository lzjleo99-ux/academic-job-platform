from datetime import datetime, timezone

import pytest

from ajp.connectors import cnrs
from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>emploi.cnrs.fr/Offres.aspx</loc></url>
<url><loc>emploi.cnrs.fr/Offres/CDD/UMR5308-CARLAZ-015/Default.aspx</loc></url>
<url><loc>emploi.cnrs.fr/Offres/CDI/UMR8502-JDUPO-001/Default.aspx</loc></url>
</urlset>"""

DETAIL_INGENIEUR = """<html><body>
<h1>Ingénieur-e bioinformatique en génomique comparative H/F</h1>
<main>
<p>Centre International de Recherche en Infectiologie</p>
<p>Lieu de Travail 69007 LYON</p>
<p>Type de Contrat IT en contrat CDD</p>
<p>Rémuneration A partir de 3175€ brut mensuel selon expérience</p>
<p>Date limite de candidature : mardi 13 octobre 2026 23:59</p>
<p>Description du Poste. Développer des méthodes et pipelines bioinformatiques.</p>
<p>Contact: <a href="mailto:contact@cnrs.invalid">contact@cnrs.invalid</a></p>
</main></body></html>"""

DETAIL_ADMIN = """<html><body>
<h1>Gestionnaire administratif et financier H/F</h1>
<main><p>Lieu de Travail 75005 PARIS</p>
<p>Date limite de candidature : lundi 05 octobre 2026 23:59</p>
<p>Gestion administrative et financiere de l'unite.</p></main></body></html>"""


class Offline(cnrs.CnrsConnector):
    pages = {}
    fetched = []
    fail_urls = set()               # F7 regression: simulate a real per-item fetch failure (e.g. a 502)

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        if url in self.fail_urls:
            raise FetchFailed(f"giving up on {url} after 3 attempts: simulated 502")
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/html", robots_allowed=True)
        Offline.fetched.append(url)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_discover_skips_the_offres_index_page_and_normalizes_scheme_free_locs(tmp_path):
    Offline.pages = {cnrs.SITEMAP_URL: SITEMAP}
    c = Offline(fast(), LocalFsStore(tmp_path))
    urls = c.discover()
    assert urls == [
        "https://emploi.cnrs.fr/Offres/CDD/UMR5308-CARLAZ-015/Default.aspx",
        "https://emploi.cnrs.fr/Offres/CDI/UMR8502-JDUPO-001/Default.aspx",
    ]


def test_parse_detail_extracts_city_and_french_deadline():
    d = cnrs.parse_detail(DETAIL_INGENIEUR)
    assert d["title"] == "Ingénieur-e bioinformatique en génomique comparative H/F"
    assert d["city"] == "LYON"
    assert d["deadline"] == datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)


def test_crawl_keeps_ingenieur_role_discards_pure_admin_role(tmp_path):
    url1 = "https://emploi.cnrs.fr/Offres/CDD/UMR5308-CARLAZ-015/Default.aspx"
    url2 = "https://emploi.cnrs.fr/Offres/CDI/UMR8502-JDUPO-001/Default.aspx"
    Offline.pages = {url1: DETAIL_INGENIEUR, url2: DETAIL_ADMIN}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([url1, url2])
    assert report == {"attempted": 2, "parsed": 2, "kept": 1, "discarded": 1, "fetch_errors": 0}
    kept = next(r for r in rows if r["kind"] != "other")
    assert kept["title_raw"].startswith("Ingénieur") and kept["country_iso2"] == "FR" and kept["city"] == "LYON"
    # T26 (packet 015)
    assert kept["contact_emails"] == ["contact@cnrs.invalid"] and kept["apply_url"] == url1
    discarded = next(r for r in rows if r["kind"] == "other")
    assert "Gestionnaire" in discarded["title_raw"]


def test_a_row_with_a_fixed_country_still_carries_a_real_location_class(tmp_path):
    """T23 (PM ruling R2) completeness fix: cnrs.py's fixed country=FR row previously never set
    location_class at all (only connectors that could discard a null-country row were touched in the
    original R2 sweep) - it silently defaulted to the DB column's own 'unknown' despite always having a
    real, known country. Must be 'located' (or a real remote/hybrid signal), never left as an omission."""
    url = "https://emploi.cnrs.fr/Offres/CDD/UMR5308-CARLAZ-015/Default.aspx"
    Offline.pages = {url: DETAIL_INGENIEUR}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, _ = c.crawl([url])
    assert rows[0]["location_class"] == "located"


# ---------------------------------------------------------------- F7 (PM live audit 2026-09-24) -----------------
def test_one_detail_url_failing_after_retries_never_loses_its_sibling_rows(tmp_path):
    ok_url = "https://emploi.cnrs.fr/Offres/CDD/UMR5308-CARLAZ-015/Default.aspx"
    bad_url = "https://emploi.cnrs.fr/Offres/CDI/UMR9999-XXXXX-001/Default.aspx"
    Offline.pages = {ok_url: DETAIL_INGENIEUR}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    rows, report = c.crawl([ok_url, bad_url])
    assert len(rows) == 1 and rows[0]["country_iso2"] == "FR"
    assert report["fetch_errors"] == 1 and report["attempted"] == 2
    Offline.fail_urls = set()


def test_every_detail_url_failing_raises_so_the_scheduler_still_sees_a_real_outage(tmp_path):
    bad_url = "https://emploi.cnrs.fr/Offres/CDD/X/Default.aspx"
    Offline.pages = {}
    Offline.fail_urls = {bad_url}
    c = Offline(fast(), LocalFsStore(tmp_path))
    with pytest.raises(FetchFailed):
        c.crawl([bad_url])
    Offline.fail_urls = set()
