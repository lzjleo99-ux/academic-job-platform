# Applicant/listing matching formula (T25, packet 015/016; T33, packet 018; T36, packet 021; T37/T38/T39,
# packet 022; T40, packet 023)

This is the transparent, deterministic formula behind `POST /v1/applicants/{id}/matches`. Every score it
produces is reproducible from the applicant's own profile and the listing's own stated fields - nothing here
is a black-box ranking or a machine-learning model. The authoritative implementation is
`src/ajp/applicants/matching.py`; this document explains it in prose and must be kept in sync with that
file (its own docstring links back here). The exact same weights are also rendered live on the methodology
page, fetched from `GET /v1/applicants/matching/weights` - never hand-restated in either place, so this
document, that page, and the real code can never silently drift apart from one another.

## The committed weights (sum to 100; rebalanced in T38, packet 022)

| Criterion         | Weight | What is compared                                                          |
|-------------------|-------:|----------------------------------------------------------------------------|
| `discipline`      | 15 | The applicant's own extracted discipline(s) against the listing's own - inferred (T36a) from its title/`requirements_raw`/`raw_text` using the SAME 6-bucket keyword scheme the CV side uses (title text checked first and trusted outright if it alone names a bucket), or a real `disciplines_euroscivoc` value on the rare row that states one. A coarse, flat 6-bucket match - never an exact taxonomy match (this project has no real EuroSciVoc tree loaded). Weight shrunk in T38 (was 20): a broad bucket has weak WITHIN-bucket discrimination (an electrical and a mechanical engineer land in the same bucket). |
| `skill_relevance` | 20 | **New in T36c, recalibrated in T37.** A best-evidence saturating curve over the overlap between the applicant's own stated skills/specific discipline terms and the listing's own title/`requirements_raw`/`raw_text` - see its own section below. This is the factor that actually tells a genuinely relevant listing apart from an unrelated one in the SAME broad discipline bucket. Weight grown in T38 (was 15): PM's own stated rationale - demonstrated skill is the strongest real relevance signal, now that T37 made it discriminating rather than "numerically inert". |
| `location`        | 20 | The listing's `country_iso2` against the applicant's `desired_countries`; a fully-remote listing (`location_class='remote'`) matches an applicant who is `remote_ok`. |
| `salary`          | 20 | The applicant's stated minimum (EUR/year, annualized the same way a listing's own salary already is) against the listing's own `salary_eur_year_max` (falling back to `salary_eur_year_min` if no max was stated). |
| `ft_pt`           | 10 | The applicant's stated full/part-time preference against the listing's own `ft_pt`. |
| `language`        | 10 | Any language the listing marks `required: true` against the applicant's own stated languages (a short ISO code and the language's full name are treated as the same language). |
| `degree_level`    | 5  | The applicant's own highest stated degree/career stage against the listing's `degree_required`, ranked bsc<msc<phd<postdoc<prof - meets or exceeds it wins. Weight shrunk in T36 (was 15): PM's own live audit found `degree_required` stated on only 36 of 3,959 real open listings, so this criterion is almost always "unknown by accident" and must never dominate the score the way its old weight let it. |

## Discipline inference (T36a, packet 021) - giving the listing side a real topical signal at all

Before T36, a listing's own `disciplines_euroscivoc` was checked directly but was **NULL on every one of
3,959 real open listings** PM's own live audit found - the discipline criterion's listing side effectively
never had any real signal at all. `infer_listing_disciplines()` (in `profile_extract.py`, reused verbatim by
the listing side and never duplicated) now runs the listing's own title/`requirements_raw`/`raw_text` through
the SAME keyword scheme the CV side already used, with the CS/AI/data keyword set substantially expanded
(machine learning, deep learning, NLP, computer vision, robotics/robot learning, data science, HPC,
optimisation, ...) and a mechanical/mechatronics set added (FEM, SolidWorks, ANSYS, fluid/structural
mechanics, ...) - never claimed as an authoritative EuroSciVoc code, purely a same-vocabulary bridge.

