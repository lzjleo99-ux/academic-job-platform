"""Deterministic keys for the contract's dedupe rules.

* block:        org_id + title_norm_hash            (title_hash)
* cross-source: ext_uid + url canonicalization       (canonicalize_url)
* fuzzy:        token_set_ratio + deadline + salary signature; the salary signature is a
                generated column on ``listing`` and token_set_ratio is applied to
                ``normalize_title`` output by the (later) matcher.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import List, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "_ga", "ref", "source"}
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Serbian Cyrillic -> ASCII, aligned with the diacritic-stripped Latin form below so that
# the same ad published in either script normalizes identically.
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "dj", "е": "e", "ж": "z", "з": "z",
    "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "c", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "c", "џ": "dz", "ш": "s",
}
_LAT_SPECIAL = {"đ": "dj", "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l"}


def canonicalize_url(url: str) -> str:
    """Stable form of a URL for cross-source matching (not for fetching)."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query: List[Tuple[str, str]] = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING
    )
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, host, path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKC", title).casefold()
    out = []
    for ch in s:
        if ch in _CYR:
            out.append(_CYR[ch])
        elif ch in _LAT_SPECIAL:
            out.append(_LAT_SPECIAL[ch])
        else:
            out.append(ch)
    s = unicodedata.normalize("NFKD", "".join(out))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^0-9a-z]+", " ", s)
    return s.strip()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:32]
