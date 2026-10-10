"""T17 source: Crossref works search (keyless, polite pool via mailto in the User-Agent per Crossref's own
etiquette - see ajp.config.user_agent). Coverage: bibliographic metadata; abstracts are rarely present, so
this source mostly contributes title-level candidates (packet 008: 'bibliographic verification + title-level
dedupe, not body similarity') - a thin candidate is not a bug, just honest about what this API returns.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Sequence
from urllib.parse import quote

from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter

HOST = "api.crossref.org"
MANIFEST = UrlManifest(rf"https://{HOST}/works\?query=[^ ]*")
_JATS_TAG = re.compile(r"<[^>]+>")


def _strip_jats(abstract: Optional[str]) -> str:
    return _JATS_TAG.sub(" ", abstract or "").strip()


class CrossrefAdapter(SourceAdapter):
    source_id = "crossref"

    def __init__(self, client: Optional[PoliteClient] = None, rows: int = 10):
        self.client = client or PoliteClient(rps=1.0)
        self.rows = rows

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        if not probes:
            return SearchResult(available=True, candidates_checked=0)
        query = probes[0]
        url = f"https://{HOST}/works?query={quote(query)}&rows={min(self.rows, budget)}"
        try:
            res = self.client.get(url, manifest=MANIFEST)
        except FetchFailed as e:
            return SearchResult(available=True, error=str(e))
        try:
            data: Dict[str, Any] = json.loads(res.content)
        except ValueError as e:
            return SearchResult(available=True, error=f"bad JSON: {e}")
        items = (data.get("message") or {}).get("items", [])[:budget]
        candidates = []
        for it in items:
            title = " ".join(it.get("title") or []) or None
            abstract = _strip_jats(it.get("abstract"))
            text = abstract or title or ""
            if not text:
                continue
            candidates.append(Candidate(
                source_id=self.source_id, text=text, title=title, url=it.get("URL") or it.get("DOI"),
                attribution=f"Crossref (DOI {it.get('DOI', '?')})", doi=it.get("DOI"),
            ))
        return SearchResult(candidates=candidates, candidates_checked=len(items), available=True)
