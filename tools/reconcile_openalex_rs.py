"""T20 OpenAlex reconciliation (PM correction, pm_t20_openalex_reconciliation_2026-09-23): the approved T20
scope is the FULL 119 in-scope RS OpenAlex institutions, not just the pre-existing 100-org static seed.
For each of the 119, classifies it as:

  REPRESENTED  - the same org as an existing seed entry (exact homepage-host match, then fuzzy name+alias
                 match via ajp.orgs.match_openalex - conservative, threshold=85/margin=3, never guessed).
  CONSOLIDATED - a named sub-unit of an already-seeded PARENT org (checked individually; the University of
                 Defence's own Strategic Research Institute is the one real case found).
  OUT_OF_SCOPE - a government/regulatory/payment body that does not hire researchers, or the one known
                 OpenAlex geo error (Gulf University, .edu.bh = Bahrain) - each with its own stated reason.
  MISSING      - a genuinely independent org not yet in the seed - added to
                 rs_orgs_openalex_supplement.json (D6: no fabricated fields, unknown stays null).

Per PM's own instruction, genuine doubt is resolved toward MISSING/include, never a silent drop. A DELIBERATE
DESIGN CHOICE, found live while building this: host-SUFFIX matching (e.g. treating any *.bg.ac.rs subdomain
as "the University of Belgrade") was tried and REJECTED - several genuinely INDEPENDENT institutes are
DNS-hosted under a university's own domain zone for historical/administrative reasons (e.g.
izbis.bg.ac.rs = Institute for Plant Protection and Environment, fins.uns.ac.rs = Institute of Food
Technology) while remaining separate legal employers, exactly matching this project's own EXISTING
convention of seeding institutes like inst-ihtm (ihtm.bg.ac.rs) as their own orgs, not folded into "bg". Only
an EXACT host match or a real fuzzy NAME match counts as REPRESENTED; a shared domain suffix alone does not.

Writes:
  pm/recon/t20_rs_openalex_reconciliation.json  - the full 119-row classification table
  src/ajp/seeds/rs_orgs_openalex_supplement.json - the MISSING orgs, seed-shaped, ready for orgs.seed_orgs()

    python tools/reconcile_openalex_rs.py
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ajp import orgs as orgs_mod  # noqa: E402

RAW_PATH = Path("/tmp/ajp/pm/recon/t12_openalex_rs_institutions_raw.json")
RECON_OUT_PATH = Path("/tmp/ajp/pm/recon/t20_rs_openalex_reconciliation.json")
SUPPLEMENT_OUT_PATH = ROOT / "src" / "ajp" / "seeds" / "rs_orgs_openalex_supplement.json"

# Manual CONSOLIDATED calls: a named sub-unit of an ALREADY-seeded parent, checked individually (not
# something either join could infer on its own - the name has to be read to recognise this).
CONSOLIDATED = {
    "https://openalex.org/I4405260830": "odbrane",  # "University of Defence in Belgrade, Strategic Research Institute"
}

# Manual OUT_OF_SCOPE calls, each with its own stated reason: government/regulatory/payment bodies that do
# not hire researchers (PM's own explicit category), the one known geo error, and two honorary/membership
# bodies with no real job postings to speak of.
OUT_OF_SCOPE = {
    "https://openalex.org/I63327873": "OpenAlex geo error: gulfuniversity.edu.bh is in Bahrain (.bh), not Serbia.",
    "https://openalex.org/I4210094076": "Economic development/investment promotion agency, not a research employer.",
    "https://openalex.org/I4210124776": "Provincial government secretariat (funding/policy body), not a research employer.",
    "https://openalex.org/I4210154129": "National ministry (Interior), not a research employer.",
    "https://openalex.org/I4210145743": "Regulatory agency (telecom/postal), not a research employer.",
    "https://openalex.org/I4210122164": "National ministry (Science/Tech policy and funding body), not itself a research employer.",
    "https://openalex.org/I4210120180": "National ministry, appears defunct/renamed (superseded by the Ministry of Science, "
                                        "Technological Development and Innovation above), not a research employer.",
    "https://openalex.org/I4210160973": "Homepage resolves to the Customs Administration (upravacarina.rs); a government "
                                        "revenue/customs body, not a research employer.",
    "https://openalex.org/I4210137538": "Government infrastructure directorate (inland waterways authority), not a research employer.",
    "https://openalex.org/I4405271826": "Provincial government secretariat (higher-ed policy/funding body), not a research employer.",
    "https://openalex.org/I4405272650": "National ministry (Defence), not itself a research employer - its own real "
                                        "research/teaching units (University of Defence, Military Academy) are already "
                                        "separately seeded (odbrane, va).",
    "https://openalex.org/I4210117259": "National health insurance fund (payment/regulatory body), not a research employer.",
    "https://openalex.org/I4210104662": "Wikimedia movement affiliate (open-content advocacy NGO), not a research employer.",
    "https://openalex.org/I4210136274": "Professional/medical society (membership body), not an employer with job postings.",
    "https://openalex.org/I4210127331": "Honorary professional academy (membership body), not an employer with job postings.",
}

# Degree-granting HEIs among the MISSING orgs (OpenAlex type=education, genuinely a university/college/
# academy, not a mislabeled research institute) -> org_master.type='university'.
UNIVERSITY_NAMES = {
    "University of Arts in Belgrade", "Technical College of Applied Sciences", "Sportska Akademija Beograd",
    "Univerzitet u Novom Pazaru", "Belgrade Open School", "European University", "Novi Sad Open University",
    "ITS - Visoka škola strukovnih studija za informacione tehnologije",
    "Belgrade Business and Arts Academy of Applied Studies", "Academy of Applied Studies Šabac",
    "Academy of Applied Studies Belgrade", "Academy of Technical and Art Applied Studies",
    "Academy of Applied Studies Southern Serbia", "Akademija strukovnih studija Politehnika",
    "Visoka strukovna vaspitačka i medicinska škola u Vršcu", "Western Serbia Academy of Applied Studies",
    "Beopolis University",
}
COMPANY_NAMES = {"Institut za ispitivanje materijala IMS a.d. Beograd"}          # a real "a.d." (joint-stock company)
FUNDING_BODY_NAMES = {"Innovation Fund of the Republic of Serbia"}              # org_master.type='funding_body' exists for exactly this


def norm_host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return re.sub(r"^www\d?\.", "", h)


def slug_for(name: str, homepage: str, existing_slugs: set) -> str:
    host = norm_host(homepage)
    label = re.sub(r"\.(rs|com|org|net|info|co)$", "", host.split(".")[0])
    label = re.sub(r"[^a-z0-9]+", "", label.lower()) or re.sub(r"[^a-z0-9]+", "", host.replace(".", ""))
    if name in UNIVERSITY_NAMES:
        prefix = "private"
    elif name in COMPANY_NAMES:
        prefix = "company"
    elif name in FUNDING_BODY_NAMES:
        prefix = "fund"
    else:
        prefix = "inst"
    base = slug = f"{prefix}-{label}"
    n = 2
    while slug in existing_slugs:
        slug = f"{base}{n}"
        n += 1
    existing_slugs.add(slug)
    return slug


def type_for(name: str) -> str:
    if name in UNIVERSITY_NAMES:
        return "university"
    if name in COMPANY_NAMES:
        return "company"
    if name in FUNDING_BODY_NAMES:
        return "funding_body"
    return "institute"


def reconcile() -> dict:
    seed = orgs_mod.load_seed()
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))["institutions"]
    seed_host_to_slug = {norm_host(o["homepage"]): o["slug"] for o in seed if o.get("homepage")}

    oa_rows = [{"id": r["id"], "display_name": r["name"], "display_name_alternatives": r.get("aliases") or []}
              for r in raw]
    name_match = orgs_mod.match_openalex(seed, oa_rows, threshold=85.0, margin=3.0)
    name_match_by_oaid = {m["openalex_id"]: m["slug"] for m in name_match["matches"]}

    represented, consolidated, out_of_scope, missing = [], [], [], []
    for r in raw:
        oaid = r["id"]
        host = norm_host(r["homepage"]) if r.get("homepage") else None
        host_slug = seed_host_to_slug.get(host) if host else None
        name_slug = name_match_by_oaid.get(oaid)
        if host_slug or name_slug:
            represented.append({"openalex_id": oaid, "name": r["name"], "seed_slug": host_slug or name_slug,
                                "matched_via": "host" if host_slug else "name"})
        elif oaid in CONSOLIDATED:
            consolidated.append({"openalex_id": oaid, "name": r["name"], "parent_slug": CONSOLIDATED[oaid]})
        elif oaid in OUT_OF_SCOPE:
            out_of_scope.append({"openalex_id": oaid, "name": r["name"], "reason": OUT_OF_SCOPE[oaid]})
        else:
            missing.append(r)

    existing_slugs = {o["slug"] for o in seed}
    supplement = []
    for r in missing:
        slug = slug_for(r["name"], r["homepage"], existing_slugs)
        supplement.append({
            "slug": slug, "name_native": None, "name_en": r["name"], "city": r.get("city"),
            "homepage": r["homepage"], "host": norm_host(r["homepage"]), "career_urls": [],
            "contact_email": None, "contact_phone": None, "type": type_for(r["name"]),
            "render_mode": None, "robots_status": None, "robots_checked_at": None,
            "source_url": "pm/recon/t12_openalex_rs_institutions_raw.json",
            "notes": f"T20 OpenAlex reconciliation supplement (2026-09-23): added from the 119 in-scope RS "
                    f"institutions list, not previously in the seed. openalex_id={r['id']}, "
                    f"openalex works_count={r['works_count']}.",
            "crawl_config": {}, "foreigner_eligible_default": None,
            "openalex_id": r["id"], "openalex_works_count": r["works_count"],
        })

    rows = (
        [{"openalex_id": r["openalex_id"], "name": r["name"], "classification": "REPRESENTED",
          "seed_slug": r["seed_slug"], "matched_via": r["matched_via"]} for r in represented]
        + [{"openalex_id": r["openalex_id"], "name": r["name"], "classification": "CONSOLIDATED",
            "parent_slug": r["parent_slug"]} for r in consolidated]
        + [{"openalex_id": r["openalex_id"], "name": r["name"], "classification": "OUT_OF_SCOPE",
            "reason": r["reason"]} for r in out_of_scope]
        + [{"openalex_id": r["id"], "name": r["name"], "classification": "MISSING",
            "added_as_slug": s["slug"], "works_count": r["works_count"]} for r, s in zip(missing, supplement)]
    )
    counts = {
        "total_openalex_rs_institutions": len(raw), "represented": len(represented),
        "consolidated": len(consolidated), "out_of_scope": len(out_of_scope), "missing_added": len(missing),
    }
    counts["identity_check"] = (counts["represented"] + counts["consolidated"] + counts["out_of_scope"]
                                + counts["missing_added"]) == counts["total_openalex_rs_institutions"]

    return {
        "reconciliation": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "pm/recon/t12_openalex_rs_institutions_raw.json (119 institutions)",
            "method": "Join 1: exact host match (normalized, www-stripped) against the existing seed. "
                     "Join 2: fuzzy name+alias match (ajp.orgs.match_openalex, token_sort_ratio, "
                     "threshold=85, margin=3 - conservative, no guessed matches). Everything neither join "
                     "resolves is CONSOLIDATED, OUT_OF_SCOPE, or MISSING (see this module's own docstring). "
                     "Host-SUFFIX matching was tried and rejected - see docstring.",
            "counts": counts, "rows": rows,
        },
        "supplement": {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "count": len(supplement), "orgs": supplement},
    }


def main() -> int:
    result = reconcile()
    RECON_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECON_OUT_PATH.write_text(json.dumps(result["reconciliation"], indent=1, ensure_ascii=False), encoding="utf-8")
    SUPPLEMENT_OUT_PATH.write_text(json.dumps(result["supplement"], indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {RECON_OUT_PATH}")
    print(f"wrote {SUPPLEMENT_OUT_PATH}")
    print(json.dumps(result["reconciliation"]["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
