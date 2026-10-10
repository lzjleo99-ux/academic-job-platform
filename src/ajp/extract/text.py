"""T29 (packet 017, PM-verified live on 4725 real listings): ONE centralized place for decoding HTML/XML
character references, reused by every connector's own row-builder and by ajp.extract_contacts - never a
second, drifting copy of this logic.

PM's own finding: src/ajp/connectors/higher_ed_jobsrss.py regex-parses RSS XML directly and never unescaped
anything, so a real `&amp;` in a feed's own `<link>`/`<title>`/`<description>` landed in the DB literally
(19 urls/apply_urls, 1078 text fields); board.py/gen_wordpress/gen_sitemap_html's own anchor-text extraction
had the same gap for `&quot;`/`&amp;` in real titles (7 titles, 1087 raw_text rows across all sources).
`ajp.extract_contacts` already had its OWN private, undocumented copy of this exact function (T26, found
live decoding a numeric-entity-obfuscated email) - that one now imports from here instead of duplicating it.

Idempotency contract (tested in tests/test_extract_text.py): `decode_entities` is safe to call more than
once on the SAME string in the ordinary case - `html.unescape` on already-plain text (no `&...;` sequences
left) is a true no-op, so a connector that decodes once at its own text-extraction boundary and then hands
the result to `extract_contacts` (which decodes again defensively) never double-corrupts anything. The one
case this does NOT cover is genuinely DOUBLE-ENCODED source text (e.g. a literal `&amp;amp;` in the feed
itself, decoding once to `&amp;` and only fully resolving on a second pass) - no real source encountered by
this project has ever done that, and this module deliberately does not loop-decode to "fix" it, since a
loop could itself mis-decode a domain name that legitimately contains an `&` followed by a real entity-
looking substring. The documented contract is therefore: **every connector's own row-builder calls this
exactly once**, at the point it pulls a text field out of raw HTML/XML, before handing that field to any
further processing - not "call it as many times as convenient and rely on idempotency to paper over it".
"""
from __future__ import annotations

import html as _html
import re
from typing import Optional, Union


def decode_entities(source: str) -> str:
    """Numeric (`&#64;`, `&#x40;`) and named (`&amp;`, `&quot;`) character references decoded via the
    standard library's own `html.unescape` - never a hand-rolled entity table. `None`/empty input passes
    through unchanged rather than raising."""
    return _html.unescape(source) if source else source


# T35a (packet 021, PM-verified live): T34's own real fix (board.py alone respecting a server's declared
# Content-Type charset) was correct but LOCAL - the identical hardcoded-utf-8 decode existed at ~20 more
# connector call sites, all sharing the same real risk (any server that declares a non-utf-8 charset, e.g.
# medf.kg.ac.rs's real windows-1251, silently corrupts every downstream text field with utf-8-replace's own
# U+FFFD). ONE shared helper now, imported everywhere, so this can never drift into N different copies again.
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([\w-]+)", re.I)


# T35b (packet 021, PM-verified defect, 24 real open rows): a real anchor's/paragraph's own captured text
# can be an ENTIRE aggregated board announcement or legal notice (PM's own real example, medf.kg.ac.rs: a
# ~10k-char Cyrillic wall covering many separate calls; also found live extending this to nsz.py's own real
# rows - a formal government ministry "ЈАВНИ КОНКУРС" notice whose "Опис послова:"/duties paragraph got
# mistaken for the position's own short title) rather than one call's own short title - never usable as a
# title regardless of whether any individual character in it is garbled. Shared here (not board.py-local)
# since T35a's own charset-decode centralization found the SAME "one connector had the real fix, others
# needed it too" pattern applies to this check as well.
_SENTENCE_BOUNDARY = re.compile(r"[.!?]\s")
MAX_TITLE_CHARS = 200


def is_oversized_or_listlike_title(title: str) -> bool:
    if len(title) > MAX_TITLE_CHARS:
        return True
    return len(_SENTENCE_BOUNDARY.findall(title)) >= 3 or title.count("\n") >= 3


def decode_bytes(body: Union[bytes, str], content_type_header: Optional[str] = None) -> str:
    """Decodes raw HTTP response bytes into text, respecting the server's own declared charset (parsed from
    its Content-Type header - 'charset=windows-1251', 'charset=latin-1', 'charset=gbk', ... case/whitespace
    tolerant) rather than always assuming utf-8. Cascade, strict-first: (1) the declared charset, if it names
    a real codec AND decodes without error; (2) utf-8, strict; (3) utf-8 with errors='replace' - ONLY as an
    absolute last resort, since that is the one path that can ever produce a lossy U+FFFD character (the
    real T34 defect this module exists to prevent: a wrong hardcoded assumption here silently corrupts every
    text field derived from it, unrecoverably once stored). Idempotent on already-decoded str input, so a
    call site that already has text (e.g. a nested parse of an already-decoded page) never needs its own
    conditional."""
    if isinstance(body, str):
        return body
    m = _CHARSET_RE.search(content_type_header or "")
    if m:
        try:
            return body.decode(m.group(1).strip())
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", "replace")
