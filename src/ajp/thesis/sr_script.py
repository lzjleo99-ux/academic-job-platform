"""Serbian Cyrillic<->Latin transliteration (standard orthographic correspondence - one script writes the
same language, not a translation). Shared by:
  - checks.py / templates: a Cyrillic-script thesis must still match a Latin-written template's section
    keywords (found live building the Cyrillic fixture - a template.match_section_key() using Latin
    substrings against Cyrillic headings matched nothing at all, silently failing every required section).
  - T17 similarity/normalize.py: fingerprints must be script-agnostic so a Cyrillic verbatim copy of a Latin
    source (or vice versa) is still detected as verbatim, not merely 'possible paraphrase'.
"""
from __future__ import annotations

# Multi-character digraphs first (longest match wins) - order matters when building the regex-free scanner.
_CYR_TO_LAT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E", "Ж": "Ž", "З": "Z", "И": "I",
    "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj", "М": "M", "Н": "N", "Њ": "Nj", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "Ћ": "Ć", "У": "U", "Ф": "F", "Х": "H", "Ц": "C", "Ч": "Č", "Џ": "Dž", "Ш": "Š",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž", "з": "z", "и": "i",
    "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "ћ": "ć", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š",
}
_LAT_TO_CYR_DIGRAPHS = {"Lj": "Љ", "LJ": "Љ", "lj": "љ", "Nj": "Њ", "NJ": "Њ", "nj": "њ",
                        "Dž": "Џ", "DŽ": "Џ", "dž": "џ", "Dz": "Џ", "dz": "џ"}
_LAT_TO_CYR_SINGLE = {
    "A": "А", "B": "Б", "V": "В", "G": "Г", "D": "Д", "Đ": "Ђ", "E": "Е", "Ž": "Ж", "Z": "З", "I": "И",
    "J": "Ј", "K": "К", "L": "Л", "M": "М", "N": "Н", "O": "О", "P": "П", "R": "Р", "S": "С", "T": "Т",
    "Ć": "Ћ", "U": "У", "F": "Ф", "H": "Х", "C": "Ц", "Č": "Ч", "Š": "Ш",
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "đ": "ђ", "e": "е", "ž": "ж", "z": "з", "i": "и",
    "j": "ј", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т",
    "ć": "ћ", "u": "у", "f": "ф", "h": "х", "c": "ц", "č": "ч", "š": "ш",
}


def cyr_to_lat(text: str) -> str:
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def lat_to_cyr(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        two = text[i:i + 2]
        if two in _LAT_TO_CYR_DIGRAPHS:
            out.append(_LAT_TO_CYR_DIGRAPHS[two])
            i += 2
            continue
        out.append(_LAT_TO_CYR_SINGLE.get(text[i], text[i]))
        i += 1
    return "".join(out)


def to_latin_for_matching(text: str) -> str:
    """Cyrillic input -> Latin; Latin input passes through unchanged. Used wherever two strings that may be
    in either script need a script-agnostic comparison (never used to display text back to a user - display
    always keeps the document's own original script)."""
    return cyr_to_lat(text)