## Skill/keyword relevance (T36c, recalibrated T37, packet 021/022) - the actual fix for the "tie farm"

PM's own real evidence (T36): 22 real listings tied at the exact same score for one real ML/CS CV, and a
genuinely perfect-fit KTH "Postdoc in Robot Learning for Manipulation" sat at rank 2037 while an unrelated
plant-pathology postdoc sat at rank 1 - because discipline's own flat 6-bucket match (CS and plant biology
both land under the same broad "Natural sciences" bucket) was the *only* real topical signal that existed.
T36c's own first version of `skill_relevance` fixed the architecture but PM's own **further** live
measurement (T37, packet 022) found its actual *numbers* still too weak to separate relevant from irrelevant
listings - a single exact title-level phrase hit scored only ~0.5, and a real mechanical-engineering CV's
own bare, generic "engineering" mention (from an unrelated "seeking positions in engineering" sentence)
scored a chemical/electrical-engineering listing as highly as the CV's own genuinely specific "mechanical
engineering"/"CAD" terms. T37 is the fix for both:

- **A generic-term stoplist** (`GENERIC_SKILL_TERM_STOPLIST` in `profile_extract.py`): bare broad-bucket
  labels and hyper-generic tokens (`engineering`, `science`, `research`, `technology`, `computer`, `study`,
  ...) never count as a skill/term match here - that coarse bucket-level signal is already `discipline`'s own
  job; `skill_relevance` must discriminate *within* a bucket. Checked by exact phrase equality, so a specific
  multi-word phrase sharing a stoplisted *word* (`computer science`) is unaffected - only the bare token
  itself is excluded. Applied both where `discipline_terms` is extracted from a CV (so a persisted profile
  never carries the generic token at all) and again, defensively, where the search pattern is actually built
  (so an older, pre-T37 persisted profile is still correctly filtered).
- **A best-evidence saturating curve**: `fraction = min(1.0, raw / 2.5)`, where `raw` sums, over every
  matched phrase, `phrase_weight` (multi-word phrase = 2, single generic token = 1) × `zone_weight` (title =
  1.0, requirements = 0.6, body = 0.3). One exact multi-word phrase hit in the listing's own **title** earns
  `2 × 1.0 = 2.0` raw points, landing it **exactly at the 0.80 floor** (`2.0 / 2.5`); two such hits already
  clear 2.5 and saturate at the full 1.0.
- **A body-stuffing cap**: body-zone contribution alone is capped at `0.4 × 2.5 = 1.0` raw points before the
  zone weight is applied - `1.0 × 0.3 = 0.3` raw, `0.3 / 2.5 = 0.12` fraction. This is an architectural
  invariant, not a tunable number that happened to come out this way: **whenever body evidence is the only
  evidence, the fraction is always exactly `_BODY_ONLY_CAP_FRACTION × _BODY_HIT_WEIGHT` (0.12) regardless of
  the saturation point**, so a keyword-stuffed body can never approach the 0.80 a real title/requirements hit
  earns on its own. A real, live example this project has run into twice (T36a's own motivating case, and
  again here): a KTH "Postdoc in Robot Learning for Manipulation" listing, for an ML/CS applicant whose own
  CV states "machine learning" but never "robot"/"robotics"/"manipulation" verbatim, only matches in the
  listing's own BODY text - its `skill_relevance` fraction is a real, honest `0.12`, not the >0.80 a literal
  title-phrase overlap would require; this is **working as designed**, not a bug, and is tracked openly in a
  `need_input` event rather than silently forced upward (see "Known, reported gaps" below).
- The factor's own `reason` always names the actual matched phrase **and its zone**
  (`"matches: machine learning (body), computer science (body)"`) - never a bare number with nothing to
  explain it.
