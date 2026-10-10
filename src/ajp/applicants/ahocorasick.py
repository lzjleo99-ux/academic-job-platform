"""T41 (packet 024, P1 perf): a small, pure-Python, vendored Aho-Corasick multi-pattern automaton - no new
third-party dependency, per PM's own explicit instruction. Replaces the per-applicant alternation REGEX
`skill_relevance` used to build (one combined `\\b(p1|p2|...)\\b` pattern, T36f/T37/T40's own history) - a
regex alternation's own matching cost scales with the NUMBER of alternatives tried at each text position;
Aho-Corasick's own cost is O(text length) per scan, independent of how many patterns it holds, which is
the fix this task needs (T40's own cluster expansion grew the typical pattern count from ~11 to ~27,
pushing `POST /matches` slightly over PM's own 1.0s median target).

**Found live building this exact task, not guessed**: the textbook "fail-link-walk" Aho-Corasick scan
(follow `fail` pointers in a `while` loop until a transition exists, standard presentation of the 1975
algorithm) was measured SLOWER than the existing regex engine for this project's actual vocabulary/text
sizes - cProfile showed it as the dominant cost, worse than before (~1.8-1.9s vs ~1.05-1.2s). The root
cause: a pure-Python per-character `while` loop carries real CPython interpreter overhead (attribute
lookups, a `dict.get` call, possible multiple fail-chain hops) that the regex engine's own native-C
alternation matching simply does not pay, even though the regex engine's own complexity scales with the
NUMBER of patterns - at ~27 patterns this project's own real vocabulary never got big enough for AC's
better asymptotic complexity to overcome Python's own per-character constant-factor cost. The fix actually
shipped here is the STANDARD "complete/deterministic" Aho-Corasick construction (still pure Python, still
the exact same algorithm family, just a different, well-known implementation strategy): precompute, once
per automaton (not once per scanned character), a full transition table covering every character that
appears anywhere in the pattern set, for every state - this removes the fail-chain `while` loop from the
hot scanning loop entirely, reducing each scan step to exactly one dict lookup. Confirmed this is actually
faster by measuring it directly (see T41's own done event for the real numbers), not assumed.

This module is a general-purpose automaton with no project-specific knowledge at all (zones, weights,
tilde reasons, stoplists - none of that lives here); `ajp.applicants.matching` is the ONLY caller, and is
responsible for everything project-specific, including the exact-word-boundary filtering and the greedy,
leftmost-longest, non-overlapping match selection that reproduces `re.finditer(alternation, text)`'s own
real behavior byte-for-byte (see matching.py's own comments for why each piece of that replication matters).
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, Iterator, List, Tuple

_ROOT = 0


class _Node:
    __slots__ = ("children", "fail", "output")

    def __init__(self) -> None:
        self.children: Dict[str, int] = {}
        self.fail: int = _ROOT
        self.output: List[str] = []    # every pattern ending exactly at this node, INCLUDING via fail links
                                       # (merged in at build time, so a scan never has to walk the fail
                                       # chain itself - see _build_fail_links's own comment).


class AhoCorasick:
    """Case-sensitive on its own input (the caller lowercases both patterns and the haystack - matching.py
    does this exactly once per applicant/per listing-field, the same convention the old regex path's own
    `re.IGNORECASE` always implied). Built once per fixed pattern set (cached by the caller exactly like
    the old compiled regex was); `iter_matches_lowercase` is then called many times (once per listing field)
    against that one automaton."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self._nodes: List[_Node] = [_Node()]
        for p in patterns:
            if p:
                self._insert(p)
        self._build_fail_links()
        self._build_complete_goto()

    def _insert(self, pattern: str) -> None:
        cur = _ROOT
        for ch in pattern:
            nxt = self._nodes[cur].children.get(ch)
            if nxt is None:
                nxt = len(self._nodes)
                self._nodes.append(_Node())
                self._nodes[cur].children[ch] = nxt
            cur = nxt
        self._nodes[cur].output.append(pattern)

    def _build_fail_links(self) -> None:
        # Standard Aho-Corasick BFS construction (Aho & Corasick, 1975). Depth-1 nodes (root's own direct
        # children) always fail to the root - there is no shorter non-empty proper suffix for a single
        # character to fall back to. Every deeper node's fail link is found by walking its PARENT's own
        # fail chain looking for a node that already has a transition on the same character; `output` is
        # merged in from the fail target at construction time (not re-walked on every scan step).
        root = self._nodes[_ROOT]
        queue: "deque[int]" = deque()
        for child in root.children.values():
            self._nodes[child].fail = _ROOT
            queue.append(child)
        while queue:
            u = queue.popleft()
            u_node = self._nodes[u]
            for ch, v in u_node.children.items():
                queue.append(v)
                f = u_node.fail
                while f != _ROOT and ch not in self._nodes[f].children:
                    f = self._nodes[f].fail
                target = self._nodes[f].children.get(ch, _ROOT)
                self._nodes[v].fail = target if target != v else _ROOT
                self._nodes[v].output = self._nodes[v].output + self._nodes[self._nodes[v].fail].output

    def _build_complete_goto(self) -> None:
        # The perf-critical piece (see this module's own docstring for why it exists): a full transition
        # table, one dict per node, covering every character that appears ANYWHERE in the inserted pattern
        # set (the trie's own "alphabet") - computed ONCE here, in BFS order (every node's `fail` target is
        # at a strictly smaller BFS depth, so its own goto row is already complete by the time a later
        # node needs to read it). A character that is not in the alphabet at all can never be part of any
        # pattern, so it is deliberately left OUT of every row - `iter_matches_lowercase` below treats a
        # missing key as "go to root", a single `dict.get(ch, _ROOT)` call that is correct for both "this
        # exact state has no transition" (precomputed, real reason) and "this text character never
        # appears in any pattern at all" (the lookup's own default) without needing to tell the two apart.
        alphabet: "set[str]" = set()
        for node in self._nodes:
            alphabet.update(node.children.keys())

        order: List[int] = []
        visited = [False] * len(self._nodes)
        visited[_ROOT] = True
        queue: "deque[int]" = deque([_ROOT])
        while queue:
            u = queue.popleft()
            order.append(u)
            for v in self._nodes[u].children.values():
                if not visited[v]:
                    visited[v] = True
                    queue.append(v)

        # T41, measured directly: a flat LIST indexed by `ord(ch)` was ALSO tried here and found SLOWER
        # than this plain dict, not faster - the extra per-character Python-level work (an `ord()` call, a
        # subtraction, a bounds check) outweighed list-indexing's own theoretical edge over dict hashing, a
        # genuinely counter-intuitive result only found by actually measuring it rather than assuming the
        # "obvious" optimization would win. A single `dict.get(ch, _ROOT)` call remains the fastest form
        # found for this table.
        self._goto: List[Dict[str, int]] = [dict() for _ in self._nodes]
        for u in order:
            node = self._nodes[u]
            row = self._goto[u]
            fail_row = self._goto[node.fail] if u != _ROOT else None
            for ch in alphabet:
                direct = node.children.get(ch)
                if direct is not None:
                    row[ch] = direct
                elif u == _ROOT:
                    row[ch] = _ROOT
                else:
                    row[ch] = fail_row[ch]

    def iter_matches_lowercase(self, text_lower: str) -> Iterator[Tuple[int, int, str]]:
        """Yields (start, end, pattern) for EVERY occurrence of every pattern in `text_lower` - including
        overlapping ones, and with no word-boundary filtering at all (`text_lower` already has case folded
        out; boundary semantics need the ORIGINAL text, which only the caller has) - in left-to-right scan
        order by `end` position. The caller (matching.py) is responsible for boundary-filtering and for
        picking a non-overlapping, leftmost-longest subset of these, exactly reproducing what
        `re.finditer()` on an equivalent alternation pattern would have returned.

        This is the convenient, testable form (used by this module's own unit tests); the hot path
        (`scan_lowercase`, below) does the exact same thing without Python's own generator-protocol
        overhead - found live measuring this exact difference (T41, packet 024): a generator's per-`yield`
        suspend/resume cost was a larger share of the real per-character cost than the dict lookups
        themselves, for this project's own real text sizes."""
        yield from self.scan_lowercase(text_lower)

    def scan_lowercase(self, text_lower: str) -> List[Tuple[int, int, str]]:
        """The exact same matches `iter_matches_lowercase` yields, as a plain list built by one flat loop
        (no generator) - the hot path `ajp.applicants.matching` actually calls."""
        out: List[Tuple[int, int, str]] = []
        cur = _ROOT
        goto = self._goto
        nodes = self._nodes
        for i, ch in enumerate(text_lower):
            cur = goto[cur].get(ch, _ROOT)
            output = nodes[cur].output
            if output:
                end = i + 1
                for pattern in output:
                    out.append((end - len(pattern), end, pattern))
        return out
