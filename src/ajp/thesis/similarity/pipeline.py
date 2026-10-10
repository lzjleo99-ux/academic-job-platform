"""T17: orchestrates extract (already done by T16's parser) -> normalize -> segment -> fingerprint -> match
sources -> score. This is the one function the API layer calls; every adapter's own network policy (bounded
probes, graceful degradation, Copyleaks consent-gating) is enforced by the adapters themselves, not repeated
here - this module just wires them together and hands the result to formula.compute_report.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ajp.connectors.base import PoliteClient
from ajp.thesis.parser import ThesisDoc
from ajp.thesis.similarity.fingerprint import fingerprint_text
from ajp.thesis.similarity.formula import SimilarityReport, SourceCoverage, compute_report
from ajp.thesis.similarity.matcher import match_against_candidates
from ajp.thesis.similarity.normalize import looks_like_masked_artifact, mask_references, normalize
from ajp.thesis.similarity.segment import segment_sentences
from ajp.thesis.similarity.sources.base import SourceAdapter
from ajp.thesis.similarity.sources.core import CoreAdapter
from ajp.thesis.similarity.sources.crossref import CrossrefAdapter
from ajp.thesis.similarity.sources.internal_corpus import InternalCorpusAdapter
from ajp.thesis.similarity.sources.oa_fulltext import OaFulltextAdapter
from ajp.thesis.similarity.sources.openalex import OpenAlexAdapter
from ajp.thesis.similarity.sources.semantic_scholar import SemanticScholarAdapter
from ajp.thesis.similarity.sources.unpaywall import UnpaywallAdapter

DEFAULT_PROBE_BUDGET = 5
MAX_PROBES = 6
MAX_OA_FULLTEXT_FETCHES = 3        # 'never bulk-download whole corpora' (packet 008) - a hard, small cap


def default_adapters(internal_corpus_documents: "List[tuple]" = (), client: Optional[PoliteClient] = None
                     ) -> "Dict[str, SourceAdapter]":
    """The 7 non-Copyleaks source adapters, keyed by source_id, sharing one PoliteClient (one rate-limit
    bucket per host is still correct even shared across sources, since each source is its own host)."""
    client = client or PoliteClient(rps=1.0)
    return {
        "internal_corpus": InternalCorpusAdapter(list(internal_corpus_documents)),
        "openalex": OpenAlexAdapter(client=client),
        "crossref": CrossrefAdapter(client=client),
        "core": CoreAdapter(client=client),
        "semantic_scholar": SemanticScholarAdapter(client=client),
        "unpaywall": UnpaywallAdapter(client=client),   # search() is a documented no-op - see its own module
    }                                                    # docstring; still shown in per-source coverage.


def build_probes(doc: ThesisDoc, sentences) -> List[str]:
    """Bounded, short probes only - a title and a handful of the longest sentences (a reasonable proxy for
    'key sentences') - NEVER the full document text (packet 008's privacy default)."""
    probes: List[str] = []
    title = doc.metadata.get("title")
    if title:
        probes.append(str(title)[:200])
    longest = sorted(sentences, key=lambda s: len(s.text), reverse=True)[:MAX_PROBES - len(probes)]
    probes.extend(s.text[:200] for s in longest)
    return probes[:MAX_PROBES]


def run_similarity(doc: ThesisDoc, adapters: "Dict[str, SourceAdapter]", *,
                   enabled_sources: Optional[Sequence[str]] = None, probe_budget: int = DEFAULT_PROBE_BUDGET,
                   oa_fulltext_adapter: Optional[OaFulltextAdapter] = None,
                   copyleaks_result: Optional[Dict[str, Any]] = None) -> SimilarityReport:
    masked = mask_references(doc.full_text, doc.references)
    sentences = [s for s in segment_sentences(masked) if not looks_like_masked_artifact(s.text)]
    fps = [fingerprint_text(normalize(s.text)) for s in sentences]
    probes = build_probes(doc, sentences)

    all_matches = []
    coverage: List[SourceCoverage] = []
    discovered_oa_urls: List[str] = []
    unresolved_dois: List[str] = []
    unpaywall_adapter: Optional[UnpaywallAdapter] = adapters.get("unpaywall")  # type: ignore[assignment]

    for source_id, adapter in adapters.items():
        if enabled_sources is not None and source_id not in enabled_sources:
            coverage.append(SourceCoverage(source_id=source_id, available=False))
            continue
        result = adapter.search(probes, budget=probe_budget)
        source_matches = match_against_candidates(sentences, fps, source_id, result.candidates) if result.candidates else []
        all_matches.extend(source_matches)
        coverage.append(SourceCoverage(source_id=source_id, available=result.available,
                                       candidates_checked=result.candidates_checked,
                                       matched=len(source_matches), error=result.error))
        for c in result.candidates:
            if c.oa_pdf_url:
                discovered_oa_urls.append(c.oa_pdf_url)
            elif c.doi:
                unresolved_dois.append(c.doi)

    # bridge: resolve a few bare DOIs via Unpaywall, then fetch+parse a small, hard-capped number of the
    # resulting PDFs for full-text (not just abstract) matching - never a bulk download of whole sources.
    if (enabled_sources is None or "oa_fulltext" in enabled_sources) and unpaywall_adapter is not None:
        for doi in unresolved_dois:
            if len(discovered_oa_urls) >= MAX_OA_FULLTEXT_FETCHES:
                break
            resolved = unpaywall_adapter.resolve_oa_pdf_url(doi)
            if resolved:
                discovered_oa_urls.append(resolved)

    if enabled_sources is None or "oa_fulltext" in enabled_sources:
        adapter = oa_fulltext_adapter or OaFulltextAdapter()
        result = adapter.fetch_urls(discovered_oa_urls[:MAX_OA_FULLTEXT_FETCHES], budget=MAX_OA_FULLTEXT_FETCHES)
        source_matches = match_against_candidates(sentences, fps, "oa_fulltext", result.candidates) if result.candidates else []
        all_matches.extend(source_matches)
        coverage.append(SourceCoverage(source_id="oa_fulltext", available=True,
                                       candidates_checked=result.candidates_checked,
                                       matched=len(source_matches), error=result.error))
    else:
        coverage.append(SourceCoverage(source_id="oa_fulltext", available=False))

    return compute_report(sentences, all_matches, coverage, copyleaks=copyleaks_result)