- `requirements_raw` is now actually wired into the live scoring query - found live building T37: the
  listing-fetch SQL never selected that column at all before this, so the "requirements" zone was silently
  dead code against every real listing in production (265 of the live DB's 4,065 open rows have real,
  non-empty `requirements_raw` that simply never reached the scorer until now).
- A deterministic **tie-break** (`match_sort_key`, T37/R4) resolves two listings landing on the exact same
  total score: skill_relevance's own points, then the number of positively-evidenced factors, then the
  listing's own `posted_at` (more recent wins), then the listing id itself - identical requests always return
  identical order. `GET /v1/applicants/{id}/matches`'s own SQL `ORDER BY` mirrors this precedence exactly.

## Neutral-unknown scoring - missing data gets a neutral score, never a 0 and never dropped (T33; extended by T36b)

**T33 superseded an earlier design** (a criterion missing on either side was excluded from both the numerator
and the denominator, then rescaled to whatever weight *was* evaluable). PM's own real acceptance testing
found that design let a listing stating almost nothing score a **perfect, indistinguishable 100** - of 3,948
real open listings, 2,381 scored exactly 100 for one real test CV, because a sparse listing's tiny evaluable
sliver rescaled to 100% of itself just as easily as a listing that genuinely matched on every criterion.

The fix: **every** criterion always contributes to a **fixed** denominator (the full committed weight, 100) -

    score = round( (sum of every criterion's own fraction * weight) / 100 * 100, 2 )

- `fraction` is `1.0` for a real match (`state: "positive"`), `0.0` for a real mismatch (`state: "negative"`),
  and **`0.5`** for a criterion neither side stated enough to evaluate (`state: "unknown"`) - a missing fact
  is real information too (it is simply *unknown*, not *bad*), so it is never scored as a 0, and it is never
  excluded from the total the way it used to be.
- **T36b's own correctness floor**: a criterion may be `"negative"` **only with positive evidence of an
  actual mismatch** - never merely because neither side stated enough to compare. `discipline` and
  `skill_relevance` are `"unknown"`, not `"negative"`, for a listing whose own topic genuinely cannot be
  determined at all (a bland title with no real content) - only a listing that DOES state a real, inferable
  topic that fails to overlap with the applicant's own is a genuine mismatch. (`skill_relevance` itself is
  the first *continuous*-valued factor here - `"negative"` for it means a real zero overlap, not merely
  "less than a perfect 1.0".)
- A listing that states almost nothing now converges toward the **neutral midpoint, ~50** - never a
  structural 100, and never a punitive 0 either.
- A listing that genuinely matches on everything it does state can still reach exactly 100 (a genuinely
  dense, multi-zone phrase overlap now saturates `skill_relevance` at a clean 1.0 too, see the worked example
  below) - a listing that actively *mismatches* on what it states falls below 50.
- `coverage` (a field alongside `score`) is the fraction of the total weight backed by **positive** evidence
  specifically - `sum(weight of every 'positive' criterion) / 100`. This is what lets a sparse ~50 score be
  told apart from a genuinely well-evidenced one at the same number; the UI and this document both treat
  `coverage`, not just the bare score, as part of what a match "means".
- `counts` (`{"positive": n, "negative": n, "unknown": n}`) and each criterion's own `factors[name].state` say
  exactly which of the 7 criteria landed in which bucket - never silently invisible to whoever reads the score.

## Worked examples (re-verified directly against the real code under the current T37+T38 weights/curve)

- A listing stating nothing at all comparable to the applicant's profile scores the pure neutral midpoint,
  **50** - every one of the 7 criteria is `unknown`, `coverage` is `0.0`. This is deliberately different from
  a listing that actively states things the applicant does not match (which scores *below* 50) - "we don't
  know" and "this doesn't fit" are not the same message, and the formula never conflates them. See
  `tests/test_applicant_matching.py`'s own cases for both.
- A fully-remote listing matches a `remote_ok` applicant on `location` even when the applicant stated no
  desired countries at all - remote work has no country to compare against.
- A real, relevant listing (a "Postdoc in Robot Learning for Manipulation", for an ML/CS candidate) scores
  **82.4** and clearly outranks an unrelated "Postdoc in Bioresource Recovery" (**15.0**) instead of both
  landing on an indistinguishable 100 (T25's own original design) or even the same ~87.5 (T33's own
  neutral-unknown fix alone, before T36a/c gave the listing side a real topical/skill signal at all) - see
  `test_a_real_relevant_ai_listing_outranks_an_unrelated_biorecovery_postdoc...`.
- A genuinely strong, multi-zone match (2 exact title-zone phrase hits + 1 requirements-zone hit, clearing
  the new 2.5 saturation point) now reaches the full `skill_relevance` fraction of **1.0**, and with every
  other one of the 7 criteria also a clean positive, an overall score of exactly **100** - this is now rare
  (it requires real, dense, multi-zone overlap, not merely "matches on everything"), but no longer
  structurally impossible the way the pre-T37 curve made it.

## Curated equivalence clusters (T40, packet 023) - resolving T37's own reported gap

T37 found (see "Resolved gap" below for the full before story) that `skill_relevance`'s exact-string
matching could not credit a listing that states a concept using *different words* than the applicant's own
CV - a CV stating "machine learning" never matched a listing titled "Robot Learning for Manipulation", even
though both are genuinely the same research area. PM's own ruling (packet 023, OPTION_B): a small, FROZEN,
PM-authored table of equivalence clusters, applied **only** inside `skill_relevance`'s own phrase matching -
never the discipline factor, never listing-side inference, never the persisted `inferred_*` columns (T39),
never any connector.

**The frozen table** (`src/ajp/applicants/skill_clusters.py` - edit only by replacing this whole module with
a new PM-issued table, never by hand-adding/removing a member):

| Cluster | Members |
|---|---|
| C1_ML_AI_CORE | machine learning, deep learning, reinforcement learning, robot learning, statistical machine learning, artificial intelligence |
| C2_NLP | natural language processing, NLP, computational linguistics, language technology, text mining, information extraction |
| C3_DATA_ANALYTICS | data science, data scientist, data analysis, data analytics, data mining, big data, statistical analysis, statistical modelling, statistical modeling |
| C4_CAD_DESIGN_TOOLS | CAD, computer-aided design, computer aided design, AutoCAD, SolidWorks, CATIA, Siemens NX, Creo, Inventor |
| C5_CAE_FEM_SIMULATION | FEM, FEA, finite element method, finite element analysis, finite element modelling, finite element modeling, computational mechanics, ANSYS, Abaqus, COMSOL |
| C6_FLUIDS | CFD, computational fluid dynamics, fluid mechanics, fluid dynamics, aerodynamics, turbomachinery, hydrodynamics |
| C7_MATERIALS_LAB | materials testing, material testing, materials characterization, material characterization, materials characterisation, material characterisation, experimental mechanics |
| C8_MECHANICAL_UMBRELLA | mechanical engineering, machine design, mechanical design, mechanical systems, precision mechanics, precision mechanic |

**Mechanism:** for every one of the applicant's own literal terms (from `skills` or `discipline_terms`) that
happens to be a cluster member, every OTHER member of that cluster becomes an additional candidate surface
phrase to search for in the listing's own text - symmetric (either member may be the applicant's own stated
term). Credit is an ordinary phrase hit: the SAME zone weights (title 1.0/requirements 0.6/body 0.3), the
SAME saturation point (2.5) and body cap (0.4), the SAME stoplist (applied *after* cluster expansion, so a
stoplisted term can never contribute its own cluster siblings either) - nothing about T37's own calibration
changed. The one new rule: **phrase weight is determined by the matched SURFACE phrase's own word count,
never by the applicant's original term** - a CV stating the single-token "CAD" that matches a listing's own
multi-word "Computer-Aided Design" earns the full multi-word weight (2), landing it at the 0.80 title-hit
floor; the reverse direction (a CV stating the full phrase, a listing stating the abbreviation) earns only
the single-token weight (1) - a real, measured, accepted asymmetry, not a bug (see the dedicated test
`test_t40_cluster_symmetry_links_fire_in_both_directions` for the full reasoning).

