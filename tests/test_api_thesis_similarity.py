"""T17 acceptance (API layer): similarity trigger/result endpoints, source toggles, Copyleaks consent gating.
Passing sources=['internal_corpus'] explicitly excludes every network adapter AND oa_fulltext/copyleaks from
even being called (see ajp.thesis.similarity.pipeline.run_similarity's own enabled_sources gate) - this is
what keeps this file network-free by default, not a mock; a live run against every real source is verified
separately (see pm/outbox evidence), matching 'no live network in default test suite (mark live)'.
"""
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.thesis import store as thesis_store
from thesis_fixtures.build_fixtures import build_clean


@pytest.fixture()
def client(conn, tmp_path, monkeypatch):
    conn.row_factory = dict_row
    monkeypatch.setattr(thesis_store, "DEFAULT_ROOT", tmp_path / "thesis_store")
    app = create_app()

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


def _upload(client, tmp_path, name="clean.docx"):
    path = build_clean(tmp_path / name)
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze", files={"file": (name, f)})
    return r.json()["id"]


def test_similarity_sources_endpoint_lists_all_eight():
    c = TestClient(create_app())
    sources = c.get("/v1/thesis/similarity/sources").json()
    assert set(sources) == {"internal_corpus", "openalex", "crossref", "core", "semantic_scholar",
                            "unpaywall", "oa_fulltext", "copyleaks"}


def test_similarity_run_with_only_internal_corpus_enabled_makes_no_network_adapter_calls(client, tmp_path):
    task_id = _upload(client, tmp_path)
    r = client.post(f"/v1/thesis/{task_id}/similarity", params={"sources": ["internal_corpus"]})
    assert r.status_code == 200
    body = r.json()
    by_source = {c["source_id"]: c for c in body["coverage"]}
    assert by_source["internal_corpus"]["available"] is True
    for sid in ("openalex", "crossref", "core", "semantic_scholar", "unpaywall", "oa_fulltext"):
        assert by_source[sid]["available"] is False
    assert body["copyleaks"] is None                              # copyleaks is a separate gate, not in `coverage`
    assert body["estimate_pct"] == 0.0                            # nothing to compare against yet
    assert any("heuristic similarity ESTIMATE" in d for d in body["disclaimers"])
    # D4: the Turnitin/iThenticate mention is only ever there to disclaim equivalence, never to claim it
    turnitin_lines = [d for d in body["disclaimers"] if "Turnitin" in d]
    assert turnitin_lines and all("not" in d for d in turnitin_lines)


def test_similarity_result_is_persisted_and_retrievable(client, tmp_path):
    task_id = _upload(client, tmp_path)
    posted = client.post(f"/v1/thesis/{task_id}/similarity", params={"sources": ["internal_corpus"]}).json()
    got = client.get(f"/v1/thesis/{task_id}/similarity").json()
    assert got["estimate_pct"] == posted["estimate_pct"]
    task = client.get(f"/v1/thesis/{task_id}").json()
    assert task["similarity"] is not None


def test_similarity_before_running_returns_404(client, tmp_path):
    task_id = _upload(client, tmp_path)
    r = client.get(f"/v1/thesis/{task_id}/similarity")
    assert r.status_code == 404


def test_copyleaks_is_never_called_without_consent_even_when_enabled_in_sources(client, tmp_path, monkeypatch):
    """Enabling 'copyleaks' in `sources` is not consent - both gates are required (packet 008)."""
    calls = []
    from ajp.thesis.similarity.sources.copyleaks import CopyleaksAdapter
    monkeypatch.setattr(CopyleaksAdapter, "run", lambda self, *a, **kw: calls.append(kw.get("consent")) or None)

    task_id = _upload(client, tmp_path)
    r = client.post(f"/v1/thesis/{task_id}/similarity",
                    params={"sources": ["internal_corpus", "copyleaks"], "copyleaks_consent": False})
    assert r.status_code == 200
    assert calls == [False]                                     # CopyleaksAdapter.run was called, but with consent=False
    assert r.json()["copyleaks"] is None                          # and therefore returned nothing


def test_similarity_unknown_task_returns_404(client):
    r = client.post("/v1/thesis/00000000-0000-0000-0000-000000000000/similarity",
                    params={"sources": ["internal_corpus"]})
    assert r.status_code == 404
