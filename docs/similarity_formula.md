# Similarity estimate formula (T17, packet 008)

This is the transparent, deterministic formula behind `/v1/thesis/{id}/similarity`'s `estimate_pct`. Every
number it produces is reproducible from the stored matches - nothing here is a black-box score. The
authoritative implementation is `src/ajp/thesis/similarity/formula.py`; this document explains it in
prose and must be kept in sync with that file (its own docstrings link back here).

## D4 standing ruling (read this first)

There is no public Turnitin/iThenticate API and no way to compute a precise, web-wide plagiarism percentage
without a licensed corpus. This module produces a **heuristic similarity ESTIMATE** against open-access
sources and the user's own uploaded/previously-analyzed reference documents - never labelled "plagiarism %",
never presented as equivalent to a licensed plagiarism-detection service. Every report the API returns
carries this disclaimer verbatim (see `DISCLAIMERS` in `formula.py`), and the UI shows it before any number.

## Pipeline

```
extract (T16 parser)  ->  normalize  ->  segment  ->  fingerprint  ->  match sources  ->  score
```

1. **Extract**: T16's `ThesisDoc.full_text`, already parsed from the upload.
2. **Reference masking**: every line matching a parsed reference entry is replaced with equal-length
   whitespace (never deleted) so character offsets into the masked text stay identical to the original -
   the bibliography is excluded from verbatim scoring, but a separate reference/citation-overlap signal
   (T16's own `check_references`) still reads the un-masked reference list.
3. **Segment**: sentence splitting with exact offsets into the masked text (`similarity/segment.py`). A
   segmented span whose text contains an abnormally long whitespace run (8+ characters) is discarded - that
   only happens where a masked reference block fused with neighbouring text into one artifact "sentence"
   (found live, 2026-09-23; see `normalize.looks_like_masked_artifact`).
4. **Normalize**: NFKC, Cyrillic→Latin transliteration (script-agnostic matching), hyphen/quote unification,
   whitespace collapse, lowercasing (`similarity/normalize.py`).
5. **Fingerprint**: 5-word shingles, a SHA-256 exact-hash set per shingle (verbatim detection), and a MinHash
   sketch (Jaccard estimate, paraphrase band) - `similarity/fingerprint.py`. Uses `datasketch` when it
   installs, else a small pure-Python MinHash with an identical interface.
6. **Match sources**: each enabled source adapter returns candidate texts (an abstract, a full text, or the
   user's own reference corpus); every thesis sentence is compared against every candidate sentence
   (`similarity/matcher.py`).
7. **Score**: `compute_report()` below.

## Per-sentence classification

For a thesis sentence *S* and a candidate sentence *C* from some source:

- **containment** = `|shingles(S) ∩ shingles(C)| / |shingles(S)|` (asymmetric: how much of *S* is found in
  *C*, not a symmetric overlap).
  - containment ≥ **0.7** → **verbatim** match, score = containment.
  - containment ≥ **0.95** → also counts toward the **sentence_exact** signal.
- Otherwise, **Jaccard** = the MinHash-estimated Jaccard similarity of the two shingle sets.
  - Jaccard ≥ **0.10** → **possible paraphrase**, score = Jaccard.
  - The 0.10 threshold was calibrated empirically (see `tests/test_similarity_planted_duplication.py`):
    5-word-shingle Jaccard is naturally low for realistic sentence lengths (~15-25 words → ~10-20 shingles),
    so it moves in coarse steps; a genuine "light paraphrase" that still shares a few short phrases lands
    around 0.10-0.20, while a full rewrite shares essentially no 5-word run at all (Jaccard ≈ 0) - a real,
    documented limitation of shingle/MinHash techniques, not something hidden behind an unrealistic fixture.
  - A verbatim match for a sentence always outranks any paraphrase match for that same sentence, regardless
    of score.

Only the **best-scoring** match per (sentence, source) survives; a sentence matched by several candidates
from the same source is never counted twice.

## Aggregate percentages

Two "buckets" a verbatim match falls into (a sentence is counted at most once per bucket):

- **oa**: every open-access adapter collectively (openalex, crossref, unpaywall, core, semantic_scholar,
  oa_fulltext) - none promises meaningful coverage alone, so they share one bucket.
- **internal**: the user's own uploaded/previously-analyzed reference corpus (`internal_corpus`) - a
  fundamentally different, closed comparison set, kept separate from "the open web".

```
verbatim_oab_pct      = 100 * (sum of token counts of sentences with a best 'oa' verbatim match) / total_tokens
verbatim_internal_pct = 100 * (sum of token counts of sentences with a best 'internal' verbatim match) / total_tokens
union_verbatim_pct    = 100 * (sum of token counts of sentences with ANY verbatim match, counted once) / total_tokens
sentence_exact_pct    = 100 * (count of sentences with containment >= 0.95) / total_sentence_count
estimate_pct          = max(verbatim_oab_pct, verbatim_internal_pct), capped at union_verbatim_pct
```

`union_verbatim_pct` is always ≥ `max(verbatim_oab_pct, verbatim_internal_pct)` by construction (it is a
superset of whichever single bucket is larger), so the cap is a defensive invariant, not something normally
triggered - both the max-pair estimate and the union are always reported side by side, per packet 008's own
instruction ("report BOTH max-pair and union coverage").

Paraphrase matches are **never** added into `estimate_pct` - they are reported entirely separately
(`paraphrase_spans`), always labelled "possible paraphrase".

## Confidence band

Based on how many distinct sources actually returned a match (not how many were merely *enabled*):

| sources that matched | band       |
|-----------------------|-----------|
| 0                      | very_low  |
| 1                      | low       |
| 2-3                    | medium    |
| 4+                     | high      |

`coverage_statement` names exactly which sources those were, e.g. *"2 of 6 enabled source(s) returned
candidate matches (internal_corpus, openalex); the estimate is bounded to what those sources could see - it
is not a web-wide check."* Disabling a source (or Semantic Scholar's frequent keyless 429s reducing it to
zero matches) visibly moves the band down - this is intentional and tested
(`tests/test_similarity_formula.py::test_confidence_band_changes_when_sources_are_disabled`).

## Copyleaks

Copyleaks is never part of this formula. When enabled (both `COPLEAKS_EMAIL`/`COPLEAKS_API_KEY` configured
**and** the caller passes `copyleaks_consent=true` for that specific run), its own provider-native score is
attached to the report under `copyleaks`, with its own attribution and disclaimer, and is never folded into
`estimate_pct`. See `src/ajp/thesis/similarity/sources/copyleaks.py` for the exact two-gate rule and its
honest scope note (full result delivery needs a public webhook endpoint this local-first tool does not
provide by default).

## Privacy

The default run sends **no document text** to any external service - only a handful of short, bounded probes
(the document title, if any, and up to 5 of the longest sentences, each capped at 200 characters) are used as
search queries against the open-access adapters (`similarity/pipeline.py::build_probes`). Full document text
only ever leaves this server if Copyleaks is both configured and explicitly consented to for that run.
