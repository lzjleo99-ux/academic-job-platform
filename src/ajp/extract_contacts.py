"""T26 (packet 015): a shared contact extractor reused by every connector's own row builder (the jobposting.py
root shared by 4 aggregators, and every bespoke connector's own detail parse). D6 unchanged: only a contact
that is REALLY present on the page - a real mailto:/tel: link, or a real, precision-first pattern match in the
ad's own text - is ever returned; nothing here infers or guesses a contact that isn't actually stated.

Found live (PM's own audit, packet 015): every connector already had contact_emails/contact_phones/
contact_name/contact_scope columns available in the schema, but ZERO of them ever populated it - this module
closes that gap in one place instead of once per connector, each with its own copy-pasted (and likely
drifting) regex.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ajp.extract.text import decode_entities as _decode_entities

# (T26, found live: '&#64;' decimal-encoding '@', and a full email individually numeric-entity-encoded
# character-by-character - a common anti-scraper obfuscation on WordPress-family sites, neither of which any
# regex below can see through unless decoded first. T29: this used to be its own private copy of the exact
# same function - centralized into ajp.extract.text so every connector and this module share one
# implementation instead of two that could quietly drift apart; see that module's own docstring for the
# idempotency contract.)

# ---------------------------------------------------------------- emails -----------------------------------
_MAILTO = re.compile(r'href\s*=\s*["\']mailto:([^"\'?]+)', re.IGNORECASE)
_PLAIN_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# Asset/file extensions that would otherwise false-match the plain email regex against markup like
# 'photo@2x.png' (a real srcset attribute shape, found live building this module against real HTML) - '@2x'
# is not a local-part, 'png' is not a TLD. A denylist of known non-email suffixes is far safer than trying to
# enumerate every real TLD (which keeps growing and is never this module's job to maintain).
_NOT_AN_EMAIL_SUFFIX = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|css|js|woff2?|ttf|eot|mp4|pdf)$", re.IGNORECASE)

# Obfuscation: 'name [at] domain [dot] com', 'name(at)domain(dot)com', 'name{at}domain{dot}com', and the
# same with bare 'at'/'AT'/'dot'/'DOT' surrounded by spaces (packet 015's own examples). The bare-word forms
# are only matched as part of this FULL shape (local-part, then 'at', then domain, then 'dot', then a final
# segment) - a sentence like 'meet me at the office' never matches because there is no following 'dot'
# segment at all, so the bare word 'at' alone is not enough to false-trigger this.
_AT_TOKEN = r"(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\bat\b)"
_DOT_TOKEN = r"(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\bdot\b)"
_OBFUSCATED_EMAIL = re.compile(
    rf"([\w.+-]+)\s*{_AT_TOKEN}\s*([\w-]+(?:\s*{_DOT_TOKEN}\s*[\w-]+)+)", re.IGNORECASE)


def _deobfuscate_domain(raw_domain: str) -> str:
    return re.sub(rf"\s*{_DOT_TOKEN}\s*", ".", raw_domain, flags=re.IGNORECASE)


def extract_emails(html: str = "", text: str = "") -> List[str]:
    """Every real email address found - mailto: links first (unambiguous), then plain addresses, then
    written-out obfuscated forms - in that order, deduplicated (case-insensitive) preserving first-seen
    casing. `html` and `text` are both optional; either can be empty."""
    found: List[str] = []
    seen: set = set()

    def _add(addr: str) -> None:
        addr = addr.strip().rstrip(".,;:")
        if not addr:
            return
        # Found live (2026-09-28): a mailto: href's own captured content is the SOURCE SITE's raw markup,
        # not something this module built - and real sites have shipped broken hrefs like
        # 'mailto:Mailto:' (no real address at all) and 'mailto:https://career.admo.um.edu.mo/' (their own
        # website URL pasted where an email belongs), plus one real case with genuine surrounding text
        # glued on ('mailto:team at talentacquisition@swin.edu.au '). Rather than trust that capture at
        # face value, only a candidate that already IS a clean, validated email is kept as-is; otherwise a
        # real address is salvaged from inside it if one is actually present, and the whole candidate is
        # dropped if not (never inventing one that isn't really there).
        if not _PLAIN_EMAIL.fullmatch(addr):
            salvaged = _PLAIN_EMAIL.search(addr)
            if not salvaged:
                return
            addr = salvaged.group(0)
        if _NOT_AN_EMAIL_SUFFIX.search(addr):
            return
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            found.append(addr)

    for source in (html, text):
        source = _decode_entities(source)
        if not source:
            continue
        # T30: an inline SVG icon-sprite's own path coordinate data is digit/dot garbage, never an email -
        # harmless here in practice (no '@' inside it), but stripped for the same shared reason phones are:
        # one noise-removal step every contact field benefits from, not a second copy per field.
        source = _strip_noise_for_contact_scanning(source)
        for m in _MAILTO.finditer(source):
            _add(m.group(1))
        for m in _PLAIN_EMAIL.finditer(source):
            _add(m.group(0))
        for m in _OBFUSCATED_EMAIL.finditer(source):
            local, domain = m.group(1), _deobfuscate_domain(m.group(2))
            _add(f"{local}@{domain}")
    return found


# ---------------------------------------------------------------- phones -----------------------------------
_TEL_HREF = re.compile(r'href\s*=\s*["\']tel:([^"\']+)', re.IGNORECASE)
# International (+CC ...) and RS/European local grouped forms (0XX/XXX-XXXX, 0XX XXX XXXX, ...) - a bare run
# of digits with no leading '+'/'0' or no grouping punctuation at all is never matched (too easily a postal
# code, a year, or an unrelated ID number seen in the same body text - the same false-positive risk this
# project's own country_from_freetext_location already guards against for a different field).
# T30 (packet 019, PM case 2, found live on jobs_ac_uk/theunijobs/nature_careers real rows): the FIRST
# digit-group used to be capped at exactly {1,4} with a MANDATORY leading delimiter for every group after it -
# so a real number with no internal punctuation at all ('+357 25002210', a single 8-digit blob after the CC)
# was truncated to just the first 4 digits ('+357 2500'), since nothing after position 4 looked like
# '[\s./-]\d{2,4}' with no delimiter present to match. Every digit group's own leading delimiter is now
# OPTIONAL (matches PM's 4 real named examples: '+357 25002210', '+357-22841533', '+43 1 47654 85501',
# '+358 294140338' - each now captured in full, verified in this module's own tests) - a zero-width delimiter
# between two adjacent groups still lets the whole contiguous digit run be consumed as several 1-4-digit
# groups back to back, while a genuine word boundary (2+ spaces, sentence punctuation, a non-delimiter
# letter) still has no delimiter char to match at all and correctly stops the run there.
_INTL_PHONE = re.compile(r"\+\d{1,3}(?:[\s./-]?\(?\d{1,4}\)?){1,6}")
_RS_LOCAL_PHONE = re.compile(r"\b0\d{1,2}[\s./-]\d{2,4}(?:[\s./-]\d{2,4}){1,3}\b")

# T30 (packet 019, PM case 2): a handful of short country codes can still pass the generic >=7-total-digits
# floor even when truncated one group short (e.g. '+357 2500' is itself already 7 digits: 357+2500) - these
# 3 are PM's own named real examples; each requires a longer real national number than that. Not an attempt
# at a full E.164-per-country table (this project never guesses a country from a phone number either) - just
# a targeted backstop for the exact shapes PM's own live audit found actually occurring.
_MIN_TOTAL_DIGITS_BY_CC = {"357": 11, "43": 10, "358": 10}

# T30 (packet 019, PM case 1 - HIGHEST IMPACT, 1988 rows on the Madgex platform/nature_careers+theunijobs):
# an inline SVG icon-sprite's own <path d="..."> coordinate data is plain decimal-number-and-dot garbage
# ('007.792.301', '064.251.08.377', '04.128.04.264', '077.216.179.281' - PM's own real captured examples)
# that a punctuation-tolerant phone regex cannot tell apart from a genuine grouped local number. Removed
# wholesale, contents and all, before any phone/email regex ever runs - PM's own fix instruction says both
# the html path (where this is actually found, via jobposting.py's extract_contacts(html=html, ...) call)
# and defensively the text path too (in case some connector's own upstream HTML-to-text step ever left a
# malformed/unclosed svg fragment behind - this module is the one place every connector's contact extraction
# shares, so it is the one place this is worth guarding regardless of which side introduced it).
_NOISE_BLOCK = re.compile(r"<(script|style|svg)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Defensive insurance named in the same PM fix instruction: a lone/self-closing <path d="..."/> that
# survived outside any matched <svg>...</svg> pair (e.g. a cruder earlier HTML-stripping pass that removed
# the outer svg tag but not its nested path children) is removed on its own terms too.
_SVG_PATH_FRAGMENT = re.compile(r"<path\b[^>]*/?>", re.IGNORECASE)


def _strip_noise_for_contact_scanning(source: str) -> str:
    return _SVG_PATH_FRAGMENT.sub(" ", _NOISE_BLOCK.sub(" ", source))


# T30 (packet 019, PM case 3, Inria bibliographies): a DOI's own date-encoded tail ('...q-2021-03-25-421')
# is indistinguishable from _RS_LOCAL_PHONE's own grouped-digit shape on punctuation alone - the phone-shaped
# regex only ever matches starting at its own leading '0' ('03-25-421'), so what precedes that '0' in the
# real DOI is always a plain 4-digit year plus a dash ('...2021-'), regardless of the 'q-'/'10.xxxx/' prefix
# further back. Two independent defenses, per PM's own fix instruction: (1) strip URLs/DOIs/arXiv ids
# wholesale before phone scanning at all (below), and (2) as a backstop, reject a token immediately preceded
# by a bare 4-digit-year-and-dash even if something upstream ever left a bare DOI-shaped fragment un-stripped.
_URL_OR_DOI_OR_ARXIV = re.compile(
    r"https?://\S+|(?:doi\s*:\s*)?10\.\d{4,9}/\S+|arxiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.IGNORECASE)
_DATE_PREFIX_BEFORE_TOKEN = re.compile(r"\d{4}-\s*$")


def _strip_urls_and_dois(source: str) -> str:
    return _URL_OR_DOI_OR_ARXIV.sub(" ", source)


# T30 (packet 019, PM case 4, Serbian NSZ/faculty PDF decision-document numbers): 'бр. III-01-261/12 од
# 26.03.2026. године' - a legal reference number ('01-261/12') matches _RS_LOCAL_PHONE's own shape exactly.
# Real phone numbers on these same sites are never written with a trailing '/NN' 1-2-digit suffix right
# after a document-reference marker like this - PM's own fix instruction lists the exact markers to check
# for within the ~12 characters immediately before the match.
_RS_DOC_REF_MARKERS = ("бр.", "бр", "број", "No", "br.", "I-", "II-", "III-", "IV-")
# T31 (packet 019), found live building this exact fixture: a real faculty document's own letterhead address
# line ('Каменичка бр. 6.') states an UNRELATED street number using the identical 'бр.' marker, immediately
# followed (a different sentence, after its own period and a line break) by a real phone number that
# happens to contain a slash ('Тел. 069/80-66-390') - genuinely dropped by this rule at first, a real
# regression. The marker only counts when nothing between it and the token itself closes off its own
# clause/line first (a period or a newline) - PM's own real named example ('бр. III-01-261/12') has the
# marker sitting directly, contiguously in front of the reference number with neither in between.
def _preceded_by_rs_doc_ref_marker(context_before: str) -> bool:
    for marker in _RS_DOC_REF_MARKERS:
        idx = context_before.rfind(marker)
        if idx != -1 and not re.search(r"[.\n]", context_before[idx + len(marker):]):
            return True
    return False


def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _looks_like_a_calendar_date(raw: str) -> bool:
    """True for a DD/MM/YYYY or YYYY/MM/DD calendar date - found live (a real CNRS ad's own stated
    application deadline, '04/01/2027') matching _RS_LOCAL_PHONE's own grouped-digit shape ('0XX/XXX-XXXX')
    exactly, since a zero-padded day/month plus a 4-digit year is indistinguishable from that pattern on
    punctuation alone. A real RS local number this pattern is meant to catch essentially never ends (or
    starts) in a 4-digit group that also happens to read as a plausible calendar year with the other two
    groups both <=31/<=12 - an acceptable trade-off given the alternative is silently storing a date as a
    contact phone number."""
    groups = re.split(r"[\s./-]", raw.strip())
    if len(groups) != 3 or not all(g.isdigit() for g in groups):
        return False
    if len(groups[-1]) == 4:
        year, rest = int(groups[-1]), groups[:2]
    elif len(groups[0]) == 4:
        year, rest = int(groups[0]), groups[1:]
    else:
        return False
    if not (1900 <= year <= 2100):
        return False
    a, b = int(rest[0]), int(rest[1])
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= a <= 12 and 1 <= b <= 31)


def extract_phones(html: str = "", text: str = "") -> List[str]:
    """Every real phone number found - tel: links first (unambiguous, kept exactly as linked), then a real
    international or RS/European grouped-local pattern in the text. Deduplicated by digits-only key so
    '011/123-4567' and '011 123 4567' (the same number, differently punctuated on two different pages) are
    never reported as two contacts."""
    found: List[str] = []
    seen: set = set()

    def _add(raw: str) -> None:
        raw = raw.strip()
        digits = re.sub(r"\D", "", raw)
        if not (7 <= len(digits) <= 15):
            return
        if _looks_like_a_calendar_date(raw):
            return
        # T30 (packet 019, PM case 2): a targeted backstop for the exact short-country-code shapes PM's own
        # live audit found still passing the generic 7-digit floor even when truncated one group short.
        cc_match = re.match(r"\+(\d{1,3})", raw)
        if cc_match:
            min_total = _MIN_TOTAL_DIGITS_BY_CC.get(cc_match.group(1))
            if min_total and len(digits) < min_total:
                return
        if digits not in seen:
            seen.add(digits)
            found.append(raw)

    for source in (html, text):
        source = _decode_entities(source)
        if not source:
            continue
        # T30 case 1 (highest impact): remove SVG/script/style noise before any phone regex runs at all.
        source = _strip_noise_for_contact_scanning(source)
        for m in _TEL_HREF.finditer(source):
            _add(m.group(1))
        # T30 case 3: URLs/DOIs/arXiv ids are stripped before the digit-group regexes run - never before the
        # tel: href pass above, since a real tel: link's own href value must never be touched by this.
        digit_scan_source = _strip_urls_and_dois(source)
        for m in _INTL_PHONE.finditer(digit_scan_source):
            _add(m.group(0))
        for m in _RS_LOCAL_PHONE.finditer(digit_scan_source):
            token = m.group(0)
            context_before = digit_scan_source[max(0, m.start() - 12):m.start()]
            # T30 case 3 backstop: a bare DOI-shaped date tail that survived the URL/DOI strip above.
            if _DATE_PREFIX_BEFORE_TOKEN.search(context_before):
                continue
            # T30 case 4: a Serbian legal/decision document reference number, never a phone - only tokens
            # with a slash (this project's own document-reference shape, 'NN-NNN/NN') are ever at risk here.
            if "/" in token and _preceded_by_rs_doc_ref_marker(context_before):
                continue
            _add(token)
    return found


# ---------------------------------------------------------------- contact name/scope -------------------------
# Only an explicit, labelled 'Contact: NAME' / 'Kontakt: NAME' style line - never a name inferred from free
# prose (D6; also matches this project's own person-specific-notice convention of acting on a real TYPE of
# label, never a guessed name). A generic HR/personnel-department label (no personal name at all) is
# reported as contact_scope='org_hr' with no contact_name; a labelled personal name is 'ad' (this specific
# ad's own stated contact), matching listing.contact_scope's own CHECK('ad', 'org_hr').
_HR_LABEL = re.compile(
    r"\b(?:hr\s*department|human\s*resources?|kadrovska\s*služba|personalna\s*služba)\b", re.IGNORECASE)
# Found live (real 'Contact:'-labelled ads, 2026-09-28): compiling this WHOLE pattern with re.IGNORECASE
# also applied IGNORECASE to the name-capture group itself, so '[A-ZŠĐČĆŽ]' silently matched a lowercase
# letter too - the "must actually be capitalized" requirement was never really enforced. Real, messy ad text
# then let this match run on: past a blank line into unrelated trailing text ('Alexander Bismarck \n\n
# alexander', a signature block's own lowercase repeat of the first name), through a lowercase word right
# after the label ('Amber Smith at amber'), and across a page break into the next unrelated label ('Chang
# Liu \n\n Tel'). Fixed two ways: (?i:...) scopes IGNORECASE to the label alternation ONLY, so the name
# tokens now genuinely require a real capital letter; and the name tokens' own separator is now spaces/tabs
# only (never a newline), so a match can never continue past the end of the line the label was found on.
_NAMED_CONTACT = re.compile(
    r"\b(?i:contact(?:\s*(?:person|name))?|kontakt(?:\s*osoba)?)\s*:[ \t]*\n?[ \t]*([A-ZŠĐČĆŽ][\wÀ-ſ'-]*"
    r"(?:[ \t]+[A-ZŠĐČĆŽ][\wÀ-ſ'-]*){0,3})")
# A handful of common single-word field labels/placeholders that are still syntactically capitalized (so
# the pattern above alone cannot rule them out) but are never themselves a person's name - found live as a
# literal captured "name": 'E-mail', 'Tel', an unfilled template placeholder ('Contact name:' with nothing
# real filled in after it).
_NOT_A_NAME = {"e-mail", "email", "tel", "phone", "fax", "address", "name", "contact", "n/a", "tbd", "dr"}


def extract_contact_name_and_scope(text: str) -> Dict[str, Optional[str]]:
    if not text:
        return {"contact_name": None, "contact_scope": None}
    text = _decode_entities(text)
    m = _NAMED_CONTACT.search(text)
    if m:
        name = m.group(1).strip()
        # the label matched case-insensitively but the captured NAME group itself still requires a real
        # capital-letter start per the pattern - a generic HR label alone ('Contact: HR Department') would
        # otherwise be captured as if it were a person's name.
        if not _HR_LABEL.search(name) and name.lower() not in _NOT_A_NAME:
            return {"contact_name": name, "contact_scope": "ad"}
    if _HR_LABEL.search(text):
        return {"contact_name": None, "contact_scope": "org_hr"}
    return {"contact_name": None, "contact_scope": None}


# ---------------------------------------------------------------- everything at once --------------------------
def extract_contacts(html: str = "", text: str = "") -> Dict[str, Any]:
    """The one function every connector's row builder calls. Returns exactly the 4 ajp.ingest.LISTING_COLUMNS
    contact fields, ready to `row.update(**extract_contacts(...))` - empty list/None when nothing was found,
    never a fabricated placeholder."""
    result: Dict[str, Any] = {
        "contact_emails": extract_emails(html, text),
        "contact_phones": extract_phones(html, text),
    }
    result.update(extract_contact_name_and_scope(text or html))
    return result
