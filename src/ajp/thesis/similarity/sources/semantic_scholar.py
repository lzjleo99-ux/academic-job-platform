"""T17 source: Semantic Scholar Graph API search. Keyless access returns 429 frequently (PM observed live,
2026-09-22) - treated as best-effort: a 429 (after PoliteClient's own retry/backoff gives up) is recorded as
a normal, expected coverage gap, never a run-blocking error. Set S2_API_KEY for reliable access.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Sequence
from urllib.parse import quote

from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter

HOST = "api.semanticscholar.org"
FIELDS = "title,abstract,openAccessPdf,externalIds"
MANIFEST = UrlManifest(rf"https://{HOST}/graph/v1/paper/search\?query=[^ ]*")


class SemanticScholarAdapter(SourceAdapter):
    source_id = "semantic_scholar"

    def __init__(self, client: Optional[PoliteClient] = None, limit: int = 10, api_key: Optional[str] = None):
        self.client = client or PoliteClient(rps=1.0, max_retries=1)   # keyless 429s are expected - fail fast
        self.limit = limit
        self.api_key = api_key if api_key is not None else os.environ.get("S2_API_KEY")

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        if not probes:
            return SearchResult(available=True, candidates_checked=0)
        query = probes[0]
        url = f"https://{HOST}/graph/v1/paper/search?query={quote(query)}&limit={min(self.limit, budget)}&fields={FIELDS}"
        headers = {"x-api-key": self.api_key} if self.api_key else None
        try:
            res = self.client.get(url, manifest=MANIFEST, extra_headers=headers)
        except FetchFailed as e:
            # keyless 429 is an EXPECTED, documented degradation, not a failure worth alarming over - the
            # coverage report still records it (available=True, error set) so it is visible, never hidden.
            return SearchResult(available=True, error=f"best-effort keyless access: {e}")
        try:
            data: Dict[str, Any] = json.loads(res.content)
        except ValueError as e:
            return SearchResult(available=True, error=f"bad JSON: {e}")
        items = data.get("data", [])[:budget]
        candidates = []
        for p in items:
            abstract = p.get("abstract") or ""
            if not abstract:
                continue
            pdf = (p.get("openAccessPdf") or {}).get("url")
            doi = (p.get("externalIds") or {}).get("DOI")
            candidates.append(Candidate(
                source_id=self.source_id, text=abstract, title=p.get("title"), url=pdf,
                attribution=f"Semantic Scholar ({doi or '?'})", oa_pdf_url=pdf, doi=doi,
            ))
        return SearchResult(candidates=candidates, candidates_checked=len(items), available=True)
