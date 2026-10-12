"""CNRS (emploi.cnrs.fr), T13 wave A. robots.txt (verified 2026-09-22): only /Administration/, /Candidat/ and
/Gestion/ are disallowed - /Offres/ (the job board) is fully open. Discovery via the job-shard sitemap
(OffresSiteMapProviderResult.ashx, NOT FichiersStatiques.xml which is static pages, per PM's route spec); its
own <loc> entries are host-only with no scheme (same normalization jobs.ac.uk needed, see
ajp.connectors.jobposting.normalize_sitemap_loc), and its first entry is the Offres.aspx category INDEX page,
not a posting - skipped. No schema.org JobPosting JSON-LD on this ASP.NET site (checked live), so this module
owns its own small HTML extraction, in the same style as ajp.connectors.cern. FR-language classifier
(ajp.extract.intl_text, extended this task with CNRS's own vocabulary - charge/directeur de recherche,
ingenieur, doctorant, concours).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from selectolax.parser import HTMLParser

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, UrlManifest
from ajp.connectors.jobposting import classify_location, fetch_sitemap_text, sitemap_locs
from ajp.extract.intl_text import kind_row_fields
from ajp.extract.text import decode_bytes
from ajp.extract_contacts import extract_contacts
from ajp.robots import RobotsDenied

HOST = "emploi.cnrs.fr"
SITEMAP_URL = f"https://{HOST}/Sitemaps/OffresSiteMapProviderResult.ashx"
MANIFEST = UrlManifest(
    rf"https://{HOST}/Sitemaps/OffresSiteMapProviderResult\.ashx",
    rf"https://{HOST}/Offres/[^?#\s]+/?",
)
_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_DEADLINE_FR = re.compile(
    r"date limite de candidature\s*:\s*\w+\s+(\d{1,2})\s+(" + "|".join(_MONTHS_FR) + r")\s+(\d{4})", re.I)
# CNRS's own 'Lieu de Travail NNNNN CITY' label writes the city name in ALL CAPS - each captured word must be
# WHOLLY uppercase, not just start with a capital (a negative lookahead rejects e.g. 'T' alone from 'Type' as
# a bare uppercase PREFIX of a mixed-case word), so a multi-word city ('AIX-EN-PROVENCE') is captured whole
# without also swallowing the unrelated prose that follows it in the same normalized-whitespace line.
_POSTAL_CITY = re.compile(
    r"\b\d{5}\s+([A-ZÀ-Ü][A-ZÀ-Ü'\-]*(?![a-zà-ÿ])(?:\s[A-ZÀ-Ü][A-ZÀ-Ü'\-]*(?![a-zà-ÿ]))*)")


def parse_detail(html: str) -> Dict[str, Any]:
    """title, city (from 'Lieu de Travail: NNNNN CITY'), a stated application deadline ('Date limite de
    candidature : <weekday> D month YYYY'), and the full body text for classification - all read from the
    page's own labelled fields, D6: nullable, never guessed from anything else."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style"):
        tag.decompose()
    h1 = tree.css_first("h1")
    title = h1.text().strip() if h1 else ""
    main = tree.css_first("main") or tree.css_first("#content") or tree.body
    body_text = re.sub(r"\s+", " ", main.text(separator=" ")).strip() if main else ""
    city = None
    m = _POSTAL_CITY.search(body_text)
    if m:
        city = m.group(1).strip()
    deadline = None
    m = _DEADLINE_FR.search(body_text)
    if m:
        day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
        month = _MONTHS_FR.get(month_name)
        if month:
            try:
                deadline = datetime(int(year), month, int(day), 23, 59, tzinfo=timezone.utc)
            except ValueError:
                deadline = None
    return {"title": title, "city": city, "body_text": body_text, "deadline": deadline}


class CnrsConnector(Connector):
    source_id = "cnrs"
    kind = "scrape_html"
    manifest = MANIFEST

    @staticmethod
    def ensure_source(conn) -> None:
        conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope, notes)
                        VALUES ('cnrs','scrape_html',%s,%s,true,1.0,'FR',
                                'CNRS (French national research) job board; sitemap discovery (the job-shard '
                                'ONLY, not the static-pages shard); no schema.org JSON-LD, own HTML extraction; '
                                'FR-language classifier. All postings are within France (the source itself is '
                                'France''s national research employment portal) - country_iso2 fixed FR, never '
                                'guessed per-ad, same structural-default reasoning as any single-country org.')
                        ON CONFLICT (id) DO NOTHING""", (f"https://{HOST}/", SITEMAP_URL))

    def discover(self) -> List[str]:
        locs = sitemap_locs(fetch_sitemap_text(self, SITEMAP_URL), HOST)
        return [u for u in locs if "/Offres/" in u and not u.rstrip("/").endswith("Offres.aspx")]

    def crawl(self, urls: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(urls), "parsed": 0, "kept": 0, "discarded": 0, "fetch_errors": 0}
        for url in urls:
            # F7 (PM live audit 2026-09-24): a single detail URL's fetch failing after retries must never
            # abort the whole sweep and lose every sibling row already collected.
            try:
                res, ref = self.fetch_raw(url)
            except (FetchFailed, ManifestViolation, RobotsDenied):
                report["fetch_errors"] += 1
                continue
            if res.status != 200:
                continue
            html = decode_bytes(res.content, res.content_type)
            detail = parse_detail(html)
            if not detail["title"]:
                continue
            report["parsed"] += 1
            kind_f = kind_row_fields(detail["title"], detail["body_text"])
            kind = kind_f["kind"]
            location = classify_location(f"{detail['title']} {detail['body_text']}", has_country=True)
            contacts = extract_contacts(html=html, text=detail["body_text"])
            row: Dict[str, Any] = {
                "ext_uid": hashlib.sha1(res.final_url.encode()).hexdigest()[:16], "source_id": self.source_id,
                "url": res.final_url, "title_raw": detail["title"], "org_raw": "CNRS",
                "country_iso2": "FR", "city": detail["city"],
                **kind_f,
                "ad_lang": "fr", "raw_text": detail["body_text"][:2000], "ft_pt": "unknown",
                "foreigner_eligible": "unknown", "extraction_family": "cnrs_html", "raw_html_ref": ref.key,
                "status": "unknown", "apply_url": res.final_url, **contacts,
                "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
                "location_class_raw": location["location_class_raw"],
            }
            if detail["deadline"]:
                row["deadline"] = detail["deadline"]
                row["status"] = "expired" if detail["deadline"] < datetime.now(timezone.utc) else "open"
            rows.append(row)
            if kind in ("employment", "grant_call", "internship", "enrollment"):
                report["kept"] += 1
            else:
                report["discarded"] += 1
        if report["attempted"] > 0 and report["fetch_errors"] == report["attempted"]:
            raise FetchFailed(f"all {report['attempted']} detail fetches failed for {self.source_id}")
        return rows, report
