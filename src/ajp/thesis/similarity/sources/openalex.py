"""T17 source: OpenAlex works search (keyless, mailto polite pool - verified live by PM 2026-09-22, see
pm/recon/t17_corpus_apis.json). Coverage: titles + reconstructed abstracts, OA status/urls.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from ajp.config import contact_email
from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter

HOST = "api.openalex.org"
MANIFEST = UrlManifest(rf"https://{HOST}/works\?search=[^ ]*")


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex stores abstracts as {word: [positions]} to respect publisher copyright on full text - this
    is the documented, standard way to get the plain text back."""
    if not inverted_index:
        return ""
    positions: Dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


class OpenAlexAdapter(SourceAdapter):
    source_id = "openalex"

    def __init__(self, client: Optional[PoliteClient] = None, per_page: int = 10):
        self.client = client or PoliteClient(rps=1.0)
        self.per_page = per_page

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        if not probes:
            return SearchResult(available=True, candidates_checked=0)
        query = probes[0]                                      # the title (or first probe) - one bounded query
        url = (f"https://{HOST}/works?search={quote(query)}&per-page={min(self.per_page, budget)}"
              f"&mailto={quote(contact_email())}")
        try:
            res = self.client.get(url, manifest=MANIFEST)
        except FetchFailed as e:
            return SearchResult(available=True, error=str(e))
        try:
            data: Dict[str, Any] = json.loads(res.content)
        except ValueError as e:
            return SearchResult(available=True, error=f"bad JSON: {e}")
        results = data.get("results", [])[:budget]
        candidates = []
        for w in results:
            abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
            if not abstract:
                continue                                        # nothing to fingerprint against
            oa = w.get("open_access") or {}
            doi = w.get("doi")
            candidates.append(Candidate(
                source_id=self.source_id, text=abstract, title=w.get("display_name") or w.get("title"),
                url=doi or w.get("id"), attribution=f"OpenAlex ({w.get('id', '')})",
                oa_pdf_url=oa.get("oa_url"), doi=doi,
            ))
        return SearchResult(candidates=candidates, candidates_checked=len(results), available=True)
