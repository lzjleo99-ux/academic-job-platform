"""ECB euro foreign exchange reference rates: fetch, parse, snapshot, upsert.

Feeds (all www.ecb.europa.eu/stats/eurofxref/):
    daily  eurofxref-daily.xml       latest working day, published ~16:00 CET
    90d    eurofxref-hist-90d.xml    last 90 days (gap repair)
    hist   eurofxref-hist.xml        everything since 1999 (initial backfill)

ECB quotes "units of currency per 1 EUR"; that is stored as fx_rate.per_eur unchanged.
ECB does not publish RSD (verified 2026-09-22, daily feed + data-api); see fx_rate migration.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, List, Optional

from defusedxml import ElementTree as ET

from ajp.connectors.base import FetchFailed, FetchResult, PoliteClient, UrlManifest
from ajp.fx.common import FxObs, upsert_rates
from ajp.rawstore import LocalFsStore, ObjectStore, default_store, put_raw

log = logging.getLogger("ajp.fx.ecb")

BASE = "https://www.ecb.europa.eu/stats/eurofxref/"
FEEDS = {"daily": "eurofxref-daily.xml", "90d": "eurofxref-hist-90d.xml", "hist": "eurofxref-hist.xml"}
SOURCE = "ECB"
RAW_SOURCE_ID = "ecb_fx"
MIN_CURRENCIES = 20  # ECB publishes ~30; far fewer means the format changed or the feed is truncated


def parse_ecb_xml(xml: bytes) -> List[FxObs]:
    """Parse any of the three feeds (they share the Cube/Cube[@time]/Cube[@currency] shape)."""
    root = ET.fromstring(xml)
    out: List[FxObs] = []
    for day in root.iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
        t = day.get("time")
        if not t:
            continue
        d = date.fromisoformat(t)
        for c in day:
            cur, rate = c.get("currency"), c.get("rate")
            # ECB marks unavailable rates as 'N/A' in the historical file: skip, never fill.
            if not cur or not rate or rate == "N/A":
                continue
            out.append(FxObs(d, cur, Decimal(rate)))
    if not out:
        raise ValueError("no rates found in ECB XML")
    return out


def validate(obs: Iterable[FxObs], feed: str) -> None:
    obs = list(obs)
    latest = max(o.rate_date for o in obs)
    todays = {o.currency for o in obs if o.rate_date == latest}
    if feed == "daily" and len(todays) < MIN_CURRENCIES:
        raise ValueError(f"only {len(todays)} currencies on {latest}; expected >= {MIN_CURRENCIES}")
    if any(o.per_eur <= 0 for o in obs):
        raise ValueError("non-positive rate in feed")


def fetch(feed: str = "daily", polite: Optional[PoliteClient] = None) -> FetchResult:
    polite = polite or PoliteClient()
    url = BASE + FEEDS[feed]
    res = polite.get(url, manifest=UrlManifest.exact(url))
    if res.status != 200:
        raise FetchFailed(f"HTTP {res.status} for {url}")
    return res


def run(
    dsn: str,
    feed: str = "daily",
    store: Optional[ObjectStore] = None,
    polite: Optional[PoliteClient] = None,
) -> dict:
    """Fetch one feed, keep the raw XML, upsert rates. Idempotent; safe to run repeatedly."""
    store = store or default_store()
    resp = fetch(feed, polite)
    obs = parse_ecb_xml(resp.content)
    validate(obs, feed)
    ref = put_raw(
        store, kind="xml", source_id=RAW_SOURCE_ID, url=BASE + FEEDS[feed], content=resp.content,
        fetched_at=resp.fetched_at, status=resp.status, content_type=resp.content_type,
        final_url=resp.final_url, robots_allowed=resp.robots_allowed, extra={"feed": feed},
    )
    written = upsert_rates(dsn, obs, SOURCE, ref.key)
    latest = max(o.rate_date for o in obs)
    age_days = (datetime.now(timezone.utc).date() - latest).days
    if age_days > 5:
        log.warning("latest ECB rate date %s is %d days old", latest, age_days)
    return {
        "feed": feed, "rows_parsed": len(obs), "rows_written_or_changed": written,
        "latest_rate_date": latest.isoformat(), "currencies_on_latest":
            sorted({o.currency for o in obs if o.rate_date == latest}), "raw_key": ref.key,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot ECB reference rates into fx_rate")
    ap.add_argument("--feed", choices=sorted(FEEDS), default="daily")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "data/raw"))
    args = ap.parse_args(argv)
    if not args.dsn:
        ap.error("set DATABASE_URL or pass --dsn")
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(args.dsn, args.feed, LocalFsStore(args.raw_root)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
