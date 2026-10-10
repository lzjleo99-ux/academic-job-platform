"""org_master seed loading and crawl-target selection (T4/T5)."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb
from rapidfuzz import fuzz

SEED_PATH = Path(__file__).parent / "seeds" / "rs_orgs.json"
# Columns owned by the seed. Enrichment columns (openalex_id, qs_*/the_*, accredited_rs) are never
# touched by a reseed, so re-running the seed cannot erase matches made by later tasks.
SEED_COLUMNS = ("name_native", "name_en", "city", "homepage", "host", "career_urls", "contact_email",
                "contact_phone", "type", "render_mode", "robots_status", "robots_checked_at", "source_url", "notes",
                "crawl_config", "foreigner_eligible_default")


def load_seed(path: Path = SEED_PATH) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["orgs"]


def seed_orgs(conn: psycopg.Connection, orgs: Optional[List[Dict[str, Any]]] = None, country: str = "RS") -> int:
    orgs = orgs if orgs is not None else load_seed()
    cols = ", ".join(SEED_COLUMNS)
    ph = ", ".join(f"%({c})s" for c in SEED_COLUMNS)
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in SEED_COLUMNS)
    for o in orgs:
        row = {c: o.get(c) for c in SEED_COLUMNS}
        row["career_urls"] = o.get("career_urls") or []
        row["crawl_config"] = Jsonb(o.get("crawl_config") or {})
        row.update(slug=o["slug"], country=country)
        conn.execute(f"INSERT INTO org_master (slug, country, {cols}) VALUES (%(slug)s, %(country)s, {ph}) "
                     f"ON CONFLICT (slug) DO UPDATE SET {upd}", row)
    for o in orgs:
        if o.get("parent_slug"):
            conn.execute("UPDATE org_master SET parent_org_id = (SELECT id FROM org_master WHERE slug = %s) WHERE slug = %s",
                         (o["parent_slug"], o["slug"]))
    for o in orgs:      # deeplink-only orgs: a channel source + one link, never a fetch
        if o.get("render_mode") == "deeplink_only" and (o.get("deeplink_url") or o.get("homepage")):
            _ensure_channel(conn, o["slug"], o["name_en"], o.get("deeplink_url") or o["homepage"],
                            reason=o.get("deeplink_reason"))
    return len(orgs)


def _ensure_channel(conn: psycopg.Connection, slug: str, name: str, link_url: str,
                    reason: Optional[str] = None) -> None:
    """A channel source + one link. Nothing is ever fetched for it. ``link_url`` is usually the organisation's own
    homepage, but a closure ruling may point it at a more specific page (e.g. an HR host's own site, R4, or a
    board page confirmed to hold nothing worth a generic homepage link) - the source's base_url still records the
    organisation's homepage when that differs, for provenance.

    ``reason`` (T25, packet 016 PM ruling on uns-magister): a short, curated, user-facing sentence for WHY this
    specific org is deeplink-only - shown in place of the generic boilerplate when the seed provides one. The
    org's own full ``notes`` field is never used directly here: it is written for an internal/dev audit trail
    (long, timestamped, cross-referencing task ids) and is not fit to show a real visitor; ``deeplink_reason`` is
    a separate, deliberately short opt-in field for the cases worth explaining specifically."""
    sid = slug.replace("-", "_")
    source_notes = reason or ("Deeplink-only: not crawled (robots disallow, HR/login host, no employment board "
                              "found, or persistently unreachable); opens on the linked site.")
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
                    VALUES (%s, 'channel', %s, %s, false, false, 'RS', %s)
                    ON CONFLICT (id) DO UPDATE SET robots_ok = false, list_url = EXCLUDED.list_url, notes = EXCLUDED.notes""",
                 (sid, link_url, link_url, source_notes))
    conn.execute("""INSERT INTO channel_link (source_id, slug, label, url, country_iso2, description)
                    VALUES (%s, 'home', %s, %s, 'RS', %s)
                    ON CONFLICT (source_id, slug) DO UPDATE SET
                        label = EXCLUDED.label, url = EXCLUDED.url, description = EXCLUDED.description""",
                 (sid, f"{name}: careers (opens on {urlsplit(link_url).netloc})", link_url,
                  reason or "Opens in your browser on the linked site; not part of our database."))


