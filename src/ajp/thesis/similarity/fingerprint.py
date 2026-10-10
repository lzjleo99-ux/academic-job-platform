"""T17 fingerprints: 5-token word shingles, MinHash (datasketch if it installs - confirmed live to install
cleanly on this project's py3.9 target - else a small pure-Python MinHash with the identical interface, per
packet 008's own instruction), and an exact-hash set for verbatim detection.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence

SHINGLE_SIZE = 5
NUM_PERM = 64

try:
    from datasketch import MinHash as _DatasketchMinHash
    HAVE_DATASKETCH = True
except ImportError:                                            # pragma: no cover - exercised only when absent
    HAVE_DATASKETCH = False


def word_shingles(normalized_text: str, k: int = SHINGLE_SIZE) -> List[str]:
    """``normalized_text`` must already be ajp.thesis.similarity.normalize.normalize()'d (NFKC, transliterated,
    lowercased) - shingling raw text would make script/case variants fingerprint differently, defeating the
    whole point of normalizing first."""
    tokens = normalized_text.split()
    if len(tokens) < k:
        return [normalized_text] if normalized_text else []
    return [" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)]


def exact_hash(normalized_text: str) -> str:
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


class _PureMinHash:
    """k independent hash functions (salted sha256) tracking the minimum per function - the same estimator
    datasketch implements, just without the numpy dependency. Interface-compatible with datasketch.MinHash
    for this module's own purposes (update(bytes), jaccard(other))."""

    def __init__(self, num_perm: int = NUM_PERM, seed: int = 1):
        self.num_perm = num_perm
        self._seed = seed
        self._mins = [None] * num_perm

    def update(self, data: bytes) -> None:
        token = data.decode("utf-8", "replace")
        for i in range(self.num_perm):
            h = int(hashlib.sha256(f"{self._seed}:{i}:{token}".encode("utf-8")).hexdigest(), 16)
            if self._mins[i] is None or h < self._mins[i]:
                self._mins[i] = h

    def jaccard(self, other: "_PureMinHash") -> float:
        pairs = [(a, b) for a, b in zip(self._mins, other._mins) if a is not None and b is not None]
        if not pairs:
            return 0.0
        matches = sum(1 for a, b in pairs if a == b)
        return matches / len(pairs)


def new_minhash(num_perm: int = NUM_PERM):
    return _DatasketchMinHash(num_perm=num_perm) if HAVE_DATASKETCH else _PureMinHash(num_perm=num_perm)


@dataclass
class Fingerprint:
    exact_hashes: "set[str]"          # per-shingle exact hash - verbatim detection
    minhash: object                   # datasketch.MinHash or _PureMinHash - Jaccard estimate for paraphrase band
    shingle_count: int


def fingerprint_text(normalized_text: str, k: int = SHINGLE_SIZE, num_perm: int = NUM_PERM) -> Fingerprint:
    shingles = word_shingles(normalized_text, k)
    mh = new_minhash(num_perm)
    hashes = set()
    for sh in shingles:
        mh.update(sh.encode("utf-8"))
        hashes.add(exact_hash(sh))
    return Fingerprint(exact_hashes=hashes, minhash=mh, shingle_count=len(shingles))


def jaccard(a: Fingerprint, b: Fingerprint) -> float:
    return a.minhash.jaccard(b.minhash)


def exact_overlap_ratio(a: Fingerprint, b: Fingerprint) -> float:
    """Fraction of ``a``'s shingles that also appear verbatim in ``b`` - the containment signal used for
    verbatim detection (asymmetric: 'how much of MY text is found in the source', not a symmetric Jaccard)."""
    if not a.exact_hashes:
        return 0.0
    return len(a.exact_hashes & b.exact_hashes) / len(a.exact_hashes)
