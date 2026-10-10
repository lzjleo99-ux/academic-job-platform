"""T17 source: CORE v3 search (anonymous tier observed working, ~10 req/window - PM verified live
2026-09-22; CORE_API_KEY raises the quota when set, never required). Coverage: aggregated OA full-text
abstracts + download URLs.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Sequence
from urllib.parse import quote

from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter

HOST = "api.core.ac.uk"
# CORE redirects /v3/search/works?... to /v3/search/works/?... (trailing slash added) - found live, 2026-09-23;
# the manifest must permit the redirect target too (PoliteClient re-checks every hop against the same manifest).
MANIFEST = UrlManifest(rf"https://{HOST}/v3/search/works/?\?q=[^ ]*")


class CoreAdapter(SourceAdapter):
    source_id = "core"

    def __init__(self, client: Optional[PoliteClient] = None, limit: int = 10, api_key: Optional[str] = None):
        self.client = client or PoliteClient(rps=1.0)
        self.limit = limit
        self.api_key = api_key if api_key is not None else os.environ.get("CORE_API_KEY")

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        if not probes:
            return SearchResult(available=True, candidates_checked=0)
        query = probes[0]
        url = f"https://{HOST}/v3/search/works?q={quote(query)}&limit={min(self.limit, budget)}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            res = self.client.get(url, manifest=MANIFEST, extra_headers=headers)
        except FetchFailed as e:
            return SearchResult(available=True, error=str(e))
        try:
            data: Dict[str, Any] = json.loads(res.content)
        except ValueError as e:
            return SearchResult(available=True, error=f"bad JSON: {e}")
        results = data.get("results", [])[:budget]
        candidates = []
        for r in results:
            text = r.get("abstract") or r.get("fullText") or ""
            if not text:
                continue
            download_url = r.get("downloadUrl") or (r.get("sourceFulltextUrls") or [None])[0]
            candidates.append(Candidate(
                source_id=self.source_id, text=text, title=r.get("title"), url=download_url,
                attribution=f"CORE (id {r.get('id', '?')})", oa_pdf_url=download_url,
            ))
        return SearchResult(candidates=candidates, candidates_checked=len(results), available=True)
