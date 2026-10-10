"""T17 acceptance: 'source adapters tested with recorded fixtures; no live network in default test suite
(mark live); governance guard proves OA pdf fetches go through robots gate and denied hosts get zero
requests'; 'Copyleaks adapter tests are mock-only and prove zero calls without keys/toggle'.
"""
import json
from datetime import datetime, timezone

import pytest

from ajp.connectors.base import FetchResult, ManifestViolation, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources import copyleaks, core, crossref, oa_fulltext, openalex, semantic_scholar, unpaywall
from stubserver import Stub

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for PoliteClient in these adapter tests - a recorded fixture response, no network, but the
    SAME manifest is checked (ManifestViolation still raised) so a bad manifest pattern would still fail."""

    def __init__(self, manifest: UrlManifest, body: bytes, status: int = 200, content_type: str = "application/json"):
        self.manifest, self.body, self.status, self.content_type = manifest, body, status, content_type
        self.requested_urls = []
        self.requested_headers = []

    def get(self, url, *, manifest=None, max_bytes=None, extra_headers=None):
        if manifest is not None and not manifest.permits(url):
            raise ManifestViolation(url)
        self.requested_urls.append(url)
        self.requested_headers.append(extra_headers)
        return FetchResult(url=url, final_url=url, status=self.status, content=self.body,
                           content_type=self.content_type, fetched_at=NOW)


# ---------------------------------------------------------------- openalex -----------------------------------
OPENALEX_FIXTURE = json.dumps({
    "results": [{
        "id": "https://openalex.org/W123", "display_name": "A Study of Example Topics", "doi": "10.1/example",
        "abstract_inverted_index": {"This": [0], "is": [1], "a": [2], "reconstructed": [3], "abstract.": [4]},
        "open_access": {"oa_url": "https://example.invalid/oa.pdf"},
    }],
}).encode()


def test_openalex_adapter_parses_recorded_fixture_and_reconstructs_the_abstract():
    client = FakeClient(openalex.MANIFEST, OPENALEX_FIXTURE)
    adapter = openalex.OpenAlexAdapter(client=client)
    result = adapter.search(["Example thesis title"], budget=5)
    assert result.available and result.error is None
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.text == "This is a reconstructed abstract."
    assert c.title == "A Study of Example Topics"
    assert "mailto=" in client.requested_urls[0]


def test_openalex_reconstruct_abstract_handles_empty_index():
    assert openalex.reconstruct_abstract(None) == ""
    assert openalex.reconstruct_abstract({}) == ""


# ---------------------------------------------------------------- crossref -----------------------------------
CROSSREF_FIXTURE = json.dumps({
    "message": {"items": [{"title": ["Example Crossref Title"], "DOI": "10.2/example",
                           "URL": "https://doi.org/10.2/example", "abstract": "<jats:p>An abstract.</jats:p>"}]},
}).encode()


def test_crossref_adapter_strips_jats_tags_from_the_abstract():
    client = FakeClient(crossref.MANIFEST, CROSSREF_FIXTURE)
    result = crossref.CrossrefAdapter(client=client).search(["Example"], budget=5)
    assert result.candidates[0].text == "An abstract."
    assert "<jats" not in result.candidates[0].text


def test_crossref_adapter_falls_back_to_title_when_no_abstract():
    fixture = json.dumps({"message": {"items": [{"title": ["Title Only"], "DOI": "10.3/x"}]}}).encode()
    client = FakeClient(crossref.MANIFEST, fixture)
    result = crossref.CrossrefAdapter(client=client).search(["q"], budget=5)
    assert result.candidates[0].text == "Title Only"


# ---------------------------------------------------------------- core ---------------------------------------
CORE_FIXTURE = json.dumps({
    "results": [{"id": 555, "title": "A CORE Result", "abstract": "CORE abstract text.",
                "downloadUrl": "https://example.invalid/core.pdf"}],
}).encode()


def test_core_adapter_parses_recorded_fixture():
    client = FakeClient(core.MANIFEST, CORE_FIXTURE)
    result = core.CoreAdapter(client=client).search(["q"], budget=5)
    assert result.candidates[0].text == "CORE abstract text."
    assert result.candidates[0].url == "https://example.invalid/core.pdf"


def test_core_manifest_permits_the_real_trailing_slash_redirect_target():
    """CORE redirects .../v3/search/works?q=... to .../v3/search/works/?q=... (trailing slash added) - found
    live, 2026-09-23; PoliteClient re-checks every redirect hop against the same manifest, so both shapes
    must be permitted or the adapter's very first live call fails on ManifestViolation."""
    assert core.MANIFEST.permits("https://api.core.ac.uk/v3/search/works?q=x&limit=3")
    assert core.MANIFEST.permits("https://api.core.ac.uk/v3/search/works/?q=x&limit=3")


def test_core_adapter_sends_the_api_key_header_only_when_configured():
    client = FakeClient(core.MANIFEST, CORE_FIXTURE)
    core.CoreAdapter(client=client, api_key="secret123").search(["q"], budget=5)
    assert client.requested_headers[0] == {"Authorization": "Bearer secret123"}
    client2 = FakeClient(core.MANIFEST, CORE_FIXTURE)
    core.CoreAdapter(client=client2, api_key=None).search(["q"], budget=5)
    assert client2.requested_headers[0] is None


