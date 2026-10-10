"""Build src/ajp/seeds/rs_orgs.json (T4 org seed).

Sources (all recorded per row in source_url / notes):
  * https://www.bg.ac.rs/faculties/ and /fakulteti/  -> 31 University of Belgrade faculties, official EN + SR names
  * pm_packet_001  -> universities, institutes, contacts, seed URLs
  * pm_packet_004 + pm/recon/bg_faculty_matrix.json -> employment-board URLs, faculty hosts
Every network request goes through PoliteClient (manifest + robots + 1 rps). Nothing is invented:
unverifiable URLs become NULL with a TODO in notes.

    python tools/build_rs_org_seed.py --pm "../pm"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ajp.connectors.base import PoliteClient, UrlManifest
from ajp.rawstore import LocalFsStore, put_raw

FAC_KEY = {  # packet 004 faculty label -> host key used below
    "Architecture": "arh", "Electrical Engineering": "etf", "Mechanical Engineering": "mas",
    "Organizational Sciences": "fon", "Technology and Metallurgy": "tmf", "Forestry": "sfb", "Biology": "bio",
    "Mathematics": "matf", "Physical Chemistry": "ffh", "Physics": "ff", "Chemistry": "chem",
    "Economics and Business": "ekof", "Education": "uf", "Security Studies": "fb", "Political Sciences": "fpn",
    "Philosophy": "f", "Philology": "fil", "Medicine": "med", "Dental Medicine": "stomf", "Pharmacy": "pharmacy",
    "Veterinary Medicine": "vet", "Agriculture": "agrif", "Transport and Traffic": "sf", "Mining and Geology": "rgf",
    "Geography": "gef", "Orthodox Theology": "bfspc", "Special Education": "fasper", "Technical Faculty Bor": "tfbor",
    "Civil Engineering": "grf",
}
MATRIX_KEY = {"Architecture": "arh", "Civil Engineering": "grf", "Electrical Engineering": "etf", "Mechanical Engineering": "mas",
              "Agriculture": "agrif", "Mining & Geology": "rgf", "Transport & Traffic": "sf", "Technical Faculty Bor": "tfbor",
              "Technology & Metallurgy": "tmf", "Organizational Sciences": "fon", "Forestry": "sfb", "Geography": "gef",
              "Biology": "bio", "Mathematics": "matf", "Physical Chemistry": "ffh", "Physics": "ff", "Chemistry": "chem",
              "Economics & Business": "ekof", "Orthodox Theology": "bfspc", "Education": "uf", "Security Studies": "fb",
              "Special Education": "fasper", "Political Sciences": "fpn", "Sports & PE": "fsfv", "Philosophy": "f",
              "Philology": "fil", "Law": "ius", "Medicine": "med", "Dental Medicine": "stomf", "Pharmacy": "pharmacy",
              "Veterinary Medicine": "vet"}
HOMEPAGE_OVERRIDE = {"fon": "https://fon.bg.ac.rs/", "mas": "https://www.mas.bg.ac.rs/", "med": "https://med.bg.ac.rs/"}
HOMEPAGE_ONLY = {"rgf", "gef", "bfspc", "tfbor", "stomf", "agrif", "grf", "uf"}  # no explicit board URL in packet 004
DROP_HOST = re.compile(r"^(zaposleni\.|.*razvojkarijere)")  # HR self-service logins / career centre: out of scope
DROP_KEYS_IN_NAMES = {"www", "www1", "en", "studiesinenglish"}
URL_RE = re.compile(r"https?://[^\s,+)]+")


def host_key(host: str) -> str:
    return ".".join(p for p in host[: -len(".bg.ac.rs")].split(".") if p not in DROP_KEYS_IN_NAMES)


def parse_faculties(html: str) -> dict:
    out: dict = {}
    for a in HTMLParser(html).css("a"):
        href = a.attributes.get("href", "")
        host = urlsplit(href).netloc
        name = re.sub(r"\s+", " ", a.text(separator=" ")).strip()
        if host.endswith(".bg.ac.rs") and host != "www.bg.ac.rs" and not host.startswith("arhiva") and name:
            out.setdefault(host_key(host), name)
    return out


def robots_status(rep: dict) -> str:
    if rep["robots"] == "unavailable":
        return "unavailable"
    if rep["robots"] == "unpublished":
        return "allowed_no_robots"
    return "allowed" if rep["allowed"] else "disallowed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pm", default="../pm")
    ap.add_argument("--out", default="src/ajp/seeds/rs_orgs.json")
    ap.add_argument("--raw-root", default="data/raw")
    a = ap.parse_args()
    pm = Path(a.pm)
    p1 = json.loads((pm / "inbox" / "pm_packet_001_serbia_seed.json").read_text())
    p4 = json.loads((pm / "inbox" / "pm_packet_004_m2_serbia_local.json").read_text())
    matrix = {MATRIX_KEY[r["name"]]: r for r in json.loads((pm / "recon" / "bg_faculty_matrix.json").read_text())}
    client, store = PoliteClient(rps=1), LocalFsStore(a.raw_root)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---- University of Belgrade faculties: official names from the university's own pages -----
    names = {}
    for lang, url in (("en", "https://www.bg.ac.rs/faculties/"), ("sr", "https://www.bg.ac.rs/fakulteti/")):
        r = client.get(url, manifest=UrlManifest.exact(url))
        put_raw(store, kind="html", source_id="bg_ac_rs_faculties", url=url, content=r.content, fetched_at=r.fetched_at,
                status=r.status, content_type=r.content_type, robots_allowed=r.robots_allowed)
        names[lang] = parse_faculties(r.content.decode("utf-8", "replace"))
    assert len(names["en"]) == 31 and set(names["en"]) == set(names["sr"]), "expected 31 faculties on both pages"

    boards = {}
    for label, text in p4["T5_bg_faculty_matrix"]["employment_board_urls_verified"].items():
        key = FAC_KEY[label]
        urls = [u.rstrip(".") for u in URL_RE.findall(text) if not DROP_HOST.match(urlsplit(u).netloc)]
        if key not in HOMEPAGE_ONLY:
            boards[key] = urls

    orgs = []
    bg_uni = next(u for u in p1["sources"]["serbia"]["public_universities"] if u["id"] == "univ_bg")
    orgs.append(dict(slug="bg", type="university", parent_slug=None, name_en="University of Belgrade", name_native=None, city="Belgrade",
                     homepage=bg_uni["homepage"], career_urls=bg_uni["career_paths"], contact_email=bg_uni["email"], contact_phone=bg_uni["phone"],
                     source_url="pm_packet_001#public_universities", notes="NO central job board; recruitment is faculty-level. contact e-mail belongs to the Career Development Centre. TODO name_native"))
    for key in sorted(names["en"]):
        m = matrix.get(key, {})
        home = HOMEPAGE_OVERRIDE.get(key) or (f"{urlsplit(m['final']).scheme}://{urlsplit(m['final']).netloc}/" if m.get("final") else f"https://{m['host']}/")
        mode = "deeplink_only" if key == "fsfv" else "httpx"
        note = []
        if key in HOMEPAGE_ONLY:
            note.append("TODO: employment board not yet known - discover from homepage navigation (T5)")
        if key in ("ius", "grf"):
            note.append("PM: possibly JS-rendered; verify before assuming httpx")
        orgs.append(dict(slug=f"bg-{key}", type="faculty", parent_slug="bg", name_en=names["en"][key], name_native=names["sr"][key],
                         city="Bor" if key == "tfbor" else "Belgrade", homepage=home, career_urls=boards.get(key, []),
                         render_mode=mode, source_url="https://www.bg.ac.rs/faculties/ + https://www.bg.ac.rs/fakulteti/ ; boards: pm_packet_004",
                         notes="; ".join(note) or None))

    def add_seed(slug, otype, name_en, home, **kw):
        orgs.append(dict(slug=slug, type=otype, name_en=name_en, homepage=home, parent_slug=None, **kw))

    verify = {"univ_odbrane", "kpu", "imi", "inep", "ifvc_ns"}
    for u in p1["sources"]["serbia"]["public_universities"]:
        if u["id"] == "univ_bg":
            continue
        add_seed(u["id"].replace("univ_", "").replace("_", "-"), "university", u["name"], u["homepage"], name_native=None,
                 career_urls=u.get("career_paths", []), contact_email=u.get("email"), contact_phone=u.get("phone"),
                 source_url="pm_packet_001#public_universities", notes=("UNVERIFIED URL (PM: VERIFY); " if u["id"] in verify else "") + (u.get("note") or ""))
    for i, url in enumerate(p1["sources"]["serbia"]["private_universities_seed"]):
        host = urlsplit(url).netloc.removeprefix("www.")
        add_seed(f"private-{host.split('.')[0]}", "university", host, url, career_urls=[],
                 source_url="pm_packet_001#private_universities_seed", notes="TODO: official name (placeholder = host); seeded from URL only")
    for u in p1["sources"]["serbia"]["institutes_national_importance"] + [x for x in p1["sources"]["serbia"]["other_key_institutes_seed"] if x["id"] != "sanu_institutes"]:
        add_seed("inst-" + u["id"].replace("inn_", ""), "institute", u["name"], u.get("homepage"), career_urls=u.get("career_paths", []),
                 contact_email=u.get("email") or u.get("email_hr") or u.get("email_info"), contact_phone=u.get("phone"),
                 source_url="pm_packet_001#institutes", notes=("UNVERIFIED URL (PM: verify); " if u["id"] in verify or "verify" in (u.get("note") or "").lower() else "") + (u.get("note") or ""))

    # ---- probes: robots status per host; verify unverified homepages; drop robots-disallowed board URLs ----
    for o in orgs:
        home = o.get("homepage")
        if home and "UNVERIFIED" in (o.get("notes") or ""):
            try:
                r = client.get(home, manifest=UrlManifest(re.escape(home.rstrip("/")) + r"(/.*)?"))  # same-origin redirects ok
                outcome = f"HTTP {r.status}"
            except Exception as e:  # noqa: BLE001 - recorded in notes, not hidden
                outcome = type(e).__name__
            base = re.sub(r"UNVERIFIED URL \(PM: [Vv][Ee][Rr][Ii][Ff][Yy]\); ", "", o["notes"])
            if outcome == "HTTP 200":
                o["notes"] = f"{base}; homepage verified {now[:10]} ({outcome})"
            else:
                o["notes"] = f"TODO: PM-suggested homepage {home} not verified on {now[:10]} ({outcome}); left NULL. {base}"
                o["homepage"] = None
        home = o.get("homepage")
        if home:
            o["host"] = urlsplit(home).netloc
            try:
                rep = client.robots_report(home)
                o["robots_status"] = robots_status(rep)
                o["robots_http_status"] = rep["http_status"]
            except Exception as e:  # noqa: BLE001
                o["robots_status"] = "unavailable"
            kept = []
            for cu in o.get("career_urls") or []:
                (kept if client.allowed(cu) else o.setdefault("robots_disallowed_urls", [])).append(cu)
            o["career_urls"] = kept
        o["robots_checked_at"] = now
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"generated_at": now, "count": len(orgs), "orgs": orgs}, ensure_ascii=False, indent=1))
    by = {}
    for o in orgs:
        by.setdefault(o.get("robots_status"), []).append(o["slug"])
    print(len(orgs), "orgs;", {k: len(v) for k, v in by.items()})
    for k in ("disallowed", "unavailable", None):
        print(k, by.get(k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
