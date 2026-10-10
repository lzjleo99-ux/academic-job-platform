"""InsideHigherEd (careers.insidehighered.com) and The Chronicle of Higher Education Jobs (jobs.chronicle.com),
T27 (packet 015). Both run the same underlying job-board platform (identical RSS shape and generator string,
'Inside Higher Ed Careers') - one config-driven engine, not two copies, per this project's own 'one engine,
many sites' convention (gen_wordpress.py/gen_sitemap_html.py).

LIVE-VERIFIED 2026-09-28: robots.txt on both hosts is permissive (only login-required paths disallowed:
/invalid-request/, /analytics/, */emailjob/*, */previewjob/*, real Sitemap: line present) - the /jobsrss/
feed itself is nowhere in that disallow list and returns real, large (17-19KB, ~20 items per page) RSS XML.
No detail fetch is needed or made: the feed's own <description> already carries the full ad body AND a
trailing location line - a second request would be pure waste.

Scope, disclosed honestly: this feed's own opensearch:totalResults reports 20,000+ jobs site-wide (every
role at every institution, most never academic) but this connector fetches only the FIRST page (~20 most
recent postings) per scheduled run, no pagination - the same disclosed "most-recent-N-only" limitation
CERN's own RSS connector already has (see cern.py's own docstring), not a bug. A daily re-crawl accumulates
genuinely new postings over time; a full historical backfill of a general, mostly-non-academic 20k-job board
was judged out of scope and needlessly heavy on the source for what this platform needs.

This is a GENERAL "every job at a higher-ed institution" board, not an academic-only one - live-verified
items include 'Campus Service Technician' and 'Training Specialist' alongside 'Post-Doc Research Associate'
and 'Professor' postings on the SAME feed page. The same strict academic-position gate this project already
applies to sljaka.com/nsz.py's own general boards is reused here (intl_text.classify_kind_intl already
returns 'other' for a non-academic role; no separate gate needed since, unlike nsz.py's RS-specific ad
detection, classify_kind_intl already IS the strict gate for English text).

Item title is '{Employer}: {Job Title}' (live-verified on every sampled item) - split on the FIRST ': ' only
(an employer name or job title could itself legitimately contain a colon further in). The description's own
LAST non-empty line is the location string; when it ends in a parenthesised 2-letter code (e.g. 'Benguerir,
Morocco (MA)') that IS the site's own stated ISO 3166-1 alpha-2 country code - a real, structured fact, never
guessed. Absent that, ajp.connectors.jobposting.country_from_freetext_location is tried on the whole line
(the same ambiguity-aware US-state-vs-country-code guards every other free-text-location connector already
relies on - found necessary live: 'Atlanta, Georgia' must never resolve to the country Georgia).

HigherEdJobs.com (a THIRD site named in the same packet) was found live, 2026-09-28, to answer every single
path (including its own homepage) with an Incapsula bot-challenge page ('Request unsuccessful. Incapsula
incident ID...') from this project's own vantage - directly contradicting the packet's own earlier live
check. Per R1/R5 (never bypass a challenge system, no proxy purchases), no connector was built for it; see
operator_actions.md for the escalation and pm_recon note.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from ajp.connectors.base import Connector, UrlManifest
from ajp.connectors.jobposting import _country_iso2, classify_location, country_from_freetext_location
from ajp.extract.intl_text import classify_kind_intl, position_kind_intl
from ajp.extract.text import decode_bytes, decode_entities
from ajp.extract_contacts import extract_contacts
from ajp.rawstore import put_raw

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
_LINK = re.compile(r"<link>(.*?)</link>", re.S)
_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_DESCRIPTION = re.compile(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.S)
_TRAILING_ISO2 = re.compile(r"\(([A-Z]{2})\)\s*$")


def parse_feed_items(xml_text: str) -> List[Dict[str, Any]]:
    """title/employer split, full body, and the description's own trailing location line, per item."""
    items = []
    for block in _ITEM.findall(xml_text):
        title_m, link_m = _TITLE.search(block), _LINK.search(block)
        if not (title_m and link_m):
            continue
        # T29 (packet 017, PM-verified live): decoded per-field, AFTER the XML regex parse above (never on
        # the raw block first - that would also unescape real tag/CDATA delimiters mid-parse). A real feed
        # item's own <link> literally contains '&amp;' for the query string's '&' (RSS/XML's own required
        # escaping of a bare '&'), and titles/descriptions carry the same for '&'/'"' - found live on 19
        # urls/apply_urls and 1078 text fields before this fix.
        raw_title = decode_entities(title_m.group(1).strip())
        employer, sep, job_title = raw_title.partition(": ")
        if not sep:
            employer, job_title = None, raw_title       # no colon at all - the whole title is the job title
        desc_m = _DESCRIPTION.search(block)
        desc = decode_entities(desc_m.group(1)) if desc_m else ""
        lines = [ln.strip() for ln in desc.replace("\r\n", "\n").split("\n") if ln.strip()]
        location_line = lines[-1] if lines else ""
        body = " ".join(lines[:-1]) if len(lines) > 1 else " ".join(lines)
        pub_m = _PUBDATE.search(block)
        posted = None
        if pub_m:
            try:
                posted = parsedate_to_datetime(pub_m.group(1).strip())
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                posted = None
        items.append({
            "title": job_title.strip(), "employer": (employer or "").strip() or None,
            "link": decode_entities(link_m.group(1).strip()), "location_line": location_line, "body": body,
            "posted": posted,
        })
    return items