**Reason format:** a cluster-sourced match is tagged with the ASCII `~` and the applicant's own matched root
term, e.g. `"robot learning (title; ~ machine learning)"` - a literal match (the surface phrase IS the
applicant's own stated term) keeps the plain, no-tilde format unchanged.

**Deliberately excluded** (PM's own explicit, reasoned list - a future edit considering any of these must
re-justify it against the SAME risk, not just plausibility):
- bare **"AI"** - real false positives (French "j'ai" and others); "artificial intelligence" (the real
  multi-word phrase) already covers the genuine signal.
- bare **"CV"** - a curriculum-vitae false positive, not computer vision.
- bare **"robot"/"robotics"/"manipulation"** - too broad on their own; "robot learning" (already a member)
  already covers the one real case (KTH) that motivated C1.
- bare **"statistics"** - stays a standalone literal skill only.
- **electrical/chemical/civil/structural engineering, automation technology, mechatronics** - NOT mutually
  interchangeable with "mechanical engineering" - the exact T36c cross-pollution lesson, re-confirmed here
  for the cluster mechanism specifically (a mechanical engineer's CV must never score an electrical/chemical
  engineering listing highly just because they share a broad discipline bucket).
- **aerospace engineering, industrial engineering** - conservative; no current golden-CV evidence either way.
- any medical/biology/chemistry generic, and anything already on `GENERIC_SKILL_TERM_STOPLIST`.

**Worked examples (real, measured against the live `~/.ajp` DB, 4,065 open rows):**
- KTH "Postdoc in Robot Learning for Manipulation" vs the real ML golden CV: `skill_relevance` fraction
  **0.92** (`"robot learning (title; ~ machine learning), machine learning (body), deep learning (body; ~
  machine learning), reinforcement learning (body; ~ machine learning)"`), rank **17** (was 542) - now
  comfortably outranking the unrelated "plant pathogen interactions" listing (rank 130).
- "Assistant Professor of Natural Language Processing" vs the same CV: fraction **0.92**, rank **3** (was
  114).
- Both SUSMAT "...Computer-Aided Design and Discovery..." postdoc rows vs the real mechanical golden CV:
  fraction **0.80** each (was 0.0) via `"computer-aided design (title; ~ CAD)"` - their OVERALL rank (88/89)
  still does not reach PM's own requested top-20, for a real, unrelated reason: both are in Morocco, not one
  of this CV's own 2 stated desired countries (AT/DE) - a 20-point `location` penalty neither T37 nor T40
  touches or is meant to touch. See the T40 `need_input` event for the full component decomposition.

## Match engine: regex (default) vs Aho-Corasick (T41, packet 024)

`skill_relevance`'s own phrase matching has TWO interchangeable, fully tested, byte-identical
implementations, selected by the `AJP_SKILL_MATCH_ENGINE` environment variable (default `"regex"`):

- **`"regex"`** (the default): one combined alternation `\b(p1|p2|...)\b` per applicant, T36f/T37/T40's own
  original implementation.
- **`"aho_corasick"`**: a small, pure-Python, vendored Aho-Corasick automaton (`ajp/applicants/
  ahocorasick.py`) built from the exact same applicant-side vocabulary. PM authorized this engine (T41,
  packet 024) specifically to improve on T40's own real perf cost (the cluster expansion grew the typical
  candidate-phrase count from ~11 to ~27, pushing `POST /matches` slightly over the 1.0s median target).

