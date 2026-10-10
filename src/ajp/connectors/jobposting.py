"""Shared machinery for tier-1 international aggregators that publish schema.org JobPosting JSON-LD on their
own detail pages (T13): jobs.ac.uk, Nature careers, AcademicTransfer all confirmed live to use it (2026-09-22).
One extractor + one row builder here; each site's own connector module only owns discovery (its sitemap shape)
and its own UrlManifest/exclusions - never re-implements JSON-LD parsing.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import pycountry
from selectolax.parser import HTMLParser

from ajp.connectors.base import FetchFailed
from ajp.extract.intl_text import classify_kind_intl, position_kind_intl
from ajp.extract.text import decode_bytes, decode_entities
from ajp.extract_contacts import extract_contacts

_HTML_TAG = re.compile(r"<[^>]+>")


_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def sitemap_locs(xml_text: str, default_host: str) -> List[str]:
    """Every <loc> in a sitemap (urlset or sitemapindex - the tag name doesn't matter, only <loc> does),
    normalized via normalize_sitemap_loc. Regex, not an XML parser: several of these sitemaps are served with
    the wrong content-type (application/octet-stream) which some XML parsers refuse outright; a flat <loc>
    regex needs no content-type and no namespace handling."""
    return [normalize_sitemap_loc(m, default_host) for m in _LOC.findall(xml_text)]


def fetch_sitemap_text(connector, url: str) -> str:
    """F9 (PM live audit 2026-09-24): every sitemap-index-driven connector's own discover() used to fetch a
    sitemap/shard URL and decode+parse its body as XML with NO status check at all - found live on
    academicpositions.com, whose shard URL started returning a real Cloudflare 403 'Just a moment...'
    challenge page (content-type text/html): decoded and regex-scanned for <loc> anyway, it simply matched
    zero URLs, so discover() silently returned [] every run - reported as a clean, empty 'nothing new today'
    run rather than the real, ongoing failure it was. Raises FetchFailed on anything but a real 200 so the
    scheduler's own exception-isolation + R1 3-strikes counter see it, exactly like a detail-page fetch
    failure does - never a silent 'zero found' standing in for 'could not even fetch the index'."""
    res, ref = connector.fetch_raw(url, kind="xml")
    if res.status != 200:
        raise FetchFailed(f"non-200 status {res.status} fetching sitemap {url}")
    return decode_bytes(res.content, res.content_type)


def normalize_sitemap_loc(loc: str, default_host: str, scheme: str = "https") -> str:
    """A sitemap <loc> is sometimes protocol-relative ('//host/path') or even scheme-AND-slash-free
    ('host/path', seen live on jobs.ac.uk and emploi.cnrs.fr) instead of a proper absolute URL. Never invents a
    host that isn't already present in the loc or given as ``default_host`` (the sitemap's own known host) -
    this is normalization of what the site published, not a guess at where it might live."""
    loc = loc.strip()
    if loc.startswith("//"):
        return f"{scheme}:{loc}"
    if re.match(r"^https?://", loc, re.I):
        return loc
    if loc.startswith(default_host):
        return f"{scheme}://{loc}"
    # bare path or an unexpected host - still safe to prefix, but flag via the default host requirement
    return f"{scheme}://{default_host}{loc if loc.startswith('/') else '/' + loc}"


def extract_jobposting(html: str) -> Optional[Dict[str, Any]]:
    """The first schema.org JobPosting JSON-LD block on the page - either a bare {"@type":"JobPosting",...} or
    one wrapped in a WebPage's "mainEntity" (AcademicTransfer's own shape). None if neither is present (a
    delisted/expired posting, or a page that never had one - never guessed from the surrounding HTML)."""
    tree = HTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for d in candidates:
            if not isinstance(d, dict):
                continue
            if d.get("@type") == "JobPosting":
                return d
            main = d.get("mainEntity")
            if isinstance(main, dict) and main.get("@type") == "JobPosting":
                return main
    return None


def _country_iso2(value: Optional[str]) -> Optional[str]:
    """'NL' / 'China' / 'United Kingdom' -> ISO 3166-1 alpha-2, via pycountry (a real lookup, never a guessed
    mapping). None if pycountry doesn't recognise it - the caller (jobposting_to_row) then persists the row
    with country_iso2=None and a real location_class instead (T23, PM ruling R2; country_iso2 was made
    nullable in migration 0020 for exactly this reason) - a listing with an unresolvable country is still
    never inserted with a fabricated one, it is just no longer discarded outright."""
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"[A-Za-z]{2}", value):
        try:
            return pycountry.countries.lookup(value).alpha_2
        except LookupError:
            return None
    try:
        return pycountry.countries.lookup(value).alpha_2
    except LookupError:
        return None


# Real USPS state/territory abbreviations - needed because several collide with real ISO 3166-1 country
# codes (MD=Moldova, GA=Gabon, IN=India, LA is not one but MT=Malta, PA=Panama, DE... is not either but
# several genuinely do) - found live building ats_lever.py: "Baltimore, MD" resolved to Moldova via a naive
# pycountry.lookup("MD") before this table existed. A bare 2-letter trailing token in a free-text "City, XX"
# location must be checked against this list FIRST; only once it's ruled out as a US state does a country
# code lookup on the same token become safe.
_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
})
# Full state names collide with full country names too - "Atlanta, Georgia" resolved to the COUNTRY Georgia
# (pycountry's real, unambiguous name match) before this list existed, found live 2026-09-23 crawling a real
# Greenhouse board. The best-known case, not the only structural risk this class of bug represents.
_US_STATE_NAMES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia", "puerto rico", "virgin islands",
    "guam", "american samoa", "northern mariana islands",
})


def _is_us_state_token(token: str) -> bool:
    t = token.strip()
    if re.fullmatch(r"[A-Za-z]{2}", t):
        return t.upper() in _US_STATES
    return t.lower() in _US_STATE_NAMES


def country_from_freetext_location(location: Optional[str]) -> Optional[str]:
    """For ATS free-text 'City, Region' location strings (Lever/Greenhouse - no structured address field at
    all, unlike schema.org JobPosting's jobLocation): the trailing token after the last comma, checked
    against BOTH the US state list (abbreviation AND full name) and the ISO country list. Several genuinely
    collide either way - DE=Delaware/Germany, MD=Maryland/Moldova, GA=Georgia/Gabon (abbreviations);
    Georgia=US state/Georgia the country (full name, the best-known case) - a token that matches both is left
    unresolved (None) rather than guessed either way (D6): the bare token alone cannot disambiguate 'Atlanta,
    Georgia' from a hypothetical Georgian (country) city also just called 'Georgia' for short. Only a token
    that matches EXACTLY ONE of the two lists resolves. Returns None for a bare city name, 'Remote', or
    anything else ambiguous."""
    if not location or "," not in location:
        return None
    trailing = location.rsplit(",", 1)[-1].strip()
    is_us = _is_us_state_token(trailing)
    country_code = _country_iso2(trailing)
    if is_us and country_code:
        return None                                            # genuinely ambiguous - never guessed
    return "US" if is_us else country_code


def _strip_html(text: Optional[str]) -> Optional[str]:
    return _HTML_TAG.sub(" ", text).strip() if text else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_SALARY_PERIOD = {"MONTH": "month", "YEAR": "year", "HOUR": "hour"}
_EMPLOYMENT_HOURS = {"FULL_TIME": "full", "PART_TIME": "part"}


def _salary_bound_num(v: Any) -> Optional[float]:
    """A JSON-LD salary bound's real numeric value, or None if missing/unparseable - used only to decide
    whether a bound is the F6 "placeholder zero" pattern (see jobposting_to_row), never to decide what
    actually gets stored (the original number-or-string value is always what's assigned/collapsed, so a
    string amount keeps its own precision). Plain Python truthiness (`not v`) is wrong here on its own: a
    real live payload quotes a zero bound as the STRING '"0.00"', which is non-empty and therefore truthy."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def jobposting_to_row(jp: Dict[str, Any], url: str, source_id: str, raw_ref: str,
                      html: str = "") -> Optional[Dict[str, Any]]:
    """schema.org JobPosting -> a listing row dict, D6-compliant (nullable + raw excerpt, never fabricated).
    Returns None only when there is no title at all (nothing to show). T23 (PM ruling R2): a country that
    cannot be resolved no longer discards the row - it is persisted with country_iso2=None plus a real
    location_class (located/remote/hybrid/unknown, via classify_location on the title+description) instead,
    since listing.country_iso2 was made nullable in migration 0020 for exactly this reason. D6 unchanged:
    the country itself is still never guessed - classify_location only ever reports a work-arrangement
    signal (or 'unknown'), never a country.

    T26 (packet 015): `html` is the caller's own already-fetched detail-page markup (optional - every real
    caller has it, kept optional only so existing direct unit tests that build a bare `jp` dict without ever
    fetching a page don't need to change) - mailto:/tel: links live there, not in the JSON-LD payload itself,
    so contact extraction needs it separately from `jp`."""
    title = (jp.get("title") or "").strip()
    if not title:
        return None
    locations = jp.get("jobLocation")
    locations = locations if isinstance(locations, list) else ([locations] if locations else [])
    country = None
    city = None
    for loc in locations:
        addr = (loc or {}).get("address") or {}
        country = _country_iso2(addr.get("addressCountry"))
        if country:
            city = addr.get("addressLocality")
            break

    # T29 (packet 017, PM-verified live: 1082 of 1110 real entity-affected rows across the whole DB were
    # exactly this field): a JobPosting JSON-LD payload's own "description" string is JSON text, not HTML -
    # json.loads() never decodes an HTML entity, it only knows JSON's own escapes. A real, common site-side
    # templating bug (running an HTML-escaping filter on text before embedding it in a JSON-LD block, which
    # application/ld+json content never actually needs) leaves a literal '&amp;'/'&quot;' in the STRING
    # VALUE itself - and some sources go further and HTML-escape their own tags too ('&lt;p&gt;'), so
    # decoding MUST run BEFORE _strip_html, never after: stripping first would miss an entity-encoded tag
    # entirely (nothing literal to match), and decoding afterward would then un-hide it as a REAL '<p>' left
    # sitting in the final text - found live building this exact fix's own test fixture.
    description = _strip_html(decode_entities(jp.get("description")))
    org = jp.get("hiringOrganization") or {}
    org_name = org.get("name") if isinstance(org, dict) else None

    salary = jp.get("baseSalary") or {}
    salary_value = salary.get("value") or {}
    salary_min, salary_max, salary_currency, salary_period, salary_raw = None, None, None, None, None
    if isinstance(salary_value, dict):
        lo, hi = salary_value.get("minValue"), salary_value.get("maxValue")
        # F6 (PM live audit, jobs.ac.uk, found 2026-09-24): a real live payload stated a SINGLE-value salary
        # as minValue=57986, maxValue=0 - a placeholder zero, not a genuine upper bound of 0 - which violated
        # listing_salary_range's own CHECK (min <= max) and crashed the whole ingest. Schema.org's own single-
        # value shape ('value' directly on the QuantitativeValue, no min/max at all) is handled the same way.
        # Any zero/missing bound paired with a real other bound collapses to a single value (min=max) rather
        # than being left as an invalid inverted range or silently dropped.
        #
        # F6b (found live re-crawling jobs.ac.uk again, 2026-09-28): the SAME placeholder-zero pattern also
        # shows up with the bounds QUOTED AS STRINGS ('"minValue":"500000.00","maxValue":"0.00"', a real
        # payload, not hypothetical) - '"0.00"' is a non-empty string, so plain Python truthiness (`not hi`)
        # never treated it as the placeholder F6 already knows about, and it reached the DB as a real,
        # inverted min>max range and crashed ingest exactly the same way F6 was meant to prevent. Every
        # truthiness check below now goes through _salary_bound_num (parses a numeric string too), while the
        # ORIGINAL value - number or string - is still what actually gets assigned/collapsed, so a real
        # string amount like "500000.00" is never coerced into a float and loses no precision.
        lo_n, hi_n = _salary_bound_num(lo), _salary_bound_num(hi)
        if not lo_n and not hi_n:
            single = salary_value.get("value")
            if _salary_bound_num(single):
                lo = hi = single
        elif not hi_n:
            hi = lo
        elif not lo_n:
            lo = hi
        if _salary_bound_num(lo) or _salary_bound_num(hi):
            salary_min, salary_max = lo, hi
            salary_currency = salary.get("currency")
            salary_period = _SALARY_PERIOD.get((salary_value.get("unitText") or "").upper())
            salary_raw = f"{salary_min}-{salary_max} {salary_currency or ''}/{salary_value.get('unitText') or ''}".strip()

    employment_type = jp.get("employmentType")
    if isinstance(employment_type, str) and employment_type.startswith("["):
        # Nature careers' own JSON-LD double-encodes this field as a JSON array SERIALIZED INTO A STRING
        # ('"[\\"FULL_TIME\\"]"') rather than a real array - seen live, not a hypothetical.
        try:
            employment_type = json.loads(employment_type)
        except (ValueError, TypeError):
            pass
    employment_type = employment_type[0] if isinstance(employment_type, list) else employment_type
    ft_pt = _EMPLOYMENT_HOURS.get((employment_type or "").upper(), "unknown")
    location = classify_location(f"{title} {description or ''}", has_country=country is not None)
    contacts = extract_contacts(html=html, text=description or "")

    row: Dict[str, Any] = {
        "ext_uid": hashlib.sha1(url.encode()).hexdigest()[:16],
        "source_id": source_id,
        "url": url,
        "title_raw": title,
        "org_raw": org_name,
        "country_iso2": country,
        "city": city,
        "position_kind": position_kind_intl(title, description or ""),
        "kind": classify_kind_intl(title, description or ""),
        "ad_lang": None,               # set by the caller when the source's own URL/locale names it (e.g. AT /en/ vs /nl/)
        "raw_text": description,
        "ft_pt": ft_pt,
        "foreigner_eligible": "unknown",   # never claimed without the ad text explicitly stating it (D6)
        "extraction_family": "jobposting_jsonld",
        "raw_html_ref": raw_ref,
        "location_class": location["location_class"],
        "location_class_conf": location["location_class_conf"],
        "location_class_raw": location["location_class_raw"],
        # T26 (packet 015): a real, explicit apply/contact mailto/tel link if the page had one, else the
        # ad's own canonical page - schema.org JobPosting carries no distinct 'apply here' field of its own,
        # and the ad page itself is always where a candidate would go to apply, so this is a real fact about
        # the page, never a fabricated placeholder. Guarantees every listing has >=1 clickable link.
        "apply_url": url,
        **contacts,
    }
    posted = _parse_dt(jp.get("datePosted"))
    if posted:
        row["posted_at"] = posted
    deadline = _parse_dt(jp.get("validThrough"))
    if deadline:
        row["deadline"] = deadline
    if salary_min is not None or salary_max is not None:
        row.update(salary_min=salary_min, salary_max=salary_max, salary_currency=salary_currency,
                   salary_period=salary_period, salary_raw=salary_raw)
    # same staleness/status discipline as every other connector: a stated deadline that has passed is 'expired'
    # (display only, the row is never dropped); with neither a deadline nor a posted date, staleness truly
    # cannot be judged, so status is 'unknown' rather than defaulting to 'open'.
    if deadline and deadline < datetime.now(timezone.utc):
        row["status"] = "expired"
    elif not posted and not deadline:
        row["status"] = "unknown"
    return row