def country_from_location_line(location_line: str) -> Optional[str]:
    """A trailing '(XX)' is the site's own stated ISO2 code (a real fact); otherwise the usual ambiguity-
    aware free-text resolution is tried on the whole line."""
    m = _TRAILING_ISO2.search(location_line)
    if m:
        code = _country_iso2(m.group(1))
        if code:
            return code
    return country_from_freetext_location(location_line)


@dataclass(frozen=True)
class JobsRssConfig:
    source_id: str
    host: str
    feed_url: str
    org_label: str


class HigherEdJobsRssConnector(Connector):
    kind = "feed"

    def __init__(self, client, store, config: JobsRssConfig):
        self.config = config
        self.source_id = config.source_id
        self.manifest = UrlManifest(re.escape(config.feed_url), rf"https://{re.escape(config.host)}/job/[^?#\s]*")
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, config: JobsRssConfig) -> str:
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes,
                                   ats_family, config)
               VALUES (%s,'feed',%s,%s,true,1.0,'INTL',%s,'higher_ed_jobsrss',%s)
               ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config, ats_family = EXCLUDED.ats_family""",
            (config.source_id, f"https://{config.host}/", config.feed_url,
             f"{config.org_label} - general higher-ed jobs RSS feed (T27); strict academic-position gate "
             "applied (a general all-roles board, not academic-only); no separate detail fetch - the feed's "
             "own <description> already carries the full body + a trailing location line.",
             Jsonb(asdict(config))),
        )
        return config.source_id

    def discover(self) -> List[Dict[str, Any]]:
        res, _ = self.fetch_raw(self.config.feed_url, kind="xml")
        return parse_feed_items(decode_bytes(res.content, res.content_type))

    def crawl(self, items: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        items = items if items is not None else self.discover()
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(items), "kept": 0, "discarded": 0}
        for it in items:
            title, body = it["title"], it["body"]
            if not title:
                continue
            kind = classify_kind_intl(title, body)
            if kind == "other":
                report["discarded"] += 1
                continue
            country = country_from_location_line(it["location_line"])
            location = classify_location(f"{title} {it['location_line']} {body}", has_country=country is not None)
            contacts = extract_contacts(text=body)
            # No separate detail fetch made (module docstring): package the feed item's own already-fetched,
            # robots-cleared bytes as this row's raw object directly - same convention as ku_dk.py/
            # gen_wordpress.py's own feed-content fallback (status=200 accurately reflects the data really
            # was obtained, just not via its own request).
            ref = put_raw(self.store, kind="xml", source_id=self.source_id, url=it["link"],
                          content=f"<item><title>{title}</title><description>{body}</description></item>".encode("utf-8"),
                          fetched_at=datetime.now(timezone.utc), status=200, content_type="text/xml",
                          robots_allowed=True)
            row: Dict[str, Any] = {
                "ext_uid": hashlib.sha1(it["link"].encode()).hexdigest()[:16], "source_id": self.source_id,
                "url": it["link"], "title_raw": title, "org_raw": it["employer"],
                "country_iso2": country, "city": it["location_line"] or None,
                "position_kind": position_kind_intl(title, body), "kind": kind,
                "ad_lang": "en", "raw_text": body[:4000], "requirements_raw": body[:4000], "ft_pt": "unknown",
                "foreigner_eligible": "unknown", "extraction_family": "higher_ed_jobsrss",
                "raw_html_ref": ref.key, "apply_url": it["link"],
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
                **contacts,
            }
            if it["posted"]:
                row["posted_at"] = it["posted"]
            rows.append(row)
            report["kept"] += 1
        return rows, report


INSIDE_HIGHER_ED = JobsRssConfig(
    source_id="insidehighered", host="careers.insidehighered.com",
    feed_url="https://careers.insidehighered.com/jobsrss/", org_label="Inside Higher Ed Careers")
CHRONICLE_JOBS = JobsRssConfig(
    source_id="chronicle_jobs", host="jobs.chronicle.com",
    feed_url="https://jobs.chronicle.com/jobsrss/", org_label="The Chronicle of Higher Education Jobs")
