"""T18 acceptance: Greenhouse connector with recorded fixtures (trimmed real sample, databricks tenant,
captured 2026-09-23) + pagination/detail mapping tests."""
import json
from datetime import datetime, timezone

from ajp.connectors.ats_greenhouse import GreenhouseConnector, TENANT_LINK_RE
from ajp.connectors.base import FetchResult, PoliteClient
from ajp.rawstore import LocalFsStore, put_raw

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

# Trimmed/adapted from a real response shape: GET https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true
JOBS = json.dumps({
    "jobs": [
        {"id": 8805655002, "title": "Research Software Engineer",
        "absolute_url": "https://example-tenant.invalid/careers/job?gh_jid=8805655002",
        "location": {"name": "Amsterdam, Netherlands"}, "first_published": "2026-09-16T09:11:57-04:00",
        "application_deadline": "2026-11-01T00:00:00-04:00",
        "content": "&lt;p&gt;We are looking for a Research Software Engineer to join our lab. "
                  "Contact: jobs@example-tenant.invalid&lt;/p&gt;"},
        {"id": 8805655003, "title": "Sales Executive",
        "absolute_url": "https://example-tenant.invalid/careers/job?gh_jid=8805655003",
        "location": {"name": "Unknown Location"}, "content": "&lt;p&gt;Sales role.&lt;/p&gt;"},
    ],
}).encode()
NOT_FOUND = json.dumps({"error": "Job not found"}).encode()


class Offline(GreenhouseConnector):
    body = b'{"jobs": []}'
    status = 200

    def fetch_raw(self, url, kind="json", max_bytes=None):
        res = FetchResult(url=url, final_url=url, status=Offline.status, content=Offline.body,
                          content_type="application/json", fetched_at=NOW)
        ref = put_raw(self.store, kind=kind, source_id=self.source_id, url=url, content=Offline.body, fetched_at=NOW,
                      status=Offline.status, content_type="application/json", robots_allowed=True)
        return res, ref


def fast():
    return PoliteClient(rps=1000, sleep=lambda s: None)


def test_crawl_maps_jobs_with_location_derived_country(tmp_path):
    Offline.body, Offline.status = JOBS, 200
    c = Offline(fast(), LocalFsStore(tmp_path), "example-tenant")
    rows, report = c.crawl()
    assert report["parsed"] == 2
    row = next(r for r in rows if r["title_raw"] == "Research Software Engineer")
    assert row["country_iso2"] == "NL" and row["city"] == "Amsterdam, Netherlands"
    assert row["source_id"] == "ats_greenhouse__example-tenant"
    assert row["url"] == "https://example-tenant.invalid/careers/job?gh_jid=8805655002"
    # S1 (PM code review 2026-09-23): application_deadline was being discarded entirely
    assert row["deadline"].isoformat().startswith("2026-11-01")
    assert row["status"] == "open"
    # T26 (packet 015)
    assert row["contact_emails"] == ["jobs@example-tenant.invalid"]
    assert row["apply_url"] == row["url"]


def test_crawl_persists_a_job_with_unresolvable_location_and_a_location_class(tmp_path):
    """T23 (PM ruling R2): an unresolvable country is never guessed (D6 unchanged) - the row is persisted
    with country_iso2=None + a real location_class now, not discarded."""
    Offline.body, Offline.status = JOBS, 200
    c = Offline(fast(), LocalFsStore(tmp_path), "example-tenant")
    rows, report = c.crawl()
    row = next(r for r in rows if r["title_raw"] == "Sales Executive")
    assert row["country_iso2"] is None
    assert row["location_class"] == "unknown"
    assert report["no_country"] == 0


def test_f10_entity_encoded_tags_are_decoded_then_stripped_never_left_over(tmp_path):
    """T29 (packet 017): this connector's own fixture (JOBS above) already has entity-ENCODED tags
    ('&lt;p&gt;', not literal '<p>') - found live building the decode fix: stripping HTML tags BEFORE
    decoding entities never sees them (nothing literal to match), and decoding AFTER stripping then un-hides
    them as a real, un-stripped '<p>'/'</p>' left sitting in the final raw_text. Must decode first."""
    Offline.body, Offline.status = JOBS, 200
    c = Offline(fast(), LocalFsStore(tmp_path), "example-tenant")
    rows, _ = c.crawl()
    row = next(r for r in rows if r["title_raw"] == "Research Software Engineer")
    assert "<p>" not in row["raw_text"] and "&lt;" not in row["raw_text"]
    assert row["raw_text"] == "We are looking for a Research Software Engineer to join our lab. Contact: jobs@example-tenant.invalid"


def test_nonexistent_tenant_404_yields_zero_rows_never_raises(tmp_path):
    Offline.body, Offline.status = NOT_FOUND, 404
    c = Offline(fast(), LocalFsStore(tmp_path), "nonexistent")
    rows, report = c.crawl()
    assert rows == [] and report["attempted"] == 1 and report["parsed"] == 0


def test_manifest_never_permits_the_disallowed_embed_path(tmp_path):
    c = GreenhouseConnector(fast(), LocalFsStore(tmp_path), "example-tenant")
    assert not c.manifest.permits("https://boards-api.greenhouse.io/embed/job_board?for=example-tenant")
    assert c.manifest.permits("https://boards-api.greenhouse.io/v1/boards/example-tenant/jobs?content=true")


def test_tenant_link_regex_extracts_from_a_careers_page_href():
    m = TENANT_LINK_RE.search('<a href="https://boards.greenhouse.io/example-tenant">Careers</a>')
    assert m.group(1) == "example-tenant"
