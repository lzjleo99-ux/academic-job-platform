"""T17 source: fetch OA PDFs discovered by the other adapters (openalex/core/unpaywall) and parse them with
T16's PDF parser for full-text (not just abstract) matching. Governance: robots-gated through PoliteClient
exactly like every connector in this project (1 rps/host default, content-type + size cap, timeout, retry/
backoff) - a denied or erroring host contributes zero requests past the robots check, never a fallback fetch.
This adapter is NOT probe-driven (search() is a documented no-op); it is fed a bounded list of already-
discovered candidate URLs by pipeline.py (see build_oa_fulltext_urls) - never a bulk crawl of a whole corpus.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from ajp.connectors.base import FetchFailed, ManifestViolation, PoliteClient, UrlManifest
from ajp.robots import RobotsDenied
from ajp.thesis.parser import ParseError, parse_pdf
from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter

MAX_PDF_BYTES = 15 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


class OaFulltextAdapter(SourceAdapter):
    source_id = "oa_fulltext"

    def __init__(self, client: Optional[PoliteClient] = None):
        self.client = client or PoliteClient(rps=1.0, timeout=DEFAULT_TIMEOUT)

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        return SearchResult(available=True, candidates_checked=0)   # see fetch_urls - not probe-driven

    def fetch_urls(self, urls: List[str], *, budget: int) -> SearchResult:
        checked = 0
        candidates: List[Candidate] = []
        errors: List[str] = []
        for url in urls[:budget]:
            checked += 1
            manifest = UrlManifest.exact(url)
            try:
                res = self.client.get(url, manifest=manifest, max_bytes=MAX_PDF_BYTES)
            except (FetchFailed, ManifestViolation, RobotsDenied) as e:
                errors.append(f"{url}: {e}")
                continue
            if res.status != 200 or "pdf" not in (res.content_type or "").lower():
                continue
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                    tmp.write(res.content)
                    tmp.flush()
                    doc = parse_pdf(Path(tmp.name))
            except (ParseError, Exception):                       # noqa: BLE001 - a broken PDF must not abort the run
                continue
            if doc.full_text.strip():
                candidates.append(Candidate(source_id=self.source_id, text=doc.full_text, url=url,
                                            attribution="OA full text (fetched + parsed)"))
        return SearchResult(candidates=candidates, candidates_checked=checked, available=True,
                            error="; ".join(errors[:5]) if errors else None)
