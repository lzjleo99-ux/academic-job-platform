"""T17 source: user-uploaded reference docs + previously analyzed docs in this local workspace. Always
available (local only, packet 008) - no network, no budget concerns beyond the corpus's own size.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from ajp.thesis.similarity.sources.base import Candidate, SearchResult, SourceAdapter


class InternalCorpusAdapter(SourceAdapter):
    source_id = "internal_corpus"

    def __init__(self, documents: "List[Tuple[str, str, str]]"):
        """``documents``: (title, url_or_empty, text) tuples - e.g. previously-analyzed thesis_task rows or
        reference files the user explicitly uploaded to this workspace."""
        self.documents = documents

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        docs = self.documents[:budget]
        candidates = [Candidate(source_id=self.source_id, text=text, title=title or None, url=url or None,
                                attribution="uploaded reference document") for title, url, text in docs]
        return SearchResult(candidates=candidates, candidates_checked=len(self.documents), available=True)
