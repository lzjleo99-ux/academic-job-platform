"""T18 ats_greenhouse: Greenhouse's public job board JSON API. Verified live 2026-09-23:

    https://boards-api.greenhouse.io/robots.txt -> 200, only 'Disallow: /embed/' (never used - the /v1/boards
    JSON below replaces it entirely); GET https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true
    -> 200 {"jobs":[{"id","title","absolute_url","location":{"name"},"content" (HTML, only with ?content=true),
    "updated_at","first_published"}]}; a nonexistent tenant -> 404 JSON {"error":"Job not found"} (not a 200).

Tenant is never guessed - it comes from a boards.greenhouse.io/{tenant} link found on the org's own careers
page (ats_detect.py).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ajp.connectors.base import Connector, ObjectStore, PoliteClient, UrlManifest
from ajp.connectors.jobposting import _parse_dt, _strip_html, classify_location, country_from_freetext_location
from ajp.extract.text import decode_entities
from ajp.extract_contacts import extract_contacts
from ajp.extract.intl_text import kind_row_fields

HOST = "boards-api.greenhouse.io"
TENANT_LINK_RE = re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.IGNORECASE)


def manifest_for(tenant: str) -> UrlManifest:
    return UrlManifest(rf"https://{HOST}/v1/boards/{re.escape(tenant)}/jobs\?[^ ]*")


class GreenhouseConnector(Connector):
    kind = "api"

    def __init__(self, client: PoliteClient, store: ObjectStore, tenant: str,
                org_id: Optional[str] = None, org_raw: Optional[str] = None):
        self.tenant = tenant
        self.source_id = f"ats_greenhouse__{tenant}".lower()
        self.manifest = manifest_for(tenant)
        self.org_id, self.org_raw = org_id, org_raw
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, tenant: str) -> str:
        source_id = f"ats_greenhouse__{tenant}".lower()
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope,
                                   ats_family, ats_tenant, notes)
               VALUES (%s,'api',%s,%s,true,1.0,'INTL','ats_greenhouse',%s,
                       'Greenhouse public board JSON with ?content=true; only /embed/ is robots-disallowed, never used')
               ON CONFLICT (id) DO NOTHING""",
            (source_id, f"https://boards.greenhouse.io/{tenant}",
             f"https://{HOST}/v1/boards/{tenant}/jobs?content=true", tenant),
        )
        return source_id

    def crawl(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        url = f"https://{HOST}/v1/boards/{self.tenant}/jobs?content=true"
        res, ref = self.fetch_raw(url, kind="json")
        report = {"attempted": 1, "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0}
        if res.status != 200:
            return [], report                                    # incl. the documented 404 'Job not found' shape
        try:
            data = json.loads(res.content)
        except ValueError:
            return [], report
        rows = []
        for j in data.get("jobs", []):
            row = self._map_row(j, ref.key)
            report["parsed"] += 1
            if row is None:
                report["no_country"] += 1
                continue
            rows.append(row)
            report["kept" if row["kind"] == "employment" else "discarded"] += 1
        return rows, report

    def _map_row(self, j: Dict[str, Any], raw_ref: str) -> Optional[Dict[str, Any]]:
        title = (j.get("title") or "").strip()
        url = j.get("absolute_url")
        if not title or not url:
            return None
        location_name = (j.get("location") or {}).get("name")
        # same free-text-location caveat as Lever - see jobposting.country_from_freetext_location.
        country = country_from_freetext_location(location_name)
        # T29 (packet 017): same defensive rationale as ats_lever.py's own identical fix - not yet confirmed
        # on any currently-live Greenhouse row, but a safe no-op on already-clean text. Decoded BEFORE
        # _strip_html, never after (see jobposting.py's own identical fix for why: this connector's own test
        # fixture literally has entity-encoded tags, '&lt;p&gt;', which the wrong order would un-hide as a
        # real, un-stripped '<p>' in the final text - found live writing this very fix).
        body = _strip_html(decode_entities(j.get("content")))
        # T23 (PM ruling R2): an unresolvable country is still never guessed (D6 unchanged) - the row is
        # persisted with country_iso2=None + a real location_class now (migration 0020), not discarded.
        # location_name itself is often the remote/hybrid signal on this ATS ("Remote", "Remote - EMEA") -
        # fed into the classifier alongside title+body.
        location = classify_location(f"{title} {location_name or ''} {body or ''}", has_country=country is not None)
        contacts = extract_contacts(html=j.get("content") or "", text=body or "")
        row: Dict[str, Any] = {
            "ext_uid": str(j.get("id")), "source_id": self.source_id, "url": url, "title_raw": title,
            "country_iso2": country, "city": location_name, "org_id": self.org_id, "org_raw": self.org_raw,
            **kind_row_fields(title, body or ""),
            "ft_pt": "unknown", "ad_lang": "en", "raw_text": body, "requirements_raw": body,
            "apply_url": url, "foreigner_eligible": "unknown",
            "extraction_family": "ats_greenhouse", "raw_html_ref": raw_ref,
            "location_class": location["location_class"], "location_class_conf": location["location_class_conf"],
            "location_class_raw": location["location_class_raw"],
            **contacts,
        }
        posted = _parse_dt(j.get("first_published"))
        if posted:
            row["posted_at"] = posted
        # S1, PM code review 2026-09-23: application_deadline (ISO date, present on some GH boards) was
        # being discarded entirely.
        deadline = _parse_dt(j.get("application_deadline"))
        if deadline:
            row["deadline"] = deadline
            row["status"] = "expired" if deadline < datetime.now(timezone.utc) else "open"
        return row
