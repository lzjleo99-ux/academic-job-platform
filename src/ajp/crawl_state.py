"""Run-level retry counter (R1) persisted in ``crawl_state``.

    transient failure (network, TLS, 5xx, robots.txt unreachable): counted once per run_id; on the 3rd consecutive
                        failed run the key becomes blocked with the error history
    permanent (robots.txt disallows): blocked immediately, no retries
    success:            counter and block are cleared
    robots 404/410 is not a failure at all (default allow).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import psycopg

BLOCK_AFTER_RUNS = 3
HISTORY_KEEP = 10


class CrawlState:
    def __init__(self, conn: psycopg.Connection, source_id: str, run_id: str):
        self.conn, self.source_id, self.run_id = conn, source_id, run_id

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""SELECT consecutive_failures, last_run_id, last_error, blocked_reason, history
                                     FROM crawl_state WHERE source_id=%s AND key=%s""", (self.source_id, key))
        r = cur.fetchone()
        return None if r is None else dict(zip(("consecutive_failures", "last_run_id", "last_error", "blocked_reason", "history"), r))

    def blocked_reason(self, key: str) -> Optional[str]:
        s = self.get(key)
        return s["blocked_reason"] if s else None

    def ok(self, key: str) -> None:
        self.conn.execute("""INSERT INTO crawl_state (source_id, key, consecutive_failures, last_run_id, last_attempt_at, last_success_at)
                             VALUES (%s,%s,0,%s,now(),now())
                             ON CONFLICT (source_id, key) DO UPDATE
                               SET consecutive_failures=0, blocked_reason=NULL, last_error=NULL, last_run_id=EXCLUDED.last_run_id,
                                   last_attempt_at=now(), last_success_at=now(), updated_at=now()""", (self.source_id, key, self.run_id))

    def fail(self, key: str, error: str, permanent: bool = False) -> Dict[str, Any]:
        """Record a failed attempt; returns the new state. Several failures within one run count once."""
        s = self.get(key) or {"consecutive_failures": 0, "last_run_id": None, "history": [], "blocked_reason": None}
        same_run = s["last_run_id"] == self.run_id
        n = s["consecutive_failures"] + (0 if same_run else 1)
        hist, entry = list(s["history"]), {"run": self.run_id, "error": error[:200]}
        if hist and same_run:
            hist[-1] = entry                       # a repeat within one run refreshes the entry instead of adding one
        else:
            hist.append(entry)
        hist = hist[-HISTORY_KEEP:]
        reason = s["blocked_reason"]
        if permanent:
            reason = f"robots_disallowed: {error[:120]}"
        elif n >= BLOCK_AFTER_RUNS:
            reason = f"unreachable_after_{n}_runs: {error[:120]}"
        self.conn.execute("""INSERT INTO crawl_state (source_id, key, consecutive_failures, last_run_id, last_error, last_attempt_at, blocked_reason, history)
                             VALUES (%s,%s,%s,%s,%s,now(),%s,%s)
                             ON CONFLICT (source_id, key) DO UPDATE
                               SET consecutive_failures=EXCLUDED.consecutive_failures, last_run_id=EXCLUDED.last_run_id,
                                   last_error=EXCLUDED.last_error, last_attempt_at=now(), blocked_reason=EXCLUDED.blocked_reason,
                                   history=EXCLUDED.history, updated_at=now()""",
                          (self.source_id, key, n, self.run_id, error[:300], reason, json.dumps(hist)))
        return {"consecutive_failures": n, "blocked_reason": reason, "history": hist}
