"""Rule-based salary span extraction (first cut for T8; extended for sr-Latn / sr-Cyrl there).

Only amounts written next to an explicit currency are taken. The raw text window is always kept
and confidence is a heuristic in [0, 0.95]: 0.5 for a currency-adjacent amount, +0.2 stated
period, +0.15 gross/net stated, +0.1 salary-type keyword nearby, -0.15 if hedged ("~", "approx.").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

_CUR = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "usd": "USD", "£": "GBP",
        "gbp": "GBP", "chf": "CHF", "rsd": "RSD", "din": "RSD", "din.": "RSD", "dinara": "RSD"}
_CURRENCY = r"€|£|\$|\b(?:EUR|USD|GBP|CHF|RSD|euros?|dinara|din\.?)(?![A-Za-z])"
_NUM = r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT = rf"(?:(?P<pre>{_CURRENCY})\s*)?(?P<num>{_NUM})\s*(?P<k>k\b)?(?:\s*(?P<post>{_CURRENCY}))?"
_RANGE = re.compile(
    rf"(?P<approx>~|≈|approx\.?|approximately|about|circa)?\s*(?P<pre>{_CURRENCY})?\s*(?P<n1>{_NUM})\s*(?P<k1>k\b)?\s*"
    rf"(?:(?:-|–|—|to)\s*(?P<n2>{_NUM})\s*(?P<k2>k\b)?)?\s*(?P<post>{_CURRENCY})?", re.I)
_PERIOD = [("year", r"per\s+year|per\s+annum|annual(?:ly)?|yearly|/\s*year|p\.?a\.?\b|godišnje|godisnje"),
           ("month", r"per\s+month|monthly|/\s*month|mesečno|mesecno|mjesečno|per\s+mo\b"),
           ("hour", r"per\s+hour|hourly|/\s*h(?:our)?\b|po\s+satu")]
_GROSS = re.compile(r"\bgross\b|\bbrutto\b|\bbruto\b", re.I)
_NET = re.compile(r"\bnet\b|\bnetto\b|\bneto\b", re.I)
_LOWER_BOUND = re.compile(r"\bfrom\b|\bat least\b|\bminimum\b|\bstarting( at| from)?\b", re.I)
_UPPER_BOUND = re.compile(r"\bup to\b|\bat most\b|\bmaximum\b", re.I)
_KEYWORD = re.compile(r"salary|stipend|remuneration|allowance|compensation|pay\b|plata|zarada|primanja", re.I)


@dataclass(frozen=True)
class SalaryHit:
    min: Optional[Decimal]
    max: Optional[Decimal]
    currency: str
    period: Optional[str]
    gross_net: Optional[str]
    conf: float
    raw: str


def _to_decimal(num: str, k: Optional[str]) -> Decimal:
    n = num.replace(" ", " ")
    m = re.fullmatch(r"(\d{1,3}(?:[., ]\d{3})+)([.,]\d{1,2})?", n)
    if m:                                             # thousands separators, optional decimals
        whole = re.sub(r"[., ]", "", m.group(1))
        dec = m.group(2)
        n = whole + ("." + dec[1:] if dec else "")
    else:
        n = n.replace(",", ".")
    v = Decimal(n)
    return v * 1000 if k else v


def extract_salary(text: str) -> Optional[SalaryHit]:
    best: Optional[SalaryHit] = None
    for m in _RANGE.finditer(text):
        cur_tok = m.group("pre") or m.group("post")
        if not cur_tok:
            continue
        cur = _CUR[cur_tok.lower()]
        lo = _to_decimal(m.group("n1"), m.group("k1"))
        hi = _to_decimal(m.group("n2"), m.group("k2") or m.group("k1")) if m.group("n2") else None
        if hi is not None and hi < lo:
            lo, hi = hi, lo
        if lo < 5:                                       # 'a 1 EUR fee' is not a salary
            continue
        a, b = max(0, m.start() - 60), min(len(text), m.end() + 80)
        while a > 0 and not text[a - 1].isspace() and a < m.start():   # snap to word boundaries
            a += 1
        while b < len(text) and not text[b].isspace() and b > m.end():
            b -= 1
        window = text[a:b]
        before = text[max(0, m.start() - 20):m.start()]
        if hi is None:
            if _LOWER_BOUND.search(before):
                pass                                    # "from X": lower bound only
            elif _UPPER_BOUND.search(before):
                lo, hi = None, lo                       # "up to X": upper bound only
            else:
                hi = lo                                 # a single stated figure is a point value
        conf = 0.5
        period = next((p for p, rx in _PERIOD if re.search(rx, window, re.I)), None)
        if period:
            conf += 0.2
        gross_net = "gross" if _GROSS.search(window) and not _NET.search(window) else \
                    "net" if _NET.search(window) and not _GROSS.search(window) else None
        if gross_net:
            conf += 0.15
        if _KEYWORD.search(window):
            conf += 0.1
        if m.group("approx"):
            conf -= 0.15
        hit = SalaryHit(lo, hi, cur, period, gross_net, round(min(max(conf, 0.0), 0.95), 2),
                        re.sub(r"\s+", " ", window).strip())
        if best is None or hit.conf > best.conf:
            best = hit
    return best
