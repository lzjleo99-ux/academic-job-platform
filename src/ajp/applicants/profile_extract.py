"""T25: CV text -> structured applicant profile. Reuses ajp.thesis.parser.parse_document for the actual
PDF/DOCX -> plain-text extraction (the same pypdf/pdfplumber/python-docx pipeline T16 already built and
tested - never a second document parser); everything in THIS module is new, CV-specific structured extraction
on top of that plain text, since a CV's own shape (skills/languages/degree/location preferences) has nothing
in common with a thesis's.

D6 unchanged: every field is either read directly from a real, matched piece of text, or left None/empty -
nothing here invents a discipline, a degree, or a salary figure the CV does not actually state. Every field's
own raw excerpt is kept alongside it in the profile dict (never just the parsed value) so a reviewing
applicant can always see WHY a field was filled in, and correct it via PATCH if the heuristic guessed wrong.

Disciplines are matched by KEYWORD, not against a loaded EuroSciVoc taxonomy tree - this project has never
had one (see /v1/meta.degraded_features: 'EuroSciVoc discipline hierarchy is flat, not a tree'), and every
connector's own disciplines_euroscivoc values are themselves inconsistent free text harvested from whatever
vocabulary each source happens to use (numeric OECD-Frascati-style codes from one source, 'Physics »
Quantum physics'-style EURAXESS labels from another) - an exact-string match against that would essentially
never fire. A small, curated OECD Frascati Manual top-level field list (the same 6 broad fields EuroSciVoc's
own top level is itself built from) is used on BOTH sides instead: extracted here by keyword from the CV, and
the SAME keyword sets are what ajp.applicants.matching checks a listing's own title/raw_text/discipline list
against - a documented, honest heuristic, not a claim of taxonomy-grade precision.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pycountry

from ajp.thesis.parser import ParseError, parse_document

# ---------------------------------------------------------------- disciplines (OECD Frascati top level) -----
# Keyword lists are deliberately small and precision-first (a real CV keyword, not a generic word that would
# false-trigger on unrelated prose) - matched case-insensitively as whole words/phrases against the CV's full
# text. Reused verbatim by ajp.applicants.matching for BOTH sides of discipline overlap now (T36a, packet 021:
# previously only the applicant's own CV was ever run through this list - a listing's own title/body text
# never was, so the discipline criterion's LISTING side had no real topical signal at all beyond whatever a
# connector happened to already populate disciplines_euroscivoc with, which PM's own live audit found NULL
# for all 3,959 real open rows).
#
# T36a (packet 021, PM's own real evidence - 'robot learning'/'manipulation' resolved to nothing at all,
# burying a genuinely perfect-fit KTH listing at rank 2037 for an ML/CS candidate): the CS/AI keyword set is
# substantially expanded here, on both the applicant and listing side (the SAME dict, never a second,
# drifting copy) - still precision-first (a real, specific term, never a bare generic word that would
# false-trigger on unrelated prose the way the old single-keyword 'education' once did, see T33).
DISCIPLINE_KEYWORDS: Dict[str, List[str]] = {
    "Natural sciences": [
        "physics", "chemistry", "biology", "mathematics", "astronomy", "geology",
        "earth science", "environmental science", "fizika", "hemija", "biologija", "matematika",
        "informatika", "računarske nauke",
        # T36a: CS/AI/data - the keyword set PM's own packet explicitly names, expanded substantially.
        "computer science", "artificial intelligence", "machine learning", "deep learning",
        "natural language processing", "nlp", "computer vision", "robotics", "robot learning",
        "autonomous system", "autonomous robot", "data science", "data engineering", "algorithm",
        "high performance computing", "hpc", "optimisation", "optimization", "signal processing",
        "photonics computing", "semiconductor computing", "reinforcement learning", "neural network",
        "software engineering",
    ],
    "Engineering and technology": [
        "engineering", "mechanical engineering", "electrical engineering", "civil engineering",
        "materials science", "robotics", "nanotechnology", "inženjerstvo", "mašinstvo", "elektrotehnika",
        "građevinarstvo",
        # T36a: mechanical/mechatronics-specific terms PM's own packet explicitly names, so a mechanical CV's
        # own real skills (SolidWorks/CAD/FEM/ANSYS) can overlap a genuinely relevant listing's own title/
        # body. Bare 'CAD' is deliberately NOT a keyword here - it is also the ISO code for the Canadian
        # dollar, a real false-positive risk on an international job board that states salaries in it.
        "mechatronics", "finite element", "solidworks", "computer-aided design", "ansys",
        "fluid dynamics", "fluid mechanics", "structural mechanics", "materials mechanics",
        "manufacturing engineering", "aerospace engineering",
    ],
    "Medical and health sciences": [
        "medicine", "clinical", "pharmacology", "neuroscience", "public health", "nursing", "dentistry",
        "medicina", "farmakologija", "javno zdravlje", "stomatologija",
    ],
    "Agricultural sciences": [
        "agriculture", "agronomy", "veterinary", "forestry", "food science", "poljoprivreda", "agronomija",
        "veterina", "šumarstvo",
    ],
    # T33 (packet 018, PM's own real ML/CS golden CV): the bare word 'education' used to be a keyword here
    # (meant for the ACADEMIC FIELD of education studies/pedagogy) but every real CV has an 'Education'
    # section header listing its own author's degrees - a virtually universal false-positive trigger, found
    # live reproducing PM's own real defect (an ML/CS CV misclassified as also 'Social sciences'). Replaced
    # with more specific multi-word phrases that only fire on a genuine statement about the FIELD, never a
    # bare section header.
    "Social sciences": [
        "economics", "psychology", "sociology", "political science", "law", "education studies",
        "educational policy", "educational psychology", "teacher education", "pedagogy", "business "
        "administration", "ekonomija", "psihologija", "sociologija", "pravo", "pedagogija",
    ],
    "Humanities and the arts": [
        "history", "philosophy", "linguistics", "literature", "archaeology", "theology", "musicology",
        "istorija", "filozofija", "lingvistika", "književnost", "arheologija",
    ],
}


# T36f (packet 021, perf): found live - real matches-endpoint timing against the real ~4,065-open-row DB was
# ~35s per applicant, far past PM's own ~1s bound, once T36a's discipline inference ran per-listing across
# every one of the (now substantially larger) keyword lists. re.escape()-ing and re.search()-ing EVERY single
# keyword separately, per listing, per bucket, was the dominant cost - one combined, precompiled alternation
# regex PER BUCKET (built once at import time, never per call) turns "check N keywords" into one single regex
# match attempt, the same real fix class as this project's own _NON_ACADEMIC_ROLE/_ACADEMIC_POSITION single-
# combined-pattern convention in intl_text.py.
_DISCIPLINE_PATTERNS: Dict[str, "re.Pattern"] = {
    field: re.compile(r"\b(?:" + "|".join(re.escape(kw) for kw in kws) + r")\b", re.IGNORECASE)
    for field, kws in DISCIPLINE_KEYWORDS.items()
}


def extract_disciplines(text: str) -> List[str]:
    return [field for field, pattern in _DISCIPLINE_PATTERNS.items() if pattern.search(text)]


# T37 (packet 022, R2 - PM's own explicit stoplist, found live against PM's own real mechanical-engineering
# golden CV): bare broad-bucket labels and hyper-generic tokens must NEVER count as a skill/field-TERM match
# for ajp.applicants.matching's own skill_relevance factor - that factor's whole job is to discriminate
# WITHIN a discipline bucket (an electrical/chemical/civil engineering listing is NOT a good fit for a
# mechanical engineer just because all four share the same broad 'Engineering and technology' bucket; that
# coarse 6-bucket overlap is already _score_discipline's own separate job, unaffected by this stoplist).
# PM's own real golden mechanical CV states, in its own Preferences sentence, 'Seeking full-time PhD research
# positions in ENGINEERING' - a real, bare, generic mention with nothing mechanical-specific about it at
# all, that was still extracted as a 'matched discipline term' before this fix, and then let it match ANY
# listing whose title bare-mentions 'engineering' (electrical, chemical, civil, structural - all scored
# equally) as if it were a specific mechanical-engineering skill - PM's own measured live evidence: bare
# 'engineering' matched at fraction 0.25, worth MORE than or equal to the genuinely specific 'mechanical
# engineering'/'CAD' phrases at 0.12. Checked by EXACT phrase equality (case-insensitive) - 'computer
# science'/'computer engineering' are a DIFFERENT string from bare 'computer' and are deliberately NOT
# excluded (PM's own explicit instruction).
GENERIC_SKILL_TERM_STOPLIST = frozenset({
    "engineering", "science", "sciences", "natural sciences", "engineering and technology", "humanities",
    "medical", "medicine", "research", "researcher", "technology", "computer",
    "academic", "university", "study", "studies",
})


# T36c (packet 021), found live building the real mechanical-CV ranking gate: using the WHOLE discipline
# bucket's own keyword list as skill_relevance's "discipline phrases" signal let an UNRELATED sub-field from
# the SAME broad bucket count as if it were relevant - a mechanical engineer's own CV (mentioning
# 'mechanical engineering', never 'electrical'/'chemical' anything) still scored a real listing titled
# 'Electrical Engineering or Automation Technology' nearly as high as a genuinely mechanical one, purely
# because both share the same coarse 'Engineering and technology' bucket. The fix: only the SPECIFIC
# keyword(s) that actually matched somewhere in the applicant's own CV text are used as discipline phrases,
# never the bucket's own full sibling list.
def extract_matched_discipline_terms(text: str) -> List[str]:
    """The specific keyword(s) that actually matched in this text (not just the bucket name they belong to) -
    e.g. a CV mentioning 'mechanical engineering' returns exactly that, never 'electrical engineering' too,
    even though both live in the same 'Engineering and technology' bucket. T37 (packet 022, R2): a bare
    generic/bucket-label token (see GENERIC_SKILL_TERM_STOPLIST) is never specific enough to count here,
    even when it is the literal text the discipline-bucket regex matched - still allowed, unaffected, as a
    bucket-level signal to extract_disciplines/_score_discipline, which never calls this function."""
    terms: List[str] = []
    for field, kws in DISCIPLINE_KEYWORDS.items():
        pattern = _DISCIPLINE_PATTERNS[field]
        for m in pattern.finditer(text):
            matched = m.group(0).lower()
            if matched in GENERIC_SKILL_TERM_STOPLIST:
                continue
            for kw in kws:
                if kw.lower() == matched and kw not in terms:
                    terms.append(kw)
                    break
    return terms


# T36a (packet 021): a LISTING's own discipline, inferred the same way a CV's own is - by keyword, against
# the SAME 6-bucket OECD-Frascati scheme - never claimed as an authoritative EuroSciVoc code (this project
# has never had a real taxonomy tree loaded, see /v1/meta.degraded_features; a connector's own real
# disciplines_euroscivoc, when one is actually stated, is still honored separately by the caller, this is
# purely the fallback every listing currently needs since PM's own live audit found it NULL on all 3,959
# real open rows). title_raw is checked on its own first and, if it alone already names a real bucket,
# THAT is trusted outright - a real ad's own body text often pads in unrelated boilerplate (funding-source
# descriptions, generic institutional blurbs) that could otherwise pull in an unrelated bucket alongside the
# genuine one; only when the title alone names nothing does the body get a chance to supply the signal
# instead (the same title-priority precedent this project's own position_kind_intl already established for
# the 'phd' rule, T13).
# T36f (packet 021, perf): found live profiling this function directly - a real listing's own raw_text is
# often several thousand characters (median ~3.5k, some up to ~19.5k in the live DB), and this ran a 6-bucket
# regex scan over the WHOLE thing, for every one of thousands of listings, on every single matches request.
# Capped at the first 3,000 characters - a real ad's own core topic/requirements are essentially always
# stated in its own opening paragraphs, never buried past that point; this is a documented precision/
# performance tradeoff (a topic mentioned ONLY beyond 3,000 characters in - almost always boilerplate,
# legal notices, or application-instructions text by then - could be missed), never a correctness bug.
_DISCIPLINE_SCAN_CHAR_LIMIT = 3000


def infer_listing_disciplines_with_zone(title_raw: str, requirements_raw: str = "",
                                        raw_text: str = "") -> "tuple[List[str], Optional[str]]":
    """T39 (packet 022): the exact same inference as infer_listing_disciplines() below, also reporting WHICH
    zone ('title' or 'body' - requirements_raw and raw_text are only ever consulted together, as a single
    fallback body, so they are not distinguished here) actually supplied the result - None when neither zone
    names any bucket at all. Persisted at ingest time (ajp.ingest.upsert_listing) as the real
    `inferred_disciplines` column; infer_listing_disciplines() itself is now a thin wrapper over this, so the
    two can never drift apart."""
    from_title = extract_disciplines(title_raw or "")
    if from_title:
        return from_title, "title"
    body = f"{requirements_raw or ''} {raw_text or ''}"[:_DISCIPLINE_SCAN_CHAR_LIMIT]
    from_body = extract_disciplines(body)
    return from_body, ("body" if from_body else None)


def infer_listing_disciplines(title_raw: str, requirements_raw: str = "", raw_text: str = "") -> List[str]:
    buckets, _zone = infer_listing_disciplines_with_zone(title_raw, requirements_raw, raw_text)
    return buckets


# T39 (packet 022): the listing-side counterpart of extract_matched_discipline_terms() above - every
# SPECIFIC, non-generic DISCIPLINE_KEYWORDS-vocabulary term (T37's own stoplist already applied) actually
# present in a LISTING's own title/requirements_raw/raw_text, zone-tagged by the FIRST (highest-priority:
# title > requirements > body) zone it appears in - the SAME zone-priority/dedup convention
# ajp.applicants.matching._score_skill_relevance already uses when matching an applicant's own
# `discipline_terms` against a listing's text. Precomputed once at ingest (ajp.ingest.upsert_listing) as the
# real `inferred_skill_terms` column, so a request never has to re-run this exact scan per listing for the
# controlled-vocabulary half of any applicant's own phrase set (T39's own stated perf goal) - the applicant's
# own free-form `skills` field is NOT covered here (it is arbitrary CV text, not this project's own
# controlled discipline vocabulary) and is still matched live, see matching.py's own comments.
_SKILL_TERM_BODY_CHAR_LIMIT = _DISCIPLINE_SCAN_CHAR_LIMIT    # the SAME documented cap, one constant


def extract_listing_skill_terms(title_raw: str, requirements_raw: str = "",
                                raw_text: str = "") -> "List[Dict[str, str]]":
    body = (raw_text or "")[:_SKILL_TERM_BODY_CHAR_LIMIT]
    out: "List[Dict[str, str]]" = []
    seen: "set" = set()
    for zone, text in (("title", title_raw or ""), ("requirements", requirements_raw or ""), ("body", body)):
        for term in extract_matched_discipline_terms(text):
            if term in seen:
                continue
            seen.add(term)
            out.append({"term": term, "zone": zone})
    return out


# ---------------------------------------------------------------- degree / career stage ----------------------
# Highest-first: a CV stating both a PhD and an earlier MSc must report 'phd', not the first one matched.
# T36e (packet 021): 'doctoral researcher'/'doctoral student' (a real, common English phrasing for a PhD
# candidate this project's own regex had no noun for at all until now) added to the phd rule.
_DEGREE_RULES: List["tuple[str, re.Pattern]"] = [
    ("prof", re.compile(r"\b(full |associate |assistant )?professor\b|\bredovni profesor\b|\bvanredni profesor\b|"
                        r"\bdocent\b", re.IGNORECASE)),
    ("postdoc", re.compile(r"\bpost[- ]?doc(?:toral)?\b|\bpostdoktor\w*\b", re.IGNORECASE)),
    ("phd", re.compile(r"\bph\.?\s?d\.?\b|\bdoctor of philosophy\b|\bdoktor nauka\b|\bdoktorat\b|"
                       r"\bdoktorske studije\b|\bdoctoral (?:researcher|student|candidate)\b", re.IGNORECASE)),
    # T36e: 'MA'/'M.Eng' added (real, common master's-level abbreviations this project had no noun for).
    ("msc", re.compile(r"\bm\.?\s?sc\.?\b|\bm\.?\s?a\.?\b|\bm\.?\s?eng\.?\b|\bmaster'?s? (?:of|degree)\b|"
                       r"\bmagistar\b|\bmaster studije\b", re.IGNORECASE)),
    ("bsc", re.compile(r"\bb\.?\s?sc\.?\b|\bbachelor'?s? (?:of|degree)\b|\bosnovne studije\b", re.IGNORECASE)),
]
_LEVEL_RANK = {"bsc": 1, "msc": 2, "phd": 3, "postdoc": 4, "prof": 5}

# T36e (packet 021, PM's own real golden CV: 'PhD candidate in Machine Learning... (expected 2027). MSc in
# Computer Science.' plus, separately, 'Seeking full-time PhD or postdoctoral research positions...') - a
# CV states career stage in (at least) 3 genuinely different ways this module must now tell apart: a
# CURRENTLY-PURSUED level (marked 'candidate'/'in progress'/an 'expected <year>' graduation), a COMPLETED
# one (stated plainly, no such marker), and a merely ASPIRATIONAL one (something the applicant is SEEKING/
# applying for/interested in, not a credential they hold or are even enrolled in at all) - found live
# reproducing PM's own real CV: 'seeking...postdoctoral research positions' was wrongly extracted as an
# ATTAINED postdoc before this fix, since the bare word 'postdoctoral' appearing anywhere in the text was
# enough, with no regard for the 'seeking' context immediately in front of it.
_ASPIRATIONAL_BEFORE = re.compile(
    r"\b(?:seeking|looking for|interested in|applying for|apply for|open to)\b[^.\n]{0,40}$", re.IGNORECASE)
_IN_PROGRESS_NEARBY = re.compile(
    r"\b(?:candidate|in progress|currently pursuing|currently completing|expected \d{4}|ongoing)\b", re.IGNORECASE)


def _classify_degree_mention(text: str, start: int, end: int) -> str:
    # Scoped to the CURRENT SENTENCE only - an in-progress/aspirational marker must never leak across a
    # sentence boundary into an adjacent, separately-stated degree. Found live reproducing PM's own real CV:
    # '...(expected 2027). MSc in Computer Science.' - a fixed lookback window (not sentence-scoped) let the
    # PhD's own 'expected 2027' wrongly mark the NEXT sentence's separately-stated, already-COMPLETED MSc as
    # if it were ALSO still in progress.
    sentence_start = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    end_candidates = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    sentence_end = min(end_candidates) if end_candidates else len(text)
    before, after = text[sentence_start:start], text[end:sentence_end]
    if _ASPIRATIONAL_BEFORE.search(before):
        return "aspirational"
    if _IN_PROGRESS_NEARBY.search(before + after):
        return "in_progress"
    return "completed"


def extract_degree(text: str) -> Optional[Dict[str, Any]]:
    """Every real career-stage/degree mention in the CV, classified as completed/in-progress/aspirational
    (never guessed, D6 - only real, matched text with a real excerpt) and reduced to the HIGHEST of each of
    the first two categories (an aspirational-only mention, e.g. 'seeking postdoctoral positions', contributes
    to neither - it is not a credential the applicant holds or is even enrolled in).

    The returned dict keeps its original 'v'/'raw' shape at the top level for every existing caller (the
    single, most relevant overall level - the in-progress one, if it is at least as advanced as anything
    completed, since actively pursuing a higher degree is itself real signal a job-matching engine should
    weigh at least as strongly as a lower COMPLETED one) plus two new, purely additive fields: 'in_progress'
    (bool, whether the primary v/raw IS the in-progress one) and 'highest_completed' (the highest genuinely
    completed level, if any, separate from whatever is in progress - None when there isn't a distinct one)."""
    best_completed = best_in_progress = None
    for value, rx in _DEGREE_RULES:
        for m in rx.finditer(text):
            category = _classify_degree_mention(text, m.start(), m.end())
            if category == "aspirational":
                continue
            entry = {"v": value, "raw": m.group(0)}
            current = best_in_progress if category == "in_progress" else best_completed
            if current is None or _LEVEL_RANK[value] > _LEVEL_RANK[current["v"]]:
                if category == "in_progress":
                    best_in_progress = entry
                else:
                    best_completed = entry
    if best_in_progress is None and best_completed is None:
        return None
    if best_in_progress and (best_completed is None or _LEVEL_RANK[best_in_progress["v"]] >= _LEVEL_RANK[best_completed["v"]]):
        primary, in_progress = best_in_progress, True
    else:
        primary, in_progress = best_completed, False
    return {**primary, "in_progress": in_progress,
           "highest_completed": best_completed if not (primary is best_completed) else None}


# ---------------------------------------------------------------- skills ---------------------------------------
# Found live building this module's own PDF test fixture: pdfplumber's text extraction collapses every
# blank-line paragraph break into a single '\n' (DOCX's own paragraph structure preserves it, PDF's plain-
# text extraction does not) - a section boundary that only recognized "\n\s*\n" as the end of a section
# swallowed the ENTIRE rest of a PDF-sourced document as if it were all "skills".
#
# T33 (packet 018, PM's own real golden CV): a real CV's OWN next section header is very often a bare word on
# its own line with NO trailing colon at all ('Languages', 'Preferences') - PM's own real extraction pulled
# in that bare header, the WHOLE languages section under it, and the free-text preferences sentence after it
# as if they were all "skills" (16 polluted entries: 'Languages', 'Serbian (native)', 'Preferences', a whole
# sentence fragment, ...). A curated list of the section names a real CV actually uses (colon optional) now
# ends the section, on top of the previous generic 'any short Capitalized-Word(s): label' fallback.
_CV_SECTION_HEADER_WORDS = (
    "skills", "technical skills", "key skills", "core competencies", "competencies", "languages",
    "language skills", "education", "experience", "work experience", "professional experience",
    "employment history", "research interests", "interests", "preferences", "publications", "projects",
    "certifications", "awards", "references", "summary", "profile", "objective", "contact",
    "veštine", "vještine", "jezici", "obrazovanje", "iskustvo",
)
# T33 (packet 018, PM's own REAL golden PDF CV, found live re-running against it after the fixture arrived):
# a real CV's own next section header is JUST as often written inline WITH its own content on the same line
# ('Languages: Serbian native, English B2.') as it is a bare header alone on its own line - the boundary
# above originally only matched a header with NOTHING after it (anchored to end-of-line, '$'), so
# 'Languages: Serbian native, English B2.' never counted as a new section at all and got swallowed into
# "skills" whole, along with the next 'Preferences: ...' line and its own free-text sentence after it. Anchors
# only to the START of the line now (header word + optional colon), matching regardless of trailing content -
# the same inline-vs-block duality already fixed for the SKILLS header itself, generalised to every header.
_CV_SECTION_HEADER_LINE = re.compile(
    r"(?im)^\s*(?:" + "|".join(re.escape(h) for h in _CV_SECTION_HEADER_WORDS) + r")\s*:?(?:\s|$)")
_GENERIC_SECTION_HEADER_LINE = re.compile(r"^[A-Z][A-Za-z /]{1,30}:")
# T33 (packet 018, PM's own real golden CV, PDF path): a real CV states this inline on ONE line
# ('Skills: SolidWorks, CAD, FEM, ANSYS, Python, materials testing.') just as often as on its own header
# line with the list below it - the previous version only ever recognised the block-header shape, so a real
# inline CV extracted skills=[] entirely despite an explicit 'Skills:' line right there in the text.
_SKILLS_HEADER = re.compile(r"(?im)^\s*(?:skills|technical skills|key skills|veštine|vještine)\s*[:\-]?\s*(.*)$")
_SKILL_SPLIT = re.compile(r"[,;••/]+")
# T33: a genuine skill token is a short noun phrase, never a full prose sentence - a real CV's own free-text
# preference/objective sentence ('Seeking a full-time PhD/postdoc position in AI...') must never be swept in
# even if it ends up on the same captured line as real skills (defense-in-depth on top of the section-
# boundary fix above, for exactly the shape PM's own real CV had: a sentence sharing a line with a header).
_PROSE_STOPWORDS = {"a", "an", "the", "to", "in", "for", "with", "of", "and", "seeking", "desired", "preferred",
                    "open", "willing", "relocation", "sponsorship", "position", "looking", "eligible"}


def _looks_like_prose(segment: str) -> bool:
    words = segment.lower().split()
    return len(words) >= 4 and any(w in _PROSE_STOPWORDS for w in words)


def extract_skills(text: str) -> List[str]:
    """Only from an explicit 'Skills:'-labelled section (D6: never guessed from unrelated prose elsewhere in
    the CV) - handles both the inline shape ('Skills: Python, Docker.') and the block shape ('Skills:' on its
    own line, the list on following lines). Stops at the next real section header (colon optional) or a
    blank line, splits on the common list separators a real CV skills line uses, drops anything that looks
    like a prose sentence rather than a short skill token, deduplicated."""
    lines = text.split("\n")
    start, inline_tail = None, ""
    for i, line in enumerate(lines):
        m = _SKILLS_HEADER.match(line)
        if m:
            start, inline_tail = i + 1, m.group(1).strip()
            break
    if start is None:
        return []
    collected: List[str] = [inline_tail] if inline_tail else []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            break
        if _CV_SECTION_HEADER_LINE.match(stripped) or _GENERIC_SECTION_HEADER_LINE.match(stripped):
            break
        collected.append(stripped)
    seen, out = set(), []
    for raw in _SKILL_SPLIT.split("\n".join(collected)):
        s = raw.strip(" \t-•*").strip().rstrip(".,;:")
        if not s or not (1 <= len(s) <= 60):
            continue
        if _looks_like_prose(s):
            continue
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


# ---------------------------------------------------------------- languages ------------------------------------
_LANGUAGE_NAMES = ("english", "serbian", "german", "french", "spanish", "italian", "russian", "hungarian",
                   "portuguese", "dutch", "chinese", "mandarin", "japanese", "arabic", "engleski", "srpski",
                   "nemački", "francuski", "ruski")
_LEVEL_WORDS = {
    "native": "native", "mother tongue": "native", "izvorni govornik": "native",
    "fluent": "fluent", "tečno": "fluent", "advanced": "advanced", "napredno": "advanced",
    "intermediate": "intermediate", "srednje": "intermediate", "basic": "basic", "osnovno": "basic",
    "c2": "C2", "c1": "C1", "b2": "B2", "b1": "B1", "a2": "A2", "a1": "A1",
}
_LANG_LINE = re.compile(
    r"\b(" + "|".join(_LANGUAGE_NAMES) + r")\b(?:\s*[:\-–(]?\s*)?(" +
    "|".join(re.escape(k) for k in _LEVEL_WORDS) + r")?", re.IGNORECASE)


def extract_languages(text: str) -> List[Dict[str, Optional[str]]]:
    """Every mentioned language, with a level ONLY when a real level word/CEFR code appears near it in the
    same match - never inferred just from a language being named at all (a language listed with no stated
    level is still real signal, kept with level=None, not dropped)."""
    section = re.search(r"(?im)^\s*languages?\s*[:\-]?\s*\n?(.{0,400})", text, re.S)
    scope = section.group(1) if section else text
    seen: Dict[str, Optional[str]] = {}
    for m in _LANG_LINE.finditer(scope):
        lang, level = m.group(1).lower(), m.group(2)
        canon = "English" if lang in ("english", "engleski") else lang.capitalize()
        level_v = _LEVEL_WORDS.get(level.lower()) if level else None
        if canon not in seen or (level_v and not seen[canon]):
            seen[canon] = level_v
    return [{"lang": k, "level": v} for k, v in seen.items()]


# ---------------------------------------------------------------- desired countries/cities --------------------
# The location-noun is REQUIRED after 'desired'/'preferred'/'target' (found live building this module's own
# test CV: "Full-time preferred." false-triggered before ever reaching the real "Desired location:" line,
# since 'preferred' alone is far too generic a word on its own - a real CV can use it about almost anything).
# T33 (packet 018, PM's own real golden CV): a real CV very often states this as plain relocation prose
# ('...relocation to Germany, Sweden, Denmark, Netherlands.') rather than a labelled 'Desired location:' line
# at all - added as an equally explicit, equally precision-first phrasing (never a bare country name
# mentioned anywhere else in the CV, e.g. a past employer's own address).
#
# T33, found live re-running against PM's own REAL golden CV file: the capture window used to grab up to 200
# raw characters regardless of sentence boundaries ('.{0,200}', with re.S letting '.' cross newlines too) - a
# real CV's own very next SENTENCE after the country list ('...Netherlands. Minimum salary 38000 EUR per
# year. No work permit needed for Serbia; visa sponsorship desired in EU.') got pulled into the SAME capture,
# and its own unrelated trailing clause ('visa sponsorship desired in EU') survived the per-token clean-up
# (which only trims each token to its OWN first '.'/newline) as a bogus desired_cities entry. The capture
# itself now stops at the first '.' or newline - one sentence/line, never spilling into the next.
_DESIRED_LOCATION = re.compile(
    r"(?im)(?:(?:desired|preferred|target)\s+(?:location|countr\w*|cit(?:y|ies))|"
    r"looking for (?:a )?(?:position|opportunit\w*) in|"
    r"relocat\w*\s+to|open to relocat\w*\s+(?:to)?)\s*[:\-]?\s*([^.\n]{0,200})")


def extract_desired_locations(text: str) -> Dict[str, List[str]]:
    """Only from an explicit 'Desired/Preferred location(s):'-style statement - a country/city merely
    mentioned elsewhere in a CV (e.g. the applicant's current employer's own address) is never assumed to be
    a PREFERENCE. Country names are resolved via pycountry (a real lookup); anything unresolved is kept as a
    city-name candidate instead of discarded."""
    m = _DESIRED_LOCATION.search(text)
    if not m:
        return {"desired_countries": [], "desired_cities": []}
    tokens = [t.strip() for t in re.split(r"[,;/]|\band\b", m.group(1)) if t.strip()]
    countries, cities = [], []
    for t in tokens[:10]:
        t_clean = re.split(r"[.\n]", t)[0].strip()
        if not t_clean:
            continue
        try:
            countries.append(pycountry.countries.lookup(t_clean).alpha_2)
        except LookupError:
            if len(t_clean) <= 40:
                cities.append(t_clean)
    return {"desired_countries": countries, "desired_cities": cities}


# ---------------------------------------------------------------- remote / ft_pt / years / salary / visa ------
_REMOTE_OK = re.compile(r"\bopen to remote\b|\bremote[- ]friendly\b|\bwilling to work remotely\b|"
                        r"\bremote work (?:preferred|welcome)\b", re.IGNORECASE)
_FT_PT = re.compile(r"\b(full[- ]time|part[- ]time)\s*(?:only|preferred)?\b", re.IGNORECASE)
_YEARS_EXP = re.compile(r"\b(\d{1,2})\+?\s*years?\s*(?:of\s+)?(?:professional\s+|research\s+)?experience\b",
                        re.IGNORECASE)
_MIN_SALARY = re.compile(
    r"(?:expected|minimum|desired)\s+salary\s*[:\-]?\s*(?:€|eur|\$|usd)?\s*([\d,.]+)\s*(?:€|eur|\$|usd)?\s*"
    r"(?:/|per\s+)?\s*(year|month|annum)?", re.IGNORECASE)
_VISA = re.compile(
    r"(?:require\w*|need\w*)\s+(?:visa|work permit|sponsorship)|"
    r"(?:eligible|authorized|authorised)\s+to\s+work[^.\n]{0,60}(?:without\s+(?:a\s+)?visa|sponsorship)|"
    r"no\s+visa\s+(?:required|needed)|(?:eu|serbian)\s+citizen", re.IGNORECASE)


def extract_work_prefs(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"remote_ok": None, "ft_pt": None, "years_experience": None,
                           "min_salary_eur_year": None, "foreigner_visa_note": None}
    if _REMOTE_OK.search(text):
        out["remote_ok"] = True
    m = _FT_PT.search(text)
    if m:
        out["ft_pt"] = "full" if "full" in m.group(1).lower() else "part"
    m = _YEARS_EXP.search(text)
    if m:
        out["years_experience"] = int(m.group(1))
    m = _MIN_SALARY.search(text)
    if m:
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            amount = None
        if amount is not None:
            period = (m.group(2) or "year").lower()
            annual = amount * 12 if period == "month" else amount
            out["min_salary_eur_year"] = round(annual, 2)
            out["min_salary_raw"] = m.group(0).strip()
    m = _VISA.search(text)
    if m:
        out["foreigner_visa_note"] = m.group(0).strip()
    return out


# ---------------------------------------------------------------- combined entrypoint -------------------------
def extract_profile(text: str) -> Dict[str, Any]:
    """Everything at once - the one function the API layer calls after extracting the CV's own plain text.
    Every field is either real signal (with its own raw excerpt, where the field's own shape allows one) or
    an honest empty/None; nothing is ever fabricated to make the profile look more complete than the CV
    actually states (D6)."""
    profile: Dict[str, Any] = {
        "disciplines": extract_disciplines(text),
        # T36c (packet 021): the SPECIFIC discipline keyword(s) that actually matched - used by
        # ajp.applicants.matching's own skill_relevance factor as a supplementary signal alongside the
        # applicant's own stated skills, never the whole bucket's own sibling keyword list (see
        # extract_matched_discipline_terms's own docstring for the real, live-found bug this replaced).
        "discipline_terms": extract_matched_discipline_terms(text),
        "degree": extract_degree(text),
        "skills": extract_skills(text),
        "languages": extract_languages(text),
    }
    profile.update(extract_desired_locations(text))
    profile.update(extract_work_prefs(text))
    return profile


def parse_cv_and_extract_profile(path: Union[str, Path]) -> "tuple[str, Dict[str, Any]]":
    """The one function the API layer actually calls: parse_document (T16's shared parser, reused as-is) for
    plain text, then extract_profile on top of it. Returns (full_text, profile); raises ParseError exactly as
    parse_document does for an unsupported/corrupt file - never silently returns an empty profile for a file
    that could not actually be read."""
    doc = parse_document(path)
    return doc.full_text, extract_profile(doc.full_text)
