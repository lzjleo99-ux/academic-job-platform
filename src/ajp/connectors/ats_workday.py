"""T18 ats_workday_cxs: Workday's CXS JSON API. Verified live 2026-09-23 for EMBL:

    https://www.embl.org/jobs/ -> 302 -> https://embl.wd103.myworkdayjobs.com/EMBL
    POST https://embl.wd103.myworkdayjobs.com/wday/cxs/embl/EMBL/jobs
         body {"appliedFacets":{},"limit":20,"offset":0,"searchText":""}
         -> 200 {"total":25,"jobPostings":[{"title","externalPath","locationsText","postedOn","bulletFields"}]}
    GET  https://embl.wd103.myworkdayjobs.com/wday/cxs/embl/EMBL/job{externalPath}
         -> 200 {"jobPostingInfo":{"title","jobDescription","location","country":{"alpha2Code"},"timeType",
                  "startDate","endDate","externalUrl","jobPostingId","jobReqId"}}

Packet 011's own hard rule: NEVER guess {tenant}/{wdN}/{site} - guessed slugs for a dozen real
universities all 422'd or 404'd. The only legitimate way to a Workday site is following the organisation's
OWN careers-page redirect (discover_workday_site) or reading a myworkdayjobs.com link the org itself
published (ats_detect.py). Workday's own host robots.txt (verified live for embl.wd103.myworkdayjobs.com:
'Allow: /EMBL/, Disallow: /refreshFacet/') never mentions /wday/ at all - RFC 9309's default-allow for an
unmatched path is what permits the CXS calls below, not an assumption.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from ajp.connectors.base import Connector, FetchFailed, ManifestViolation, ObjectStore, PoliteClient, UrlManifest
from ajp.connectors.jobposting import _country_iso2, _parse_dt, _strip_html, classify_location
from ajp.extract_contacts import extract_contacts
from ajp.extract.intl_text import kind_row_fields
from ajp.extract.text import decode_entities
from ajp.rawstore import put_raw
from ajp.robots import RobotsDenied

WORKDAY_HOST_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.IGNORECASE)
_REDIRECT_TARGET_PATTERN = r"https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\s]*"
PAGE_SIZE = 20
MAX_PAGES = 100     # hard cap: 2000 postings - no real academic org's Workday site approaches this


@dataclass(frozen=True)
class WorkdaySite:
    tenant: str
    wd_host: str        # e.g. "wd103"
    site: str

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.wd_host}.myworkdayjobs.com"

    @property
    def cxs_base(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @property
    def public_base(self) -> str:
        return f"https://{self.host}/{self.site}"

    @property
    def source_id(self) -> str:
        return f"ats_workday_cxs__{self.tenant}_{self.site}".lower()


def discover_workday_site(client: PoliteClient, careers_url: str) -> Optional[WorkdaySite]:
    """Follows the org's OWN careers-page redirect looking for a myworkdayjobs.com destination. Returns
    None - never a guess - if the page does not redirect there (or errors, or robots denies it)."""
    manifest = UrlManifest(re.escape(careers_url), _REDIRECT_TARGET_PATTERN)
    try:
        res = client.get(careers_url, manifest=manifest)
    except (FetchFailed, ManifestViolation, RobotsDenied):
        return None
    m = WORKDAY_HOST_RE.search(res.final_url)
    if not m:
        return None
    tenant, wd, site = m.groups()
    return WorkdaySite(tenant=tenant.lower(), wd_host=wd.lower(), site=site)


def cxs_manifest(site: WorkdaySite) -> UrlManifest:
    base = re.escape(site.cxs_base)
    return UrlManifest(rf"{base}/jobs", rf"{base}/job/[^\s]*")


class WorkdayCxsConnector(Connector):
    kind = "api"

    def __init__(self, client: PoliteClient, store: ObjectStore, site: WorkdaySite,
                org_id: Optional[str] = None, org_raw: Optional[str] = None):
        self.site = site
        self.source_id = site.source_id
        self.manifest = cxs_manifest(site)
        self.org_id, self.org_raw = org_id, org_raw
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, site: WorkdaySite) -> str:
        # T21: ajp.scheduler.build_connector reconstructs a WorkdaySite straight from ats_tenant/ats_site
        # (already columns) plus config.wd_host - the ONE piece of this dataclass that has no column of its
        # own, so it must round-trip through config or the factory can never rebuild the real site. Found
        # live (the first real ajp-run) that gen_wordpress had the exact same missing-config-write bug;
        # fixed here alongside it before it was ever hit for this family too.
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope,
                                   ats_family, ats_tenant, ats_site, config, notes)
               VALUES (%s,'api',%s,%s,true,1.0,'INTL','ats_workday_cxs',%s,%s,%s,
                       'Workday CXS - tenant/site discovered from the org''s own careers-page redirect, never guessed')
               ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config""",
            (site.source_id, site.public_base, site.cxs_base + "/jobs", site.tenant, site.site,
             Jsonb({"wd_host": site.wd_host})),
        )
        return site.source_id

    def _post(self, url: str, body: dict):
        res = self.client.post(url, body, manifest=self.manifest)
        ref = put_raw(self.store, kind="json", source_id=self.source_id, url=url, content=res.content,
                      fetched_at=res.fetched_at, status=res.status, content_type=res.content_type,
                      robots_allowed=res.robots_allowed)
        return res, ref

    def discover(self, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES) -> List[Dict[str, Any]]:
        """Every jobPostings summary across all pages (offset stepping, honouring the API's own `total`)."""
        postings: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            body = {"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""}
            res, _ = self._post(self.site.cxs_base + "/jobs", body)
            if res.status != 200:
                break
            data = json.loads(res.content)
            batch = data.get("jobPostings", [])
            postings.extend(batch)
            total = data.get("total", len(postings))
            offset += page_size
            if not batch or offset >= total:
                break
        return postings

    def crawl(self, postings: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        postings = postings if postings is not None else self.discover()
        rows: List[Dict[str, Any]] = []
        report = {"attempted": len(postings), "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0}
        for p in postings:
            external_path = p.get("externalPath")
            if not external_path:
                continue
            # externalPath already starts with '/job/...' in the live payload (verified 2026-09-23) - do NOT
            # prepend another '/job' (found via this test suite's own fixtures: doing so built a real,
            # literally double '/job/job/...' URL that would 404 against the live API).
            detail_url = self.site.cxs_base + external_path
            res, ref = self.fetch_raw(detail_url, kind="json")
            if res.status != 200:
                continue
            info = json.loads(res.content).get("jobPostingInfo") or {}
            row = self._map_row(info, external_path, ref.key)
            report["parsed"] += 1
            if row is None:
                report["no_country"] += 1
                continue
            rows.append(row)
            report["kept" if row["kind"] == "employment" else "discarded"] += 1
        return rows, report

    def _map_row(self, info: Dict[str, Any], external_path: str, raw_ref: str) -> Optional[Dict[str, Any]]:
        title = (info.get("title") or "").strip()
        if not title:
            return None
        # T23 (PM ruling R2): an unresolvable country is still never guessed (D6 unchanged) - but the row is
        # persisted with country_iso2=None + a real location_class now (migration 0020), not discarded.
        country = _country_iso2((info.get("country") or {}).get("alpha2Code")
                                or (info.get("jobRequisitionLocation") or {}).get("country", {}).get("alpha2Code"))
        # T29 (packet 017, PM-verified live, 20 real rows): Workday's own CXS API returns jobDescription as a
        # JSON string, not HTML - a real site-side templating bug (HTML-escaping text before it's put into
        # this JSON field, which never needed it) leaves a literal '&amp;'/'&quot;' in the string itself.
        # Decoded BEFORE _strip_html, never after (see jobposting.py's own identical fix for why: an entity-
        # encoded tag like '&lt;p&gt;' is invisible to _strip_html until decoded, and decoding AFTER
        # stripping would un-hide it as a real, un-stripped '<p>' left in the final text).
        body_text = _strip_html(decode_entities(info.get("jobDescription")))
        time_type = (info.get("timeType") or "").lower()
        ft_pt = "full" if "full" in time_type else ("part" if "part" in time_type else "unknown")
        location = classify_location(f"{title} {body_text or ''}", has_country=country is not None)
        url = info.get("externalUrl") or (self.site.public_base + external_path)
        contacts = extract_contacts(html=info.get("jobDescription") or "", text=body_text or "")
        row: Dict[str, Any] = {
            "ext_uid": info.get("jobPostingId") or external_path,
            "source_id": self.source_id,
            "url": url,
            "title_raw": title, "country_iso2": country, "city": info.get("location"),
            "org_id": self.org_id, "org_raw": self.org_raw,
            **kind_row_fields(title, body_text or ""),
            "ft_pt": ft_pt, "ad_lang": "en", "raw_text": body_text, "requirements_raw": body_text,
            "foreigner_eligible": "unknown", "extraction_family": "ats_workday_cxs", "raw_html_ref": raw_ref,
            "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
            "location_class_raw": location["location_class_raw"],
            "apply_url": url, **contacts,
        }
        posted = _parse_dt(info.get("startDate"))
        if posted:
            row["posted_at"] = posted
        deadline = _parse_dt(info.get("endDate"))
        if deadline:
            row["deadline"] = deadline
            row["status"] = "expired" if deadline < datetime.now(timezone.utc) else "open"
        return row
