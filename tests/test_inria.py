"""T19 acceptance: Inria with recorded fixtures (trimmed real samples from jobs.inria.fr, captured
2026-09-23)."""
from datetime import datetime, timezone

from ajp.connectors import inria
from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

LIST_HTML = """<html><body>
<a href="/public/classic/en/offres/2026-10501">PhD Position F/M Visualization</a>
<a href="/public/classic/en/offres/2026-10501">PhD Position F/M Visualization</a>
<a href="/public/classic/en/offres/2026-09802">Juriste propriété intellectuelle et contrats</a>
<a href="/public/classic/en/offres/map">Site map</a>
</body></html>"""

# Trimmed real detail page shape - genuinely bilingual research ad (no lang="fr" on the h1).
DETAIL_PHD = """<html><body>
<h1 class="h1-detail">PhD Position F/M Visualization of the Plausibility and Bias for Data Resources</h1>
<div class="grand-item-offre">
<div class="item-offre"><p class="list-group-item-text"><b>Contract type : </b>Fixed-term contract</p></div>
</div>
<div class="grand-item-offre">
<h2 class="h2-detail">About the research centre</h2>
<div class="list-group-item-text">The Inria Saclay research centre works on visualization and data science.</div>
</div>
<div class="red-test">
<h2 class="inria-red">General Information</h2>
<ul class="nomargin nostyle">
<li><strong>Theme/Domain :</strong>Interaction and visualization</li>
<li><strong>Town/city :</strong>GIF SUY YVETTE</li>
<li><strong>Inria Center :</strong><a href="http://www.inria.fr/centre/saclay">Centre Inria de Saclay</a></li>
<li><strong>Starting date :</strong>2027-10-01</li>
<li><strong>Duration of contract :</strong>3 years</li>
<li><strong>Deadline to apply :</strong>2027-03-28</li>
</ul>
</div>
<p>Contact: <a href="mailto:phd-recruitment@inria.invalid">phd-recruitment@inria.invalid</a></p>
</body></html>"""

# Trimmed real detail page shape - a French-ONLY admin ad (h1 carries lang="fr", a real live-verified marker).
DETAIL_FR_ONLY = """<html><body>
<h1 class="h1-detail" lang="fr">Juriste propriété intellectuelle et contrats</h1>
<div class="grand-item-offre">
<div class="item-offre"><p class="list-group-item-text"><b>Contract type : </b>Fixed-term contract</p></div>
</div>
<div class="grand-item-offre">
<h2 class="h2-detail">Context</h2>
<div class="list-group-item-text">La Direction des affaires juridiques recrute un juriste pour ses contrats.</div>
</div>
<div class="red-test">
<ul class="nomargin nostyle">
<li><strong>Town/city :</strong>Paris</li>
<li><strong>Deadline to apply :</strong>2026-10-15</li>
</ul>
</div>
</body></html>"""


class Offline(inria.InriaConnector):
    pages = {}

    def fetch_raw(self, url, kind="html", max_bytes=None):
        if not self.manifest.permits(url):
            raise ManifestViolation(url)
        body = self.pages[url].encode()
        res = FetchResult(url=url, final_url=url, status=200, content=body, content_type="text/html", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=body, fetched_at=NOW,
                      status=200, content_type="text/html", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_parse_list_deduplicates_repeated_links_and_skips_the_non_posting_map_page():
    """A real, live-found utility link (/offres/map, a site map) has no digit in its id segment - never a
    real posting, which are all 'YYYY-NNNNN' - and must not be discovered as a fake row."""
    links = inria.parse_list(LIST_HTML)
    assert links == [
        "https://jobs.inria.fr/public/classic/en/offres/2026-10501",
        "https://jobs.inria.fr/public/classic/en/offres/2026-09802",
    ]


def test_parse_detail_resolves_the_structured_general_information_block():
    d = inria.parse_detail(DETAIL_PHD)
    assert d["title"] == "PhD Position F/M Visualization of the Plausibility and Bias for Data Resources"
    assert d["city"] == "GIF SUY YVETTE"                              # kept verbatim - the org's own raw value
    assert d["centre"] == "Centre Inria de Saclay"
    assert d["contract"] == "fixed"                                     # mapped to listing.contract's own enum
    assert d["contract_raw"] == "Fixed-term contract"                   # the page's own text, kept alongside
    assert d["deadline"] == datetime(2027, 3, 28, tzinfo=timezone.utc)
    assert d["ad_lang"] == "en"                                        # no lang="fr" on this ad's own h1
    assert "visualization and data science" in d["body_text"]


def test_contract_type_is_mapped_to_listings_own_controlled_enum_not_passed_through_as_free_text():
    """T21, found live (the first real ajp-run --once against a real seed): the page's own free-text
    'Contract type' value was passed straight through to listing.contract, whose CHECK enum
    (fixed/permanent/grant/fellowship/phd_enrolment/service_contract) rejected 'Fixed-term contract'
    outright with a real CheckViolation on ingest - never caught by T19's own connector-level-only testing,
    which never ran a row through upsert_listing at all."""
    assert inria._map_contract("Fixed-term contract") == "fixed"
    assert inria._map_contract("Permanent position") == "permanent"
    assert inria._map_contract("Some future phrasing this connector has never seen") is None    # never guessed
    assert inria._map_contract(None) is None


def test_parse_detail_detects_a_french_only_ad_via_its_own_h1_lang_attribute():
    d = inria.parse_detail(DETAIL_FR_ONLY)
    assert d["ad_lang"] == "fr"
    assert d["city"] == "Paris"


def test_crawl_discards_the_admin_role_and_keeps_the_phd_position(tmp_path):
    Offline.pages = {
        inria.LIST_URL: LIST_HTML,
        "https://jobs.inria.fr/public/classic/en/offres/2026-10501": DETAIL_PHD,
        "https://jobs.inria.fr/public/classic/en/offres/2026-09802": DETAIL_FR_ONLY,
    }
    c = Offline(fast(), LocalFsStore(tmp_path))
    links = c.discover()
    rows, report = c.crawl(links)
    assert report["attempted"] == 2 and report["parsed"] == 2 and report["kept"] == 1 and report["discarded"] == 1
    kept = rows[0]
    assert kept["kind"] == "employment" and kept["position_kind"] == "phd"
    assert kept["country_iso2"] == "FR" and kept["city"] == "GIF SUY YVETTE"
    assert kept["location_class"] == "located"                                          # T23 (PM ruling R2)
    assert kept["contact_emails"] == ["phd-recruitment@inria.invalid"]                  # T26 (packet 015)
    assert kept["apply_url"] == "https://jobs.inria.fr/public/classic/en/offres/2026-10501"
    assert kept["ext_uid"] == "2026-10501"
    assert kept["ad_lang"] == "en" and kept["org_raw"] == inria.ORG_RAW


def test_manifest_permits_both_locale_prefixes_but_not_www_inria_fr(tmp_path):
    c = inria.InriaConnector(fast(), LocalFsStore(tmp_path))
    assert c.manifest.permits(inria.LIST_URL)
    assert c.manifest.permits("https://jobs.inria.fr/public/classic/en/offres/2026-10501")
    assert c.manifest.permits("https://jobs.inria.fr/public/classic/fr/offres/2026-10501")
    assert not c.manifest.permits("https://www.inria.fr/en/some-page")            # excluded per the addenda