**Both are proven byte-identical** - a committed regression test compares every fraction, reason string
(including tilde cluster notation), state, counts and coverage across a representative fixture battery,
and a live, one-off comparison ran BOTH engines against all 4,065 real open rows for both golden CVs: **0
mismatches, identical ranking order, for either CV.**

**Real, measured finding (not assumed): Aho-Corasick does NOT outperform the regex engine at this
project's own real vocabulary scale.** Interleaved, repeated timing (profiled directly with `cProfile`,
not guessed) found the regex engine consistently faster for the larger (ML, ~27-phrase) vocabulary and
roughly tied for the smaller (MECH) one - pure-Python per-character interpreter overhead (even after two
rounds of real optimization: precomputing a complete transition table to remove the textbook fail-chain
`while` loop, and switching from a generator to a flat list to remove per-`yield` overhead) outweighs
Aho-Corasick's better theoretical complexity at this scale. A third attempted optimization (a flat array
indexed by character code, instead of a dict, for the transition table) was measured to be SLOWER still,
not faster - a genuinely counter-intuitive result, kept out of the shipped code specifically because it
was measured to be a regression, not assumed to be an improvement. `"regex"` therefore stays the default;
`"aho_corasick"` remains available, correct, and tested, for PM's own re-verification or as a starting
point for a future, genuinely faster (likely compiled) implementation of the same algorithm. See the T41
`need_input` event for the full real numbers.

