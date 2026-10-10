"""EURAXESS job-offer detail HTML -> data_contract.listing row.

DORMANT by design: the EURAXESS robots.txt forbids fetching its job pages, so nothing in this
project retrieves them (see ajp/channels/euraxess.py). This mapping activates only if a licensed
feed / written permission later delivers such HTML (packet 003 operator action) and a non-channel
source (default id ``euraxess_feed``) is registered. It is specified by the PM's packet-002 field
map and tested exclusively against synthetic fixtures.

The <dt>/<dd> pairs are read generically (repeated terms keep every value, in order). Values that
the listing contract has no column for are returned in ``extras`` rather than dropped or invented.
D6: anything not stated stays None; no duration, salary or eligibility is inferred beyond the
explicit rules below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pycountry
from selectolax.parser import HTMLParser

from ajp.extract.salary import extract_salary

DEFAULT_SOURCE_ID = "euraxess_feed"


class NormalizeError(ValueError):
    pass


@dataclass
class Normalized:
    row: Dict[str, Any]
    extras: Dict[str, Any] = field(default_factory=dict)
    fields: Dict[str, List[str]] = field(default_factory=dict)  # raw dt -> dd values, for audit


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def parse_definition_list(html: str) -> Dict[str, List[str]]:
    """{normalized term: [values in document order]} from every dt/dd pair on the page."""
    tree = HTMLParser(html)
    out: Dict[str, List[str]] = {}
    term: Optional[str] = None
    # NB: css("dt, dd") groups all dt before all dd; traverse() keeps document order
    for node in (n for n in tree.root.traverse() if n.tag in ("dt", "dd")):
        if node.tag == "dt":
            term = _clean(node.text(separator=" ")).rstrip(":").lower() or None
        elif term:
            items = [_clean(li.text(separator=" ")) for li in node.css("li")]
            vals = [v for v in items if v] or ([_clean(node.text(separator=" "))] if _clean(node.text()) else [])
            out.setdefault(term, []).extend(vals)
    return out


def _first(f: Dict[str, List[str]], *keys: str) -> Optional[str]:
    for k in keys:
        if f.get(k):
            return f[k][0]
    return None


_MONTHS = "%d %B %Y"
_DEADLINE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?\s*(?:\(([^)]+)\))?")


def parse_deadline(raw: str) -> Optional[Dict[str, Any]]:
    m = _DEADLINE.search(raw)
    if not m:
        return None
    day, mon, year, hh, mm, tzname = m.groups()
    try:
        d = datetime.strptime(f"{day} {mon} {year}", _MONTHS) if len(mon) > 3 else datetime.strptime(f"{day} {mon} {year}", "%d %b %Y")
    except ValueError:
        return None
    tz_assumed = False
    tz = timezone.utc
    if tzname:
        try:
            tz = ZoneInfo(tzname.strip())
        except (ZoneInfoNotFoundError, ValueError):
            tz_assumed = True
    else:
        tz_assumed = True
    when = d.replace(hour=int(hh) if hh else 23, minute=int(mm) if mm else 59, second=0 if hh else 59, tzinfo=tz)
    return {"deadline": when.astimezone(timezone.utc), "tz_assumed": tz_assumed, "time_assumed": hh is None}


def derive_status(deadline: Optional[datetime], now: datetime) -> str:
    """Listing presence never proves an ad is open: a passed deadline means 'expired'.
    No deadline stated -> 'open' (the contract has no 'unknown'; last_seen expiry handles the rest)."""
    return "expired" if deadline is not None and deadline <= now else "open"


# postdoc must be tested before phd: "Postdoctoral" contains "doctoral"
_POSITION_RULES = [("postdoc", r"post-?doc"), ("phd", r"phd|doctoral|doctorate"), ("professor", r"professor|chair"),
                   ("lecturer", r"lecturer"), ("technician", r"technician"), ("intern", r"intern"),
                   ("research_assistant", r"research assistant")]
_DEGREE_RULES = [("phd", r"phd|doctorate|doctoral"), ("msc", r"master"), ("bsc", r"bachelor")]
_ELIGIBLE_YES = re.compile(r"any nationalit|all nationalit|regardless of (?:their )?(?:nationality|citizenship)|"
                           r"international (?:applicants|candidates)", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _first_match(rules, text: str) -> Optional[str]:
    for value, rx in rules:
        if re.search(rx, text, re.I):
            return value
    return None


def _language_entries(f: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    names = f.get("languages") or f.get("language") or []
    levels = f.get("language level") or f.get("level") or []
    out = []
    for i, name in enumerate(names):
        try:
            code = pycountry.languages.lookup(name).alpha_2
        except (LookupError, AttributeError):
            code = name.lower()
        out.append({"lang": code, "level": levels[i] if i < len(levels) else None, "required": None})
    return out


def normalize_detail(
    html: str, *, ext_uid: str, url: str, raw_html_ref: str, source_id: str = DEFAULT_SOURCE_ID,
    now: Optional[datetime] = None, title: Optional[str] = None, posted_at: Optional[datetime] = None,
) -> Normalized:
    now = now or datetime.now(timezone.utc)
    tree = HTMLParser(html)
    f = parse_definition_list(html)
    extras: Dict[str, Any] = {}

    title = title or (tree.css_first("h1").text(strip=True) if tree.css_first("h1") else None)
    if not title:
        raise NormalizeError("no title")
    country_raw = _first(f, "country")
    try:
        country = pycountry.countries.lookup(country_raw).alpha_2 if country_raw else None
    except LookupError:
        country = None
    if not country:
        raise NormalizeError(f"country missing or unmappable: {country_raw!r}")

    body_node = tree.css_first("main") or tree.body
    body_text = _clean(body_node.text(separator=" ")) if body_node else ""
    ad_text = body_text

    dl = parse_deadline(_first(f, "application deadline") or "")
    deadline = dl["deadline"] if dl else None
    if dl and (dl["tz_assumed"] or dl["time_assumed"]):
        extras["deadline_assumptions"] = {"tz_assumed_utc": dl["tz_assumed"], "end_of_day_assumed": dl["time_assumed"]}

    contract_raw = (_first(f, "type of contract") or "").lower()
    status_raw = (_first(f, "job status") or "").lower()
    ft = "both" if "full" in status_raw and "part" in status_raw else "full" if "full" in status_raw else \
         "part" if "part" in status_raw else "unknown"

    edu = {_first_match(_DEGREE_RULES, v) for v in f.get("education level", [])} - {None}
    degree = next(iter(edu)) if len(edu) == 1 else ("unknown" if len(edu) > 1 else None)

    emails = list(dict.fromkeys(_EMAIL.findall(" ".join(f.get("e-mail", []) + f.get("email", [])))))
    positions = " ".join(f.get("positions", []))
    salary = extract_salary(ad_text)

    row: Dict[str, Any] = {
        "ext_uid": ext_uid, "source_id": source_id, "url": url, "title_raw": title,
        "org_raw": _first(f, "organisation/company", "company/institute", "organisation"),
        "country_iso2": country, "city": _first(f, "city"),
        "disciplines_euroscivoc": list(dict.fromkeys(f.get("research field", []))),
        "position_kind": _first_match(_POSITION_RULES, positions or title) or "other",
        "contract": "fixed" if "temporary" in contract_raw else "permanent" if "permanent" in contract_raw else None,
        "ft_pt": ft, "degree_required": degree, "language": _language_entries(f),
        "foreigner_eligible": "yes" if _ELIGIBLE_YES.search(ad_text) else "unknown",
        "contact_emails": emails, "contact_scope": "ad" if emails else None,
        "deadline": deadline, "posted_at": posted_at, "status": derive_status(deadline, now),
        "ad_lang": (tree.css_first("html").attributes.get("lang") if tree.css_first("html") else None),
        "raw_text": ad_text or None, "raw_html_ref": raw_html_ref,
    }
    if salary:
        row.update(salary_min=salary.min, salary_max=salary.max, salary_currency=salary.currency,
                   salary_period=salary.period, salary_gross_net=salary.gross_net, salary_conf=salary.conf,
                   salary_raw=salary.raw)
    row = {k: v for k, v in row.items() if v is not None or k in ("org_raw",)}

    for key, name in (("hours per week", "hours_per_week"), ("offer starting date", "offer_starting_date"),
                      ("department", "department"), ("website", "website"), ("number of offers available", "n_offers"),
                      ("researcher profile", "researcher_profile"), ("postal code", "postal_code"), ("street", "street")):
        if f.get(key):
            extras[name] = f[key] if len(f[key]) > 1 else f[key][0]
    for k, v in f.items():
        if "grant agreement" in k or "framework programme" in k or "research infrastructure" in k:
            extras[k] = v if len(v) > 1 else v[0]
    return Normalized(row=row, extras=extras, fields=f)


def coverage(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Field-coverage stats for a batch: how many rows carry each nullable field."""
    keys = ["org_raw", "city", "disciplines_euroscivoc", "contract", "degree_required", "language",
            "contact_emails", "deadline", "posted_at", "salary_min"]
    n = len(rows)
    return {"rows": n, **{k: sum(1 for r in rows if r.get(k) not in (None, [], "")) for k in keys}}