# ---------------------------------------------------------------- semantic scholar -----------------------------
S2_FIXTURE = json.dumps({
    "data": [{"title": "An S2 Paper", "abstract": "S2 abstract.",
             "openAccessPdf": {"url": "https://example.invalid/s2.pdf"}, "externalIds": {"DOI": "10.4/x"}}],
}).encode()


def test_semantic_scholar_adapter_parses_recorded_fixture():
    client = FakeClient(semantic_scholar.MANIFEST, S2_FIXTURE)
    result = semantic_scholar.SemanticScholarAdapter(client=client).search(["q"], budget=5)
    assert result.candidates[0].text == "S2 abstract."


def test_semantic_scholar_429_degrades_gracefully_never_raises():
    from ajp.connectors.base import FetchFailed

    class Failing429Client:
        def get(self, url, **kw):
            raise FetchFailed(f"HTTP 429 for {url}")

    result = semantic_scholar.SemanticScholarAdapter(client=Failing429Client()).search(["q"], budget=5)
    assert result.available is True
    assert result.candidates == []
    assert "429" in result.error


# ---------------------------------------------------------------- unpaywall ------------------------------------
def test_unpaywall_search_is_a_documented_noop():
    result = unpaywall.UnpaywallAdapter(client=FakeClient(unpaywall.MANIFEST, b"{}")).search(["q"], budget=5)
    assert result.candidates == [] and result.available is True


def test_unpaywall_resolve_oa_pdf_url_from_recorded_fixture():
    fixture = json.dumps({"best_oa_location": {"url_for_pdf": "https://example.invalid/unpaywall.pdf"}}).encode()
    client = FakeClient(unpaywall.MANIFEST, fixture)
    adapter = unpaywall.UnpaywallAdapter(client=client)
    assert adapter.resolve_oa_pdf_url("10.5/example") == "https://example.invalid/unpaywall.pdf"


def test_unpaywall_resolve_returns_none_on_no_oa_location():
    client = FakeClient(unpaywall.MANIFEST, b"{}")
    assert unpaywall.UnpaywallAdapter(client=client).resolve_oa_pdf_url("10.6/x") is None


# ---------------------------------------------------------------- oa_fulltext governance guard ------------------
def test_oa_fulltext_denied_host_gets_zero_requests_past_robots():
    stub = Stub()
    try:
        stub.robots("User-agent: *\nDisallow: /")           # disallow everything
        stub.route("/paper.pdf", status=200, headers={"Content-Type": "application/pdf"}, body=b"%PDF-1.4 fake")
        client = PoliteClient(rps=1000, sleep=lambda s: None)
        adapter = oa_fulltext.OaFulltextAdapter(client=client)
        result = adapter.fetch_urls([f"{stub.base}/paper.pdf"], budget=5)
        assert result.candidates == []
        assert "/paper.pdf" not in stub.paths()               # the PDF path itself was NEVER requested
        assert "/robots.txt" in stub.paths()                  # only robots.txt was ever hit
    finally:
        stub.stop()


def test_oa_fulltext_allowed_host_fetches_and_parses_a_real_pdf():
    import pdfplumber
    from reportlab.pdfgen import canvas
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "This is a genuine OA full text paragraph for matching purposes today.")
    c.save()
    pdf_bytes = buf.getvalue()

    stub = Stub()
    try:
        stub.robots("")                                       # no robots.txt -> R1 default allow
        stub.route("/paper.pdf", status=200, headers={"Content-Type": "application/pdf"}, body=pdf_bytes)
        client = PoliteClient(rps=1000, sleep=lambda s: None)
        adapter = oa_fulltext.OaFulltextAdapter(client=client)
        result = adapter.fetch_urls([f"{stub.base}/paper.pdf"], budget=5)
        assert len(result.candidates) == 1
        assert "genuine OA full text" in result.candidates[0].text
    finally:
        stub.stop()


# ---------------------------------------------------------------- copyleaks: mock-only, zero calls -------------
def test_copyleaks_makes_zero_calls_without_keys():
    class ExplodingClient:
        def get(self, *a, **kw):
            raise AssertionError("Copyleaks must never be called without keys")

    adapter = copyleaks.CopyleaksAdapter(email=None, api_key=None, client=ExplodingClient())
    assert adapter.configured is False
    assert adapter.run("scan-1", b"doc bytes", "thesis.docx", consent=True) is None


def test_copyleaks_makes_zero_calls_without_consent_even_when_configured():
    class ExplodingClient:
        def get(self, *a, **kw):
            raise AssertionError("Copyleaks must never be called without explicit consent")

    adapter = copyleaks.CopyleaksAdapter(email="a@b.invalid", api_key="k", client=ExplodingClient())
    assert adapter.configured is True
    assert adapter.run("scan-1", b"doc bytes", "thesis.docx", consent=False) is None


def test_copyleaks_only_calls_the_api_when_both_configured_and_consented(monkeypatch):
    login_fixture = json.dumps({"access_token": "tok123"}).encode()
    client = FakeClient(copyleaks.LOGIN_MANIFEST, login_fixture)
    adapter = copyleaks.CopyleaksAdapter(email="a@b.invalid", api_key="k", client=client)
    result = adapter.run("scan-1", b"doc bytes", "thesis.docx", consent=True)
    assert client.requested_urls, "expected exactly the login call to have been made"
    assert result["status"] in ("submitted", "submitted_no_webhook")
    assert result["provider"] == "copyleaks"