# --------------------------------------------------------------------- T23 (PM ruling R2) --------------------
# EN + RS (Latin script - board.py's own connectors already run Cyrillic through rs_text's own transliteration
# before this would ever see it) remote/hybrid-work signals. Kept intentionally small and precision-first,
# same "never guess, stay unknown" design note every other classifier in this project already follows -
# a bare "remote" in an unrelated sense (e.g. "remote sensing", a real discipline name Inria/academic ads use)
# would be a false positive, so the pattern requires a work-arrangement-shaped phrase, not the bare word alone.
_REMOTE_SIGNAL = re.compile(
    r"\bfully remote\b|\bremote work\b|\bremote position\b|\bwork(?:ing)? from home\b|\bwfh\b|\btelework\w*\b|"
    r"\brad na daljinu\b|\brad od kuće\b", re.I)
_HYBRID_SIGNAL = re.compile(r"\bhybrid (work|role|position)\b|\bhibridni? rad\b", re.I)


def classify_location(text: str, *, has_country: bool) -> Dict[str, Any]:
    """listing.location_class (T23, PM ruling R2): 'hybrid'/'remote' when the ad's own text says so (a real
    span kept as location_class_raw, a fixed confidence since this is a keyword match, not a probabilistic
    score); otherwise 'located' when a country WAS resolved elsewhere (the normal case), or 'unknown' when
    neither a country nor a remote/hybrid signal exists - never a guessed country, and never silently
    dropping a real ad just because its location could not be pinned down. Checked in this order (hybrid
    first): an ad describing a hybrid arrangement often also uses the word 'remote' in passing (e.g. "hybrid
    role with some remote days"), and hybrid is the more specific, more informative fact to report."""
    m = _HYBRID_SIGNAL.search(text)
    if m:
        return {"location_class": "hybrid", "location_class_conf": 0.8, "location_class_raw": m.group(0)}
    m = _REMOTE_SIGNAL.search(text)
    if m:
        return {"location_class": "remote", "location_class_conf": 0.8, "location_class_raw": m.group(0)}
    if has_country:
        return {"location_class": "located", "location_class_conf": None, "location_class_raw": None}
    return {"location_class": "unknown", "location_class_conf": None, "location_class_raw": None}
