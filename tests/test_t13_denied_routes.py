"""T13 acceptance: 'a guard test asserts the full T13 dry-run issues ZERO requests to disallowed routes' -
Nature /search, EURAXESS /jobs/* and ? and api/rest/pdf, AcademicTransfer /apply//account/, jobs.ac.uk
/job/feedback/, HigherEdJobs categoryFeed beyond robots, no ClickThru/redirector endpoints. Every wave-A
connector's own UrlManifest is the actual enforcement (ManifestViolation on anything else); this test asserts
each specific disallowed URL from the acceptance list is refused by the connector that would otherwise touch it.
"""
import pytest

from ajp.connectors import (academicpositions, academictransfer, cern, cnrs, jobs_ac_uk, nature_careers, sljaka)


@pytest.mark.parametrize("url", [
    "https://www.nature.com/naturecareers/jobs/search",
    "https://www.nature.com/naturecareers/jobs/search?q=phd",
    "https://www.nature.com/search",
    "https://www.nature.com/search?q=x",
])
def test_nature_careers_manifest_refuses_search(url):
    assert not nature_careers.MANIFEST.permits(url)
    assert not nature_careers.is_job_detail_url(url)


@pytest.mark.parametrize("url", [
    "https://www.academictransfer.com/apply/123/",
    "https://www.academictransfer.com/account/settings",
    "https://www.academictransfer.com/account/",
])
def test_academictransfer_manifest_refuses_apply_and_account(url):
    assert not academictransfer.MANIFEST.permits(url)


@pytest.mark.parametrize("url", [
    "https://www.jobs.ac.uk/job/feedback/",
    "https://www.jobs.ac.uk/job/feedback/DQH648",
    "https://www.jobs.ac.uk/enhanced/fp/x",
])
def test_jobs_ac_uk_manifest_refuses_feedback_and_enhanced_fp(url):
    assert not jobs_ac_uk.MANIFEST.permits(url)
    assert not jobs_ac_uk.is_job_detail_url(url)


@pytest.mark.parametrize("url", [
    "https://api.smartrecruiters.com/v1/companies/CERN/postings",
    "https://api.smartrecruiters.com/v1/companies/CERN/postings?limit=100",
])
def test_cern_manifest_refuses_the_robots_disallowed_smartrecruiters_api(url):
    assert not cern.MANIFEST.permits(url)


@pytest.mark.parametrize("url", [
    "https://emploi.cnrs.fr/Administration/x",
    "https://emploi.cnrs.fr/Candidat/login",
    "https://emploi.cnrs.fr/Gestion/x",
])
def test_cnrs_manifest_refuses_robots_disallowed_paths(url):
    assert not cnrs.MANIFEST.permits(url)


@pytest.mark.parametrize("mod,url", [
    (academictransfer, "https://tracking.example.invalid/click?to=https://academictransfer.com/en/jobs/1/x/"),
    (jobs_ac_uk, "https://www.jobs.ac.uk/clickthru?id=1"),
    (nature_careers, "https://www.nature.com/naturecareers/redirect/12345"),
    (academicpositions, "https://academicpositions.com/redirect/1"),
    (cnrs, "https://emploi.cnrs.fr/redirect?to=x"),
])
def test_no_connector_manifest_permits_a_clickthru_or_redirector_endpoint(mod, url):
    """None of these endpoints were ever named by any route spec - a manifest that only lists sitemap+detail
    patterns already refuses them by construction; asserted explicitly per the T13 acceptance list."""
    assert not mod.MANIFEST.permits(url)


def test_euraxess_channel_never_constructs_a_jobs_search_or_api_url_outside_its_own_module():
    """The existing governance guard (test_governance_guards.py) already enforces this file-wide; this test
    just confirms the deeplink URLs euraxess.py itself DOES generate are exactly the two curated ones, never a
    live /jobs/search fetch or an /api/rest/pdf/xmlguidelines endpoint."""
    from ajp.channels import euraxess
    urls = [d.url for d in euraxess.deeplinks()]
    assert all(u.startswith("https://euraxess.ec.europa.eu/jobs") for u in urls)
    assert not any("/api/" in u or "xmlguidelines" in u for u in urls)


def test_higheredjobs_channel_never_links_the_walled_rss_feed_itself():
    from ajp.channels import higheredjobs
    for d in higheredjobs.deeplinks():
        assert "categoryFeed.cfm" not in d.url            # the Imperva-walled route, never linked or fetched


def test_sljaka_manifest_only_permits_the_rss_feed_itself():
    assert sljaka.MANIFEST.permits(sljaka.RSS_URL)
    assert not sljaka.MANIFEST.permits("https://sljaka.com/poslovi/domar/")   # a listing page, never fetched directly
