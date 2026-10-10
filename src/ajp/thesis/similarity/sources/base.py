"""T17 source adapter interface. Every adapter takes a short, bounded probe (a title or a handful of
top-shingle phrases - NEVER the full document text, per packet 008's privacy default) and returns candidate
documents/abstracts to fingerprint-match against. Every adapter must degrade gracefully: a quota error, a
timeout, or being disabled/unconfigured all just mean an empty result + a recorded reason, never an exception
that aborts the whole similarity run (packet 008: 'never block the run').
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class Candidate:
    source_id: str
    text: str                          # abstract or full text to fingerprint against
    title: Optional[str] = None
    url: Optional[str] = None          # the link shown to the user (DOI, landing page, or a PDF - whichever this source gives)
    attribution: Optional[str] = None  # e.g. 'CORE (id 12345)' - shown next to any matched span from this candidate
    oa_pdf_url: Optional[str] = None   # a directly fetchable OA full-text PDF, when this source knows one -
    doi: Optional[str] = None          # consumed by pipeline.py to feed ajp.thesis.similarity.sources.oa_fulltext
                                        # a bare DOI (no oa_pdf_url yet) - pipeline.py can try Unpaywall on it


@dataclass
class SearchResult:
    candidates: List[Candidate] = field(default_factory=list)
    candidates_checked: int = 0        # how many were fetched/considered, not just returned
    available: bool = True             # False = disabled (no key, not toggled on) - distinct from an error
    error: Optional[str] = None


class SourceAdapter:
    source_id: str = ""

    def search(self, probes: Sequence[str], *, budget: int) -> SearchResult:
        """``probes`` are short strings (a title, a few key sentences/shingles) - bounded to ``budget``
        underlying requests/candidates. Must never raise; catch everything internally and return a
        SearchResult with ``error`` set instead."""
        raise NotImplementedError
