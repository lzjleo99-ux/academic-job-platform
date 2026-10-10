"""T17: sentence segmentation with exact character offsets into the source text - the UI's highlighted spans
and every matched-span report entry point back into these offsets, so segmentation always runs on the
ORIGINAL (reference-masked, but otherwise untouched) text, never on a normalized copy (see normalize.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# Common Serbian/English abbreviations whose trailing '.' must never be read as a sentence end.
_ABBREVIATIONS = {
    "npr", "itd", "tj", "sl", "dr", "prof", "mr", "br", "god", "vol", "str", "gl", "tzv", "sr", "en",
    "mag", "doc", "st", "vs", "etc", "fig", "no",
}
_SENT_PUNCT = re.compile(r"[.!?]+")
_TRAILING_WORD = re.compile(r"(\w+)$", re.UNICODE)
MIN_SENTENCE_CHARS = 15


@dataclass
class Sentence:
    text: str
    start: int
    end: int


def segment_sentences(text: str, *, min_chars: int = MIN_SENTENCE_CHARS) -> List[Sentence]:
    sentences: List[Sentence] = []
    start = 0
    for m in _SENT_PUNCT.finditer(text):
        end_punct = m.end()
        word_match = _TRAILING_WORD.search(text[start:m.start()])
        word = word_match.group(1).lower() if word_match else ""
        if m.group(0) == "." and word in _ABBREVIATIONS:
            continue                                           # not a real sentence boundary - keep accumulating
        s = start
        while s < end_punct and text[s].isspace():
            s += 1
        if end_punct - s >= min_chars:
            sentences.append(Sentence(text=text[s:end_punct], start=s, end=end_punct))
        elif end_punct > s:
            pass                                                # too short (a stray heading/number) - dropped
        start = end_punct
    if start < len(text):
        s = start
        while s < len(text) and text[s].isspace():
            s += 1
        if len(text) - s >= min_chars:
            sentences.append(Sentence(text=text[s:], start=s, end=len(text)))
    return sentences
