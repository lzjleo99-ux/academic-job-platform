"""What a classification decision is made OF: its raw excerpt, the rule that fired, and a confidence.

Why this module exists (T48, packet 028): ``listing`` carries ``{conf, raw}`` for EVERY extracted value -
``salary_*``, ``duration_months_*``, ``study_while_work_ok_*``, ``location_class_*``, ``posted_at_*`` -
except two: ``kind`` and ``position_kind``. Those two are the platform's own classification outputs, they
decide whether a row is shown at all (``kind='other'`` is hidden by default), and until now nothing on the
row recorded WHICH text span produced them or how much the classifier trusted it. That is the one D6 gap
left in the schema ("a value cannot exist without its raw excerpt"), and it is also precisely what blocks
a human review queue: you cannot review a decision you cannot see the evidence for.

WHAT ``conf`` IS AND IS NOT
---------------------------
``conf`` is a RULE-STRENGTH PRIOR, not a calibrated probability. A regex classifier has no labelled
training data to calibrate against, so 0.95 does NOT mean "95% chance this is right" - it means "this
pattern names one specific thing and no live counter-example for it has been found in this codebase".
Anyone reading a persisted number back must be able to tell which model produced it, hence
``CONFIDENCE_MODEL`` below: it is bumped whenever a tier changes, and is meant to be persisted next to
the number so an old row can never be silently read as if it came from the current model.

The three things that genuinely vary, and are therefore the only three things folded into the number:

  1. PATTERN SPECIFICITY (per-rule tier). The code's own comments already rank these: "unambiguous
     name-strings" (Marie Sklodowska-Curie) vs "too common/ambiguous to trust standalone" (a bare
     'posao'). Tiers are per-RULE-GROUP, and are set by the group's WEAKEST member - deliberately
     conservative, because a group is one regex and one match tells you only that *something* in it hit.
     The precision the tier cannot give you is carried by ``raw`` instead: the actual matched span.
  2. MATCH ZONE (title vs body). A pattern hit in the title is a stronger statement about what the ad IS
     than the same pattern hit somewhere in the body - the same title-priority precedent this project
     already established for real in ``position_kind_intl`` (a KU DK PhD ad whose body named its
     supervisor's own rank). Body-only matches are discounted.
  3. COUNTER-EVIDENCE (a guard fired). If a negative guard - narrative past tense, a standing-document
     marker, an ERC-funded job describing its own funding - suppressed a rule before the winner was
     reached, the surviving decision is weaker than one reached cleanly.

'other' is NOT confidence 0. 0.0 would assert the row is definitely not a job; what is true is that no
rule produced positive evidence at all. That is modelled as ``conf=None`` (NULL in the database), never
as a number - while ``raw``/``rule`` may still be populated for an EXCLUSION (a non-academic role was
positively identified), which is a real decision with real evidence, just not a positive classification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# Bumped whenever any tier below changes. Persist it alongside any stored conf so a value written by an
# older model can be recognised as such instead of being compared against today's numbers as if equal.
CONFIDENCE_MODEL = "ajp-rule-prior/1"

# Zone discounts. A title match is the strongest statement available; a body-only match can be boilerplate
# (an eligibility section, a funding acknowledgement, a supervisor's own rank).
ZONE_TITLE = 1.0
ZONE_BODY = 0.85
# Applied once when at least one guard suppressed a candidate rule before the winning one was reached.
GUARD_SUPPRESSED_FACTOR = 0.9

MAX_RAW_LEN = 160


def _round(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 3)


def clip_raw(raw: Optional[str]) -> Optional[str]:
    """Bound the persisted excerpt. Excerpts are evidence, not content: 160 chars is far more than any
    pattern in this codebase needs to be identifiable, and keeps the column bounded on long ad bodies."""
    if raw is None:
        return None
    raw = " ".join(raw.split())
    return raw[:MAX_RAW_LEN]


@dataclass(frozen=True)
class Classification:
    """One classification decision plus everything needed to audit it."""
    kind: str
    conf: Optional[float] = None        # None = no positive evidence ('other'), never 0.0
    raw: Optional[str] = None           # D6: the exact span the decision was made on
    rule: Optional[str] = None          # which rule fired - 'grant_call.strong', 'position.professor', ...
    zone: Optional[str] = None          # 'title' | 'body' | None
    guards: Tuple[str, ...] = field(default_factory=tuple)  # negative guards that fired on this text

    @property
    def is_positive(self) -> bool:
        return self.conf is not None

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "conf": self.conf, "raw": self.raw, "rule": self.rule,
                "zone": self.zone, "guards": list(self.guards), "model": CONFIDENCE_MODEL}

    def as_row(self, prefix: str) -> Dict[str, Any]:
        """The listing columns for this decision: ``<prefix>_conf`` / ``<prefix>_raw``.
        ``rule``/``zone``/``guards`` are NOT separate columns - they ride along inside ``raw``'s sibling
        jsonb only where a column exists; here they are folded into the raw string's own audit trail via
        the review queue, which stores the whole ``as_dict()``."""
        return {f"{prefix}_conf": self.conf, f"{prefix}_raw": self.raw}


def score(tier: float, *, zone: Optional[str] = None, guards_fired: int = 0) -> float:
    """Fold the three real signals into one number. See the module docstring for why these three only."""
    f = ZONE_TITLE if zone == "title" else (ZONE_BODY if zone == "body" else 1.0)
    if guards_fired:
        f *= GUARD_SUPPRESSED_FACTOR
    return _round(tier * f)


def with_kind_override(fields: Dict[str, Any], new_kind: str) -> Dict[str, Any]:
    """A connector that overrides the classifier's own answer (a source's configured ``default_kind``, a
    board category, an ``academic_ad`` fallback, a document-path URL hint) must ALSO drop the classifier's
    evidence: the excerpt describes the decision the classifier made, not the one being persisted, and
    leaving it next to a different kind would be fabricated provenance - the exact thing D6 forbids.
    Called with the kind the classifier already produced it is a no-op, so callers can apply it
    unconditionally after any branch that might have changed the kind."""
    if fields.get("kind") == new_kind:
        return fields
    return {**fields, "kind": new_kind, "kind_conf": None, "kind_raw": None}


def classify_other(*, rule: Optional[str] = None, raw: Optional[str] = None,
                   guards: Tuple[str, ...] = ()) -> Classification:
    """The 'other' decision. ``conf`` is None by definition - 'other' is the ABSENCE of positive evidence,
    not a confident negative. An exclusion (a non-academic role positively identified) passes its rule and
    raw span so the decision is still auditable, and still reviewable."""
    return Classification(kind="other", conf=None, raw=clip_raw(raw), rule=rule, zone=None, guards=guards)
