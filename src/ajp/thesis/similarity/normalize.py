"""T17 normalization: NFKC, whitespace/hyphen/quote unification, lowercase, and script-agnostic Serbian
matching (Cyrillic text is transliterated to Latin before fingerprinting, via the same sr_script module T16
uses for template heading matching - so a Cyrillic verbatim copy of a Latin source, or vice versa, still
fingerprints identically). Offsets for the UI's highlighted spans are computed on the ORIGINAL text during
segmentation (segment.py), before this normalization ever runs - normalize() is only ever applied to an
already-extracted sentence's own text, never used to compute a position back into the document.
"""
from __future__ import annotations

import re
import unicodedata

from ajp.thesis.parser import Reference
from ajp.thesis.sr_script import to_latin_for_matching

_HYPHENS = re.compile(r"[‐‑‒–—−]")
_QUOTES = re.compile(r"[‘’‚‛′]")
_DQUOTES = re.compile(r"[“”„‟″„“”]")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC -> transliterate Cyrillic to Latin -> unify hyphens/quotes -> collapse whitespace -> lowercase.
    Used per-sentence for fingerprinting; never used to compute an offset back into the source document."""
    text = unicodedata.normalize("NFKC", text)
    text = to_latin_for_matching(text)
    text = _HYPHENS.sub("-", text)
    text = _QUOTES.sub("'", text)
    text = _DQUOTES.sub('"', text)
    text = _WS.sub(" ", text).strip()
    return text.lower()


_LONG_WHITESPACE_RUN = re.compile(r"\s{8,}")


def looks_like_masked_artifact(text: str) -> bool:
    """A segmented 'sentence' spanning into a mask_references() blank-out run has no sentence-ending
    punctuation inside the masked stretch (whitespace never terminates a sentence), so it gets fused with
    whatever came before/after into one abnormally long pseudo-sentence with a huge internal gap - found
    live, 2026-09-23: this fused text was then picked as a 'longest sentence' probe by pipeline.build_probes,
    sending a mostly-blank query to every search adapter. Real prose never has an 8+ character whitespace
    run, so this is a safe, targeted filter - applied wherever sentences are segmented from masked text."""
    return bool(_LONG_WHITESPACE_RUN.search(text))


def mask_references(full_text: str, references: "list[Reference]") -> str:
    """Blank out reference-list lines (replaced with equal-length whitespace, never removed) so offsets into
    the returned string stay identical to offsets into ``full_text`` - the references/bibliography section is
    excluded from verbatim similarity scoring (packet 008), but the reference-overlap signal (formula.py)
    reads the very same raw reference strings separately, so they are masked here, never deleted."""
    if not references:
        return full_text
    raw_lines = {r.raw for r in references}
    out_lines = []
    for line in full_text.split("\n"):
        if line.strip() in raw_lines:
            out_lines.append(" " * len(line))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
