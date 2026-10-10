"""T21 acceptance: 'factory test - every registered non-deeplink source row builds the expected connector;
deeplink rows return a labelled no-op'."""
import pytest

from ajp import scheduler
from ajp.connectors.academicpositions import AcademicPositionsConnector
from ajp.connectors.academictransfer import AcademicTransferConnector
from ajp.connectors.ats_greenhouse import GreenhouseConnector
from ajp.connectors.ats_lever import LeverConnector
from ajp.connectors.ats_workday import WorkdayCxsConnector
from ajp.connectors.base import PoliteClient
from ajp.connectors.cern import CernConnector
from ajp.connectors.cnrs import CnrsConnector
from ajp.connectors.gen_sitemap_html import SitemapHtmlConnector
from ajp.connectors.gen_wordpress import WordPressConnector
from ajp.connectors.inria import InriaConnector
from ajp.connectors.jobs_ac_uk import JobsAcUkConnector
from ajp.connectors.kth import KthConnector
from ajp.connectors.ku_dk import KuDkConnector
from ajp.connectors.nature_careers import NatureCareersConnector
from ajp.connectors.sljaka import SljakaConnector
from ajp.rawstore import LocalFsStore


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def base_row(**kw):
    row = {"id": "x", "kind": "feed", "ats_family": None, "ats_tenant": None, "ats_site": None, "config": {}}
    row.update(kw)
    return row


@pytest.mark.parametrize("source_id,cls", [
    ("academicpositions", AcademicPositionsConnector), ("academictransfer", AcademicTransferConnector),
    ("cern", CernConnector), ("cnrs", CnrsConnector), ("inria", InriaConnector),
    ("jobs_ac_uk", JobsAcUkConnector), ("kth", KthConnector), ("ku_dk", KuDkConnector),
    ("nature_careers", NatureCareersConnector), ("sljaka", SljakaConnector),
])
def test_every_bespoke_source_builds_its_own_known_connector_class(tmp_path, source_id, cls):
    row = base_row(id=source_id, kind="feed")
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, cls)
    assert connector.source_id == source_id


def test_workday_cxs_row_builds_a_real_workday_connector_with_the_stored_tenant_site(tmp_path):
    row = base_row(id="ats_workday_cxs__embl_embl", kind="api", ats_family="ats_workday_cxs",
                   ats_tenant="embl", ats_site="EMBL", config={"wd_host": "wd103"})
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, WorkdayCxsConnector)
    assert connector.site.tenant == "embl" and connector.site.wd_host == "wd103" and connector.site.site == "EMBL"


def test_lever_row_builds_with_the_stored_tenant(tmp_path):
    row = base_row(id="ats_lever__leverdemo", kind="api", ats_family="ats_lever", ats_tenant="leverdemo")
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, LeverConnector)
    assert connector.tenant == "leverdemo"


def test_greenhouse_row_builds_with_the_stored_tenant(tmp_path):
    row = base_row(id="ats_greenhouse__databricks", kind="api", ats_family="ats_greenhouse", ats_tenant="databricks")
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, GreenhouseConnector)
    assert connector.tenant == "databricks"


def test_gen_wordpress_row_reconstructs_its_config_including_tuple_fields_from_json_lists(tmp_path):
    row = base_row(id="gen_wordpress__pefja", kind="feed", ats_family="gen_wordpress",
                   config={"source_id": "gen_wordpress__pefja", "host": "pefja.kg.ac.rs", "classifier": "rs",
                          "fixed_country": "RS", "feed_paths": ["/feed/", "/?feed=rss2"]})
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, WordPressConnector)
    assert connector.config.host == "pefja.kg.ac.rs"
    assert connector.config.feed_paths == ("/feed/", "/?feed=rss2")     # coerced back from a JSON list


def test_gen_wordpress_config_drops_an_unknown_key_rather_than_raising(tmp_path):
    """Schema drift: a config written by an older version of the dataclass may carry a field the current
    class no longer has - dropped, not fatal, so an old row still constructs today."""
    row = base_row(id="gen_wordpress__x", kind="feed", ats_family="gen_wordpress",
                   config={"source_id": "gen_wordpress__x", "host": "x.invalid", "a_field_removed_since": "old"})
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, WordPressConnector)


def test_gen_sitemap_html_row_reconstructs_its_config(tmp_path):
    row = base_row(id="gen_sitemap_html__theunijobs", kind="scrape_html", ats_family="gen_sitemap_html",
                   config={"source_id": "gen_sitemap_html__theunijobs", "host": "www.timeshighereducation.com",
                          "sitemapindex_url": "https://www.timeshighereducation.com/unijobs/sitemapindex.xml",
                          "detail_url_pattern": r"/unijobs/listing/\d+/", "detail_mode": "jsonld"})
    connector = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(connector, SitemapHtmlConnector)
    assert connector.config.detail_mode == "jsonld"


def test_a_channel_source_returns_a_labelled_noop_never_a_connector(tmp_path):
    row = base_row(id="euraxess", kind="channel")
    result = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(result, scheduler.NoOpConnector)
    assert "deeplink" in result.reason


def test_rs_org_boards_returns_a_labelled_noop_not_a_connector_instance(tmp_path):
    """rs_org_boards is org-fan-out (board.crawl_orgs()), not a single per-source connector - the factory
    must never try to instantiate BoardConnector with no org argument."""
    row = base_row(id="rs_org_boards", kind="scrape_html")
    result = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(result, scheduler.NoOpConnector)
    assert "org-fan-out" in result.reason


def test_nsz_returns_the_bespoke_scheduler_adapter_not_a_noop(tmp_path):
    """F8 (PM live audit 2026-09-24): nsz.py exposes crawl_section()/crawl_pdf_issue()/crawl_pdf_backfill() -
    a bespoke multi-method API, not the discover()+crawl(items) shape every other bespoke connector follows -
    so a real adapter (NszScheduledConnector, a bare crawl() with no args) drives it instead of a NoOp."""
    from ajp.connectors.nsz import NszScheduledConnector

    row = base_row(id="nsz", kind="scrape_html")
    result = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(result, NszScheduledConnector)
    assert not isinstance(result, scheduler.NoOpConnector)


def test_a_detection_only_ats_family_returns_a_labelled_noop(tmp_path):
    row = base_row(id="ats_smartrecruiters__someorg", kind="api", ats_family="ats_smartrecruiters")
    result = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(result, scheduler.NoOpConnector)
    assert "detection-only" in result.reason


def test_an_unrecognised_source_shape_returns_a_labelled_noop_never_raises(tmp_path):
    row = base_row(id="something_new", kind="feed", ats_family=None)
    result = scheduler.build_connector(row, fast(), LocalFsStore(tmp_path))
    assert isinstance(result, scheduler.NoOpConnector)
    assert "unrecognised" in result.reason
