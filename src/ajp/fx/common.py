from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

import psycopg


@dataclass(frozen=True)
class FxObs:
    rate_date: date
    currency: str
    per_eur: Decimal  # units of `currency` per 1 EUR


_UPSERT = """
INSERT INTO fx_rate (rate_date, currency, per_eur, source, raw_ref)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (rate_date, currency, source) DO UPDATE
   SET per_eur = EXCLUDED.per_eur, raw_ref = EXCLUDED.raw_ref, fetched_at = now()
 WHERE fx_rate.per_eur IS DISTINCT FROM EXCLUDED.per_eur
"""


def upsert_rates(dsn: str, obs: Iterable[FxObs], source: str, raw_ref: str) -> int:
    """Insert new rates / update corrected ones; return rows inserted or changed."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, [(o.rate_date, o.currency, o.per_eur, source, raw_ref) for o in obs])
            written = cur.rowcount
        conn.commit()
    return written