def add_standalone_channel(conn: psycopg.Connection, source_id: str, links: List[Dict[str, str]], notes: str = "") -> None:
    """A channel not tied to one organisation's own board (e.g. a university-wide career-guidance portal). ``links``
    is a list of {slug, label, url, country_iso2?, description?}; nothing under ``source_id`` is ever fetched."""
    base = links[0]["url"] if links else ""
    conn.execute("""INSERT INTO source (id, kind, base_url, list_url, robots_ok, needs_browser, geo_scope, notes)
                    VALUES (%s, 'channel', %s, %s, false, false, 'RS', %s)
                    ON CONFLICT (id) DO UPDATE SET base_url = EXCLUDED.base_url, list_url = EXCLUDED.list_url, notes = EXCLUDED.notes""",
                 (source_id, base, base, notes))
    for l in links:
        conn.execute("""INSERT INTO channel_link (source_id, slug, label, url, country_iso2, description)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_id, slug) DO UPDATE SET label = EXCLUDED.label, url = EXCLUDED.url""",
                     (source_id, l["slug"], l["label"], l["url"], l.get("country_iso2", "RS"), l.get("description", "")))


def prune_crawl_state(conn: psycopg.Connection, source_id: str = "rs_org_boards") -> int:
    """Delete crawl_state rows (failure counters) for a URL that no longer belongs to any org's career_urls -
    e.g. a board URL a closure ruling replaced with a deeplink. Returns the number of rows removed."""
    return conn.execute(
        """DELETE FROM crawl_state cs WHERE cs.source_id = %s
             AND NOT EXISTS (SELECT 1 FROM org_master o WHERE cs.key = ANY(o.career_urls))""",
        (source_id,)).rowcount


def degrade_to_deeplink(conn: psycopg.Connection, slug: str, reason: str) -> bool:
    """R1: after the 3rd failed run (or a permanent robots disallow) an org stops being crawled and becomes a deeplink channel."""
    row = conn.execute("SELECT name_en, homepage FROM org_master WHERE slug=%s", (slug,)).fetchone()
    if row is None or not row[1]:
        return False
    conn.execute("""UPDATE org_master SET render_mode='deeplink_only',
                    notes = concat_ws('; ', notes, %s::text) WHERE slug=%s""", (f"degraded to deeplink: {reason[:200]}", slug))
    _ensure_channel(conn, slug, row[0] or slug, row[1])
    return True


