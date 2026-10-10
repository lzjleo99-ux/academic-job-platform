"""T20 acceptance: GET /v1/governance and /v1/coverage - the governance dashboard's own API, over the
same dict_row connection every other API route uses."""
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp import discovery
from ajp.api.app import create_app
from ajp.api.deps import get_conn


def test_governance_and_coverage_endpoints_return_real_data(conn):
    conn.execute("""INSERT INTO org_master (slug, name_en, country, type, homepage)
                    VALUES ('gov-test-org', 'Gov Test Org', 'RS', 'faculty', 'https://gov-test-org.invalid/')""")
    discovery.record_run(conn, discovery.DiscoveryResult("gov-test-org", "https://gov-test-org.invalid/",
                                                          "href_harvest", ["https://gov-test-org.invalid/x"], 1))
    discovery.record_discovery(conn, discovery.DiscoveryResult("gov-test-org", "https://gov-test-org.invalid/",
                                                                "href_harvest", ["https://gov-test-org.invalid/x"], 1))

    app = create_app()
    conn.row_factory = dict_row

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        gov = c.get("/v1/governance").json()
        assert gov["discovery_funnel"]["discovered"] >= 1

        cov = c.get("/v1/coverage").json()
        assert cov["discovery_runs_total"] >= 1
        assert "rs_orgs_total" in cov
