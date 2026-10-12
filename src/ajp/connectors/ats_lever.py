"""T18 ats_lever: Lever's public postings JSON API. Verified live 2026-09-23:

    https://api.lever.co/robots.txt -> 200 'User-agent: * Allow: / Crawl-delay: 1' (enforced via PoliteClient's
    Crawl-delay machinery, T13); GET https://api.lever.co/v0/postings/{tenant}?mode=json&limit=N -> 200 JSON
    array, one object per posting, full description + categories inline (no separate detail fetch needed).

Tenant is never guessed - it comes from a jobs.lever.co/{tenant} link found on the org's own careers page
(ats_detect.py).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ajp.connectors.base import Connector, ObjectStore, PoliteClient, UrlManifest
from ajp.connectors.jobposting import _strip_html, classify_location, country_from_freetext_location
from ajp.extract.text import decode_entities
from ajp.extract_contacts import extract_contacts
from ajp.extract.intl_text import kind_row_fields

HOST = "api.lever.co"
TENANT_LINK_RE = re.compile(r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)", re.IGNORECASE)
LIMIT = 100


def manifest_for(tenant: str) -> UrlManifest:
    return UrlManifest(rf"https://{HOST}/v0/postings/{re.escape(tenant)}\?[^ ]*")


class LeverConnector(Connector):
    kind = "api"

    def __init__(self, client: PoliteClient, store: ObjectStore, tenant: str,
                org_id: Optional[str] = None, org_raw: Optional[str] = None):
        self.tenant = tenant
        self.source_id = f"ats_lever__{tenant}".lower()
        self.manifest = manifest_for(tenant)
        self.org_id, self.org_raw = org_id, org_raw
        super().__init__(client, store)

    @staticmethod
    def ensure_source(conn, tenant: str) -> str:
        source_id = f"ats_lever__{tenant}".lower()
        conn.execute(
            """INSERT INTO source (id, kind, base_url, list_url, robots_ok, rate_rps, geo_scope,
                                   ats_family, ats_tenant, notes)
               VALUES (%s,'api',%s,%s,true,1.0,'INTL','ats_lever',%s,
                       'Lever public postings JSON; Crawl-delay:1 from api.lever.co robots.txt honoured automatically')
               ON CONFLICT (id) DO NOTHING""",
            (source_id, f"https://jobs.lever.co/{tenant}",
             f"https://{HOST}/v0/postings/{tenant}?mode=json&limit={LIMIT}", tenant),
        )
        return source_id

    def crawl(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        url = f"https://{HOST}/v0/postings/{self.tenant}?mode=json&limit={LIMIT}"
        res, ref = self.fetch_raw(url, kind="json")
        report = {"attempted": 1, "parsed": 0, "kept": 0, "discarded": 0, "no_country": 0}
        if res.status != 200:
            return [], report
        try:
            postings = json.loads(res.content)
        except ValueError:
            return [], report
        if not isinstance(postings, list):
            return [], report
        rows = []
        for p in postings:
            row = self._map_row(p, ref.key)
            report["parsed"] += 1
            if row is None:
                report["no_country"] += 1
                continue
            rows.append(row)
            report["kept" if row["kind"] == "employment" else "discarded"] += 1
        return rows, report

    def _map_row(self, p: Dict[str, Any], raw_ref: str) -> Optional[Dict[str, Any]]:
        title = (p.get("text") or "").strip()
        if not title:
            return None
        categories = p.get("categories") or {}
        location = categories.get("location") or (categories.get("allLocations") or [None])[0]
        # Lever's own 'location' is free text (e.g. 'Baltimore, MD', 'Remote - Europe') with no structured
        # country field at all - see jobposting.country_from_freetext_location for why a bare 2-letter
        # trailing token needs its own ambiguity-aware resolution (US state vs. ISO country code collisions).
        country = country_from_freetext_location(location)
        # T29 (packet 017): the SAME JSON-text-is-not-HTML site-side templating bug found live on
        # academictransfer/nature_careers/jobs_ac_uk/theunijobs/ats_workday could affect Lever's own API text
        # too - not yet confirmed on any currently-live Lever row, but the fix is a no-op on already-clean
        # text (ajp.extract.text's own idempotency contract), so applied here defensively rather than only
        # after it is separately caught live. Decoded BEFORE _strip_html, never after (see jobposting.py's
        # own identical fix for why).
        body = _strip_html(decode_entities(p.get("description") or p.get("descriptionPlain")))
        commitment = (categories.get("commitment") or "").lower()
        ft_pt = "full" if "full" in commitment else ("part" if "part" in commitment else "unknown")
        # T23 (PM ruling R2): an unresolvable country is still never guessed (D6 unchanged) - the row is
        # persisted with country_iso2=None + a real location_class now (migration 0020), not discarded.
        # Lever's own free-text 'location' is often the remote signal itself (e.g. 'Remote - Europe') -
        # fed into the classifier alongside title+body.
        loc_class = classify_location(f"{title} {location or ''} {body or ''}", has_country=country is not None)
        url = p.get("hostedUrl") or p.get("applyUrl")
        contacts = extract_contacts(html=p.get("description") or "", text=body or "")
        row: Dict[str, Any] = {
            "ext_uid": p.get("id"), "source_id": self.source_id,
            "url": url, "title_raw": title, "country_iso2": country,
            "city": location, "org_id": self.org_id, "org_raw": self.org_raw or categories.get("team"),
            **kind_row_fields(title, body or ""),
            "ft_pt": ft_pt, "ad_lang": "en", "raw_text": body, "requirements_raw": body,
            # T26 (packet 015): a real Lever-provided apply link when there is one, else the ad's own
            # canonical page - never left null (>=1 clickable link, guaranteed).
            "apply_url": p.get("applyUrl") or url, "foreigner_eligible": "unknown",
            "extraction_family": "ats_lever", "raw_html_ref": raw_ref,
            "location_class": loc_class["location_class"], "location_class_conf": loc_class["location_class_conf"],
            "location_class_raw": loc_class["location_class_raw"],
            **contacts,
        }
        if not row["url"]:
            return None
        created_at = p.get("createdAt")
        if isinstance(created_at, (int, float)):
            row["posted_at"] = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
        return row
