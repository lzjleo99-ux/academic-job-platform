"""T48 (packet 028): one-off backfill of listing.kind_conf/kind_raw/position_kind_conf/position_kind_raw
(migration 0026) for every row already in the live DB before those columns existed, and optionally seed
the human review queue from the result.

Uses the SAME functions the connectors now call at ingest (ajp.extract.rs_text.kind_row_fields /
ajp.extract.intl_text.kind_row_fields) - never a second, drifting copy of the classification - so a row
backfilled by this script and a row ingested tomorrow always agree on what the evidence is.

IMPORTANT, AND DELIBERATE: this recomputes the EVIDENCE, it does not re-decide the kind. `listing.kind`
itself is left exactly as the connector wrote it, because several connectors legitimately override the
classifier (a source's configured default_kind, a board category, an academic_ad gate, nsz_pdf's "an entry
of the job publication is a job ad"). Re-running a bare classifier here would silently undo all of those.
So the recomputed kind is compared against the stored one and the evidence is only attached where they
agree - the same rule the connectors apply live via ajp.extract.evidence.with_kind_override. Where they
disagree, the row keeps NULL evidence, which is the honest answer: the kind on that row was not produced
by the classifier, so there is no classifier excerpt to attach to it.

Safe to re-run (recomputes and overwrites, never duplicates). Re-run it whenever the confidence tiers in
rs_text/intl_text change, so persisted numbers always match the current model.

    python tools/backfill_classification_evidence.py [--data-dir PATH] [--batch-size N] [--queue] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import psycopg

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ajp.classification_review import queue_classification  # noqa: E402
from ajp.extract import rs_text  # noqa: E402
from ajp.extract.intl_text import (classify_kind_intl_with_evidence,  # noqa: E402
                                   position_kind_intl_with_evidence)
from ajp.cli.init import DEFAULT_DATA_DIR  # noqa: E402


def _is_serbian(ad_lang: str | None, title: str) -> bool:
    """Which classifier produced this row's kind. ad_lang is the connectors' own recorded answer
    ('sr-Latn'/'sr-Cyrl' from rs_text.script_of, or 'en'/'fr'/... from the international connectors);
    where it is NULL (jobposting leaves it to the caller) the script of the title itself decides."""
    if ad_lang:
        return ad_lang.startswith("sr")
    return rs_text.script_of(title or "") in ("sr-Cyrl", "sr-Latn")


def backfill(dsn: str, batch_size: int = 500, queue: bool = False, dry_run: bool = False) -> dict:
    stats: Counter = Counter()
    updates, queue_rows = [], []
    with psycopg.connect(dsn, autocommit=not dry_run) as conn:
        rows = conn.execute(
            """SELECT id, title_raw, raw_text, kind, position_kind, ad_lang, org_id
                 FROM listing ORDER BY id""").fetchall()
        for listing_id, title, body, kind, position_kind, ad_lang, org_id in rows:
            stats["rows"] += 1
            title = title or ""
            body = body or ""
            if _is_serbian(ad_lang, title):
                k_ev = rs_text.classify_kind_with_evidence(f"{title} {body}", title=title)
                pk_ev = rs_text.position_kind_with_evidence(title)[0]
            else:
                k_ev = classify_kind_intl_with_evidence(title, body)
                pk_ev = position_kind_intl_with_evidence(title, body)
            # Only attach evidence the classifier actually produced for THIS kind (see module docstring).
            f = {"kind": k_ev.kind, "kind_conf": k_ev.conf, "kind_raw": k_ev.raw,
                 "position_kind": pk_ev.kind, "position_kind_conf": pk_ev.conf,
                 "position_kind_raw": pk_ev.raw}
            if f["kind"] != kind:
                f["kind_conf"] = f["kind_raw"] = None
                stats["kind_disagrees"] += 1
            if f["position_kind"] != position_kind:
                f["position_kind_conf"] = f["position_kind_raw"] = None
                stats["position_kind_disagrees"] += 1
            if f["kind_conf"] is None and f["position_kind_conf"] is None:
                stats["no_evidence"] += 1
            else:
                stats["with_evidence"] += 1
            updates.append((f["kind_conf"], f["kind_raw"], f["position_kind_conf"], f["position_kind_raw"],
                            listing_id))
            if queue:
                # computed HERE, inside the per-row loop: `title`/`body` are per-row locals, and reading
                # them again in the queueing pass below would silently use the LAST row's text for every
                # row in the DB (a real bug caught on the live run - it emptied this bucket entirely).
                academic = org_id is not None or rs_text.is_academic(f"{title} {body[:300]}")
                queue_rows.append((listing_id, k_ev, org_id, kind, f, academic))
        if dry_run:
            print(f"[dry-run] would update {len(updates)} rows; queue={queue}")
            return dict(stats)
        cur = conn.cursor()
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            cur.executemany(
                """UPDATE listing SET kind_conf=%s, kind_raw=%s, position_kind_conf=%s,
                   position_kind_raw=%s WHERE id=%s""", batch)
            stats["written"] += len(batch)
            print(f"  ... {stats['written']}/{len(updates)}")
        # A pending row carries NO human decision (a decided one is protected by queue_classification's
        # own NOT EXISTS guard), so clearing them costs nothing and is what keeps the worklist honest:
        # without it, a row queued under an older tier or an older threshold would linger forever as
        # "worth reviewing" even after the classifier stopped doubting it.
        deleted = conn.execute("DELETE FROM classification_review WHERE status = 'pending'").rowcount
        if deleted:
            stats["stale_pending_cleared"] = deleted
        for listing_id, k_ev, org_id, kind, evidence, academic in queue_rows:
            # Queued against the kind actually ON THE ROW, not against this script's recomputation. Found
            # live: rows whose stored kind='employment' came from board.py's academic_ad() fallback while a
            # bare re-classification of the same text says 'other' - queueing the recomputed value showed a
            # reviewer "classified_as=other" on a listing that says employment, i.e. a contradiction rather
            # than a work item. The evidence is only carried when it belongs to that kind (see above).
            # academic_context = something on the row says "academic" even though no classification rule
            # fired: a known academic employer (org_master), or a real academic word in the ad's own text
            # (rs_text.is_academic - fakultet/institut/univerzitet/nauc/istrazivac/...). This is what keeps
            # 'unclassified_academic' a worklist instead of every unmatched row in the DB: measured on the
            # live DB it selects 217 of 2,035 kind='other' rows, and they include genuine misses such as a
            # real 'Chemical and Process Engineering | Faculty Openings' ad sitting unclassified.
            reason = queue_classification(
                conn, listing_id, "kind", kind=kind, conf=evidence["kind_conf"], raw=evidence["kind_raw"],
                # The rule belongs to the stored kind only if the two agree. NOT gated on conf: an
                # EXCLUSION has conf=None by definition yet is exactly the decision worth reviewing
                # (gating on conf silently emptied the whole excluded_by_rule bucket on the first run).
                rule=k_ev.rule if kind == k_ev.kind else None,
                zone=k_ev.zone, guards=k_ev.guards, academic_context=academic)
            if reason:
                stats[f"queued_{reason}"] += 1
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--queue", action="store_true", help="also seed classification_review from the result")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import pgserver
    server = pgserver.get_server(str(Path(args.data_dir) / "pgdata"))
    stats = backfill(server.get_uri(), batch_size=args.batch_size, queue=args.queue, dry_run=args.dry_run)
    for k in sorted(stats):
        print(f"{k:28s} {stats[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
