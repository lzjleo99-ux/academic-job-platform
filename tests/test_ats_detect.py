"""T18 acceptance: 'ats detection unit tests: sample career-page HTML snippets -> correct family/tenant
extraction'."""
from ajp.connectors.ats_detect import detect_ats, detect_one

WORKDAY_SNIPPET = """<a href="https://embl.wd103.myworkdayjobs.com/en-US/EMBL">Open positions</a>"""
SMARTRECRUITERS_SNIPPET = """<a href="https://jobs.smartrecruiters.com/CERN">Careers at CERN</a>"""
TEAMTAILOR_SNIPPET = """<iframe src="https://ki.teamtailor.com/jobs"></iframe>"""
GREENHOUSE_SNIPPET = """<a href="https://boards.greenhouse.io/airbnb">View openings</a>"""
LEVER_SNIPPET = """<a class="apply" href="https://jobs.lever.co/leverdemo/abc-123">Apply</a>"""
ICIMS_SNIPPET = """<a href="https://acme.icims.com/jobs/intro">Careers</a>"""
TALEO_SNIPPET = """<a href="https://acme.taleo.net/careersection/2/jobsearch.ftl">Search jobs</a>"""
SUCCESSFACTORS_SNIPPET = """<a href="https://career5.successfactors.com/career?company=acme">Jobs</a>"""
REACHMEE_SNIPPET = """<a href="https://web103.reachmee.com/apply?id=42">Sök jobbet</a>"""
JOBTEASER_SNIPPET = """<a href="https://university.jobteaser.com/en/job-offers">Student jobs</a>"""
NO_ATS_SNIPPET = """<a href="/konkursi/2026/oglas-1.html">Konkurs za asistenta</a>"""


def test_workday_tenant_and_site_extracted():
    m = detect_one(WORKDAY_SNIPPET)
    assert m.family == "ats_workday_cxs" and m.tenant == "embl" and m.site == "EMBL"


def test_smartrecruiters_tenant_extracted_detection_only():
    m = detect_one(SMARTRECRUITERS_SNIPPET)
    assert m.family == "ats_smartrecruiters" and m.tenant == "CERN"


def test_teamtailor_tenant_extracted():
    m = detect_one(TEAMTAILOR_SNIPPET)
    assert m.family == "ats_teamtailor" and m.tenant == "ki"


def test_greenhouse_tenant_extracted():
    m = detect_one(GREENHOUSE_SNIPPET)
    assert m.family == "ats_greenhouse" and m.tenant == "airbnb"


def test_lever_tenant_extracted():
    m = detect_one(LEVER_SNIPPET)
    assert m.family == "ats_lever" and m.tenant == "leverdemo"


def test_icims_tenant_extracted():
    m = detect_one(ICIMS_SNIPPET)
    assert m.family == "ats_icims" and m.tenant == "acme"


def test_taleo_detected_without_tenant_group():
    m = detect_one(TALEO_SNIPPET)
    assert m.family == "ats_taleo"


def test_successfactors_detected():
    m = detect_one(SUCCESSFACTORS_SNIPPET)
    assert m.family == "ats_successfactors"


def test_reachmee_detected():
    m = detect_one(REACHMEE_SNIPPET)
    assert m.family == "ats_reachmee_varbi"


def test_jobteaser_detected():
    m = detect_one(JOBTEASER_SNIPPET)
    assert m.family == "ats_jobteaser"


def test_no_ats_link_returns_none():
    assert detect_one(NO_ATS_SNIPPET) is None
    assert detect_ats(NO_ATS_SNIPPET) == []


def test_multiple_ats_links_all_detected_priority_picks_ingestable_one():
    html = WORKDAY_SNIPPET + REACHMEE_SNIPPET
    matches = detect_ats(html)
    families = {m.family for m in matches}
    assert families == {"ats_workday_cxs", "ats_reachmee_varbi"}
    assert detect_one(html).family == "ats_workday_cxs"     # ingestable family preferred over detection-only
