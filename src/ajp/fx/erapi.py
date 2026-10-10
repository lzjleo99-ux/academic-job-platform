"""open.er-api.com keyless daily rates: the RSD fallback until NBS access exists (packet 002).

Indicative only (source='ERAPI', lowest precedence). Terms (exchangerate-api.com/docs/free,
read 2026-09-22): attribution link "Rates By Exchange Rate API" required wherever a converted
figure is shown; caching allowed; redistributing the raw rates is not (expose derived EUR
salaries, not the rate table); one request per 24h is always within limits.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest
from ajp.fx.common import FxObs, upsert_rates
from ajp.rawstore import ObjectStore, default_store, put_raw

URL = "https://open.er-api.com/v6/latest/EUR"
SOURCE = "ERAPI"
RAW_SOURCE_ID = "erapi_fx"
ATTRIBUTION_HTML = '<a href="https://www.exchangerate-api.com">Rates By Exchange Rate API</a>'
MIN_CURRENCIES = 100  # ~166 published


def parse_erapi(body: bytes) -> List[FxObs]:
    j = json.loads(body, parse_float=Decimal)
    if j.get("result") != "success" or j.get("base_code") != "EUR":
        raise ValueError(f"unexpected er-api response: result={j.get('result')!r} base={j.get('base_code')!r}")
    rates = j.get("rates") or {}
    if len(rates) < MIN_CURRENCIES:
        raise ValueError(f"only {len(rates)} currencies; expected >= {MIN_CURRENCIES}")
    d = datetime.fromtimestamp(int(j["time_last_update_unix"]), tz=timezone.utc).date()
    out = []
    for cur, rate in rates.items():
        if cur == "EUR":
            continue
        rate = Decimal(str(rate))
        if rate <= 0:
            raise ValueError(f"non-positive rate for {cur}")
        out.append(FxObs(d, cur, rate))
    return out


def run(dsn: str, store: Optional[ObjectStore] = None, polite: Optional[PoliteClient] = None) -> dict:
    store = store or default_store()
    polite = polite or PoliteClient()
    resp = polite.get(URL, manifest=UrlManifest.exact(URL))
    if resp.status != 200:
        raise FetchFailed(f"HTTP {resp.status} for {URL}")
    obs = parse_erapi(resp.content)
    ref = put_raw(store, kind="json", source_id=RAW_SOURCE_ID, url=URL, content=resp.content,
                  fetched_at=resp.fetched_at, status=resp.status, content_type=resp.content_type,
                  final_url=resp.final_url, robots_allowed=resp.robots_allowed,
                  extra={"terms": "https://www.exchangerate-api.com/terms", "attribution_required": True,
                         "confidence": "indicative third-party rate; lower than central-bank sources"})
    written = upsert_rates(dsn, obs, SOURCE, ref.key)
    return {"provider": "erapi", "rows_parsed": len(obs), "rows_written_or_changed": written,
            "latest_rate_date": obs[0].rate_date.isoformat(), "raw_key": ref.key,
            "has_RSD": any(o.currency == "RSD" for o in obs)}
