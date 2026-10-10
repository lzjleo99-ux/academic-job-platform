"""T17: sentence-level matching between the thesis and a source's candidate documents - shared by every
adapter (each adapter only has to fetch candidate texts; this module does the actual fingerprint comparison).
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ajp.thesis.similarity.fingerprint import Fingerprint, exact_overlap_ratio, fingerprint_text, jaccard
from ajp.thesis.similarity.formula import PARAPHRASE_JACCARD_THRESHOLD, VERBATIM_CONTAINMENT_THRESHOLD, SpanMatch
from ajp.thesis.similarity.normalize import normalize
from ajp.thesis.similarity.segment import Sentence, segment_sentences
from ajp.thesis.similarity.sources.base import Candidate

_SNIPPET_MAX = 220


def match_against_candidates(thesis_sentences: Sequence[Sentence], thesis_fps: Sequence[Fingerprint],
                             source_id: str, candidates: Sequence[Candidate]) -> List[SpanMatch]:
    """One SpanMatch per thesis sentence that clears at least the paraphrase threshold against ANY candidate
    from this source - the best-scoring candidate wins per sentence, so a sentence is never reported twice
    for the same source even if several candidates partially match it."""
    matches: Dict[int, SpanMatch] = {}
    for cand in candidates:
        cand_sentences = segment_sentences(cand.text)
        cand_fps = [fingerprint_text(normalize(s.text)) for s in cand_sentences]
        for ti, (tsent, tfp) in enumerate(zip(thesis_sentences, thesis_fps)):
            best: "Tuple[float, str] | None" = None
            for cfp in cand_fps:
                containment = exact_overlap_ratio(tfp, cfp)
                if containment >= VERBATIM_CONTAINMENT_THRESHOLD:
                    candidate_score, signal = containment, "verbatim"
                else:
                    jac = jaccard(tfp, cfp)
                    if jac < PARAPHRASE_JACCARD_THRESHOLD:
                        continue
                    candidate_score, signal = jac, "paraphrase"
                if best is None or candidate_score > best[0]:
                    best = (candidate_score, signal)
            if best is None:
                continue
            score, signal = best
            existing = matches.get(ti)
            # a verbatim match always outranks a paraphrase one for the same sentence, regardless of score
            if existing is not None:
                existing_rank = (1, existing.score) if existing.signal == "verbatim" else (0, existing.score)
                new_rank = (1, score) if signal == "verbatim" else (0, score)
                if new_rank <= existing_rank:
                    continue
            matches[ti] = SpanMatch(
                sentence_index=ti, start=tsent.start, end=tsent.end, token_count=len(tsent.text.split()),
                snippet=tsent.text[:_SNIPPET_MAX], source_id=source_id, signal=signal, score=round(score, 4),
                source_url=cand.url, title=cand.title, attribution=cand.attribution,
            )
    return list(matches.values())