def crawl_targets(conn: psycopg.Connection, country: str = "RS") -> List[Dict[str, Any]]:
    """The only sanctioned way for a board crawler to pick organisations: robots allows, a fetch mode
    that performs requests, and at least one known board URL. deeplink_only / skip / disallowed /
    unavailable organisations are never returned."""
    cur = conn.execute(
        """SELECT id, slug, name_en, name_native, city, type, host, homepage, career_urls, render_mode, robots_status, crawl_config,
                  foreigner_eligible_default
             FROM org_master
            WHERE country = %s AND render_mode IN ('httpx', 'playwright')
              AND robots_status IN ('allowed', 'allowed_no_robots')
              AND cardinality(career_urls) > 0
            ORDER BY slug""", (country,))
    names = [d.name for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- OpenAlex matching --------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold().replace("&", " and ").replace("đ", "dj"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", s)).strip()


def match_openalex(orgs: List[Dict[str, Any]], oa_rows: List[Dict[str, Any]], threshold: float = 90.0,
                   margin: float = 5.0) -> Dict[str, Any]:
    """Match seed orgs to OpenAlex institutions by name. Conservative by design: a match needs a score
    >= threshold, a lead of >= margin over the runner-up (a different OpenAlex id), and OpenAlex ids are
    assigned one-to-one. Anything else is reported as ambiguous / unmatched, never guessed.
    token_sort_ratio (not token_set) so 'University of Belgrade' cannot match one of its faculties."""
    by_slug = {o["slug"]: o for o in orgs}
    cands = [(r["id"], r["display_name"], [_norm(n) for n in [r["display_name"]] + list(r.get("display_name_alternatives") or [])])
             for r in oa_rows]
    scored = []
    for o in orgs:
        if not o.get("name_en") or "placeholder" in (o.get("notes") or ""):
            continue
        parent = by_slug.get(o.get("parent_slug") or "")
        q = _norm(f"{parent['name_en']} {o['name_en']}" if parent and parent.get("name_en") else o["name_en"])
        ranked = sorted(((max(fuzz.token_sort_ratio(q, n) for n in names), oid, disp) for oid, disp, names in cands), reverse=True)
        scored.append((o["slug"], ranked[:3]))
    matches, ambiguous, unmatched, claimed = [], [], [], {}
    for slug, top in sorted(scored, key=lambda x: -x[1][0][0]):
        best, second = top[0], (top[1] if len(top) > 1 else (0, None, None))
        if best[0] >= threshold and best[0] - second[0] >= margin and best[1] not in claimed:
            claimed[best[1]] = slug
            matches.append({"slug": slug, "openalex_id": best[1], "matched_name": best[2], "score": round(best[0], 1)})
        elif best[0] >= threshold:
            ambiguous.append({"slug": slug, "candidates": [{"openalex_id": t[1], "name": t[2], "score": round(t[0], 1)} for t in top]})
        else:
            unmatched.append({"slug": slug, "best": {"openalex_id": best[1], "name": best[2], "score": round(best[0], 1)}})
    return {"matches": matches, "ambiguous": ambiguous, "unmatched": unmatched}


def apply_openalex_matches(conn: psycopg.Connection, matches: List[Dict[str, Any]]) -> int:
    """Store ids only where none exists yet (a manual/curated id is never overwritten)."""
    n = 0
    for m in matches:
        n += conn.execute("UPDATE org_master SET openalex_id = %s WHERE slug = %s AND openalex_id IS NULL",
                          (m["openalex_id"], m["slug"])).rowcount
    return n


def apply_openalex_works_count(conn: psycopg.Connection, works_counts: Dict[str, int]) -> int:
    """{slug: works_count} -> org_master.openalex_works_count, only where a match already exists (openalex_id
    IS NOT NULL) so this can never attach a research-activity number to an org OpenAlex was never actually
    matched to. Never touched by a reseed (T12: enrichment, same convention as openalex_id/qs_rank)."""
    n = 0
    for slug, count in works_counts.items():
        n += conn.execute(
            "UPDATE org_master SET openalex_works_count = %s WHERE slug = %s AND openalex_id IS NOT NULL",
            (count, slug)).rowcount
    return n


# T12 sizing addenda: a healthcare-NAMED facility (oncology/clinical/hospital) can appear in OpenAlex under an
# academic-shaped type (its clinicians publish research) without being an academic EMPLOYER for this platform's
# purpose - excluded from the review queue of "new org" candidates, not from org_master if it was already there
# for another reason. Serbian + English keywords; word-boundary so e.g. 'klinicki' doesn't also catch 'tehnicki'.
# includes health-INSURANCE bodies (RFZO, 'National Health Insurance Fund'): PM named it explicitly alongside
# the clinical-facility examples - not an academic employer either, even though it isn't itself a care provider.
_HEALTHCARE_FACILITY = re.compile(
    r"\b(onkolo\w*|oncology|klinick\w*|klinik\w*|clinic\w*|bolnic\w*|hospital\w*|"
    r"zavod za zdravstvenu zastit\w*|dom zdravlja|urgentni centar|dijagnostick\w* centar|"
    r"health insurance|zdravstveno osiguranj\w*)\b", re.I)


def is_healthcare_facility(name: str) -> bool:
    return bool(_HEALTHCARE_FACILITY.search(_norm(name)))


def openalex_review_queue(seed: List[Dict[str, Any]], oa_rows: List[Dict[str, Any]], matched_ids: set,
                          min_works: int = 50) -> List[Dict[str, Any]]:
    """OpenAlex institutions NOT already matched to a seed org, with >=min_works and not healthcare-named - a
    review queue of plausible NEW academic employers for a future task, never auto-added to org_master (PM
    ruling: name-only matches are reviewed, never auto-merged)."""
    return sorted(
        ({"openalex_id": r["id"], "name": r["display_name"], "type": r.get("type"), "works_count": r.get("works_count", 0),
          "homepage": r.get("homepage"), "city": r.get("city")}
         for r in oa_rows
         if r["id"] not in matched_ids and (r.get("works_count") or 0) >= min_works and not is_healthcare_facility(r["display_name"])),
        key=lambda x: -x["works_count"])


# ---------------------------------------------------------------- PM recon addenda -------
_ACRONYM = re.compile(r"\((\w+)\)\s*$")


def apply_board_addenda(seed: List[Dict[str, Any]], addenda: List[Dict[str, Any]], allowed, prefix: str = "bg-") -> Dict[str, Any]:
    """Apply PM board recon to the seed: org label '(ACRONYM)' -> slug '<prefix>acronym'.
      * 'DEEPLINK ONLY' decisions: render_mode=deeplink_only, no career URLs, robots_status=disallowed (never fetched);
      * career_urls REPLACE the old ones, each kept only if ``allowed(url)`` (the robots gate) and never on an HR/login host (R4);
      * 'Playwright render path' in the notes => render_mode='playwright' with crawl_config.empty_is_failure, so an org whose
        rendered board yields nothing on 3 consecutive runs degrades to a deeplink (R1 counter).
    Returns a change log; unknown orgs are reported, not invented."""
    by_slug = {o["slug"]: o for o in seed}
    log: Dict[str, Any] = {"applied": [], "unknown": [], "dropped_urls": []}
    for add in addenda:
        m = _ACRONYM.search(add.get("org", ""))
        slug = f"{prefix}{m.group(1).lower()}" if m else None
        org = by_slug.get(slug or "")
        if org is None:
            log["unknown"].append(add.get("org"))
            continue
        notes = []
        if "DEEPLINK ONLY" in (add.get("decision") or ""):
            org.update(render_mode="deeplink_only", career_urls=[], robots_status="disallowed")
            notes.append("PM 2026-09-22: robots.txt 'Disallow: /' - deeplink only, zero fetches incl. sitemap")
        elif add.get("career_urls"):
            kept = []
            for u in add["career_urls"]:
                host = re.sub(r"^https?://([^/]+).*$", r"\1", u)
                if re.match(r"^(zaposleni|servisi|login|sso|mail)\.", host):
                    log["dropped_urls"].append((slug, u, "R4 HR/login host"))
                elif not allowed(u):
                    log["dropped_urls"].append((slug, u, "robots gate"))
                else:
                    kept.append(u)
            org["career_urls"] = kept
            if "Playwright render path" in (add.get("notes") or ""):
                org["render_mode"] = "playwright"
                org["crawl_config"] = dict(org.get("crawl_config") or {}, empty_is_failure=True)
                notes.append("JS shell: rendered via Playwright; degrades to deeplink if 3 consecutive runs find nothing")
            if add.get("exclude"):
                notes.append("excluded (R4): " + ", ".join(add["exclude"]))
        if add.get("parser_note") and add.get("host"):        # '/category/...' paths named in a parser note become board URLs too
            for path in dict.fromkeys(re.findall(r"(?<![\w.])(/category/[\w\-/]+/)", add["parser_note"])):
                u = f"https://{add['host']}{path}"
                if u in org.get("career_urls", []):
                    continue
                if allowed(u):
                    org["career_urls"] = list(org.get("career_urls", [])) + [u]
                else:
                    log["dropped_urls"].append((slug, u, "robots gate"))
        text = add.get("notes") or ""
        if re.search(r"deeplink-only", text, re.I) and not add.get("career_urls") and not org.get("career_urls"):
            org.update(render_mode="deeplink_only", career_urls=[])                     # e.g. employment only behind an HR portal (R4)
            notes.append("no public employment board (R4 HR portal / none found): deeplink only")
        if re.search(r"\b3 (?:empty )?runs\b|\belse deeplink\b", text, re.I) and org.get("render_mode") != "deeplink_only":
            org["crawl_config"] = dict(org.get("crawl_config") or {}, empty_is_failure=True)   # nothing open on 3 consecutive runs => deeplink
        if add.get("notes"):
            notes.append(add["notes"][:300])
        if notes:
            org["notes"] = "; ".join(x for x in [org.get("notes")] + notes if x)
        log["applied"].append(slug)
    return log


def apply_crawl_config(seed: List[Dict[str, Any]], entries: List[Dict[str, Any]]) -> List[str]:
    """Merge per-organisation crawl_config entries ({"slug": ..., "crawl_config": {...}, "why": ...}) into the seed.
    Kept separate from PM recon files (which are the PM's) so operator/engineering tuning is visible and reviewable."""
    by_slug = {o["slug"]: o for o in seed}
    applied = []
    for e in entries:
        org = by_slug.get(e["slug"])
        if org is None:
            raise ValueError(f"unknown org in crawl config: {e['slug']}")
        cfg = e["crawl_config"]
        if "max_pages" in cfg and not (isinstance(cfg["max_pages"], int) and 1 <= cfg["max_pages"] <= 10):
            raise ValueError(f"{e['slug']}: max_pages must be an int in 1..10")
        org["crawl_config"] = dict(org.get("crawl_config") or {}, **cfg)
        applied.append(e["slug"])
    return applied