## Resolved gap (was: "Known, reported gaps", T37, packet 022)

PM's own T37 packet proposed 3 specific numeric targets for one real golden ML CV against the live DB that
T37's own recalibration, run honestly against the real data, did **not** reach on its own - a real KTH
listing's `skill_relevance` fraction was an architecturally-invariant 0.12 (body-only evidence only, since
the CV's own "machine learning" never appears in that listing's TITLE, only its body), its overall rank did
not overtake an unrelated "plant pathogen interactions" listing, and a real "Assistant Professor of Natural
Language Processing" listing ranked 114th instead of inside the top 10 - all three because exact-string
matching could not recognise "robot learning"/"Natural Language Processing" as the SAME concept the CV
states differently ("machine learning"/"NLP"). **T40's curated clusters (above) are PM's own explicit
OPTION_B resolution of this exact gap** - all 3 numeric targets now pass live (see the worked examples
above); the one SUSMAT rank target that still does not pass is a real, different, unrelated cause (a
location mismatch, not a skill-relevance gap) and is reported in its own `need_input`, not silently retried
with a wider cluster or weight change.

## What this formula deliberately does NOT do

- It never uses a listing's own `foreigner_eligible` column as a scoring criterion - that field describes a
  *listing's* stated eligibility rule (e.g. a military/police default), not whether a *specific applicant*
  can work there; an applicant's own visa/citizenship situation is free text (`foreigner_visa_note`) shown
  alongside a match for a human to read, never algorithmically matched against a listing's own field.
- It is not a machine-learning ranking and carries no claim of predicting real interview or hiring outcomes -
  it is a documented, reproducible heuristic over the fields both sides actually stated, shown with its own
  full per-criterion breakdown every time.
- `infer_listing_disciplines()`/`skill_relevance`'s own keyword matching never fabricates a skill or
  requirement the listing does not actually state - every match is a real, literal phrase found in the
  listing's own title/requirements/body text, named in the factor's own `reason` for anyone to verify
  directly (a T40 cluster match is still a real, literal phrase found in the listing's own text - it is
  only the *credit* for that phrase, not its existence, that is extended via the frozen cluster table, and
  the `reason` always says exactly which applicant term it was credited via).
- It never credits an UNCURATED synonym or "related concept" - `skill_relevance`'s own equivalence is
  limited to T40's one frozen, PM-authored cluster table (`skill_clusters.py`), never an open-ended or
  inferred notion of "similar enough"; a concept pairing this project has not explicitly reviewed and
  listed (e.g. "mechanical engineering" crediting "electrical engineering") stays unmatched, deliberately,
  per the exact same T36c cross-pollution lesson the table's own exclusions re-state.
