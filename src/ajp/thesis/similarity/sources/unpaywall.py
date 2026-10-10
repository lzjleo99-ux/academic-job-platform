"""T17 source: Unpaywall (keyless, requires an email per its own terms - reuses ajp.config.contact_email).
Unpaywall has no free-text search endpoint - it resolves a KNOWN DOI to an OA full-text URL. So unlike the
other adapters, ``search()`` here is a documented no-op (it cannot act on title/shingle probes at all); the
real entry point is ``resolve_oa_pdf_url(doi)``, called by oa_fulltext.py for DOIs the other adapters (mainly
Crossref, sometimes OpenAlex) turned up without their own oa_url. It still appears as its own row in the
similarity report's per-source coverage - 'available but found nothing from these probes' is itself honest,
useful information, not a reason to hide the source from the report.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence
from urllib.parse import quote

from ajp.config import contact_email
from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources.base import SearchResult, SourceAdapter

HOST = "api.unpaywall.org"
MANIFEST = UrlManifest(rf"https://{HOST}/v2/[^ ?]+\?email=[^ ]*")


class UnpaywallAdapter(SourceAdapter):
    source_id = "unpaywall"

    def __init__(self, client: Optional[PoliteClient] = None):
        self.client = client or PoliteClient(rps=1.0)

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        return SearchResult(available=True, candidates_checked=0)

    def resolve_oa_pdf_url(self, doi: str) -> Optional[str]:
        url = f"https://{HOST}/v2/{quote(doi, safe='')}?email={quote(contact_email())}"
        try:
            res = self.client.get(url, manifest=MANIFEST)
        except FetchFailed:
            return None
        try:
            data = json.loads(res.content)
        except ValueError:
            return None
        for loc in [data.get("best_oa_location")] + (data.get("oa_locations") or []):
            if loc and loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
        return None
