"""Rule-based classification for international tier-1 aggregators (T13): jobs.ac.uk, Nature careers,
AcademicTransfer, CERN, CNRS. Multilingual (EN/NL/FR, +SV since T19's KTH) - the Serbian classifier in
rs_text.py is untouched and never imported here; RS-sourced connectors (board.py, nsz.py, sljaka.py) keep
using that one. Same kind taxonomy as rs_text.py (employment/grant_call/internship/enrollment/other) and the
same "never guess, stay 'other'" default. First cut for T13: precision over recall, exactly like rs_text.py's
own design note.
"""
from __future__ import annotations

import re

# Exclusion markers checked FIRST: a non-academic role on an academic-flavoured board (administrative/facilities/
# hospitality/sales/finance-ops staff, or a non-research internship) is not shown, whatever academic-sounding
# words appear alongside it (e.g. a university's own facilities-manager ad still says "University of X").
_NON_ACADEMIC_ROLE = re.compile(
    r"\b(administrative assistant|admin(?:istration)? officer|facilities (manager|officer|assistant)|"
    r"catering|hospitality|housekeeping|security officer|sales (executive|manager|representative)|"
    r"finance (officer|assistant|manager)|accounts (payable|receivable)|payroll (officer|assistant)|"
    r"hr (officer|assistant|business partner)|human resources (officer|assistant)|receptionist|"
    r"marketing (officer|assistant|executive)|it support|helpdesk|"
    # Swedish (KTH, live 2026-09-23): a real batch of KTH's own "lediga jobb" feed items, none of them
    # academic despite sharing the same board as PhD/postdoc ads - hr-generalist, handläggare (a generic
    # admin/case-officer title; also catches forskningshandläggare, a RESEARCH ADMINISTRATOR, not a
    # researcher, despite the 'forsknings-' prefix), vägledare (study/career counsellor), tekniker
    # (technician), ekonom (a finance/economist role), gruppchef/teamledare (line-management titles),
    # samordnare (coordinator), besöksledare (visitor-centre guide, found on KTH's own outreach museum ad).
    r"hr-generalist|\w*handl[äa]ggare|\w*v[äa]gledare|\w*tekniker|\w*ekonom|gruppchef|teamledare|"
    r"\w*samordnare|bes[öo]ksledare)\b", re.I)

# A GENUINE funding/grant CALL (as opposed to a job posting for a funded position): Marie Skłodowska-Curie
# schemes, an explicit fellowship/scholarship/grant competition. These name-strings are unambiguous.
_GRANT_CALL_STRONG = re.compile(
    r"\bmarie sk[lł]odowska-curie\b|\bmarie curie\b|\bfellowship\b|\bscholarship\b|"
    r"\bresearch grant\b|\bfunding (call|scheme|opportunity)\b|\bgrant call\b", re.I)
# 'ERC' alone is genuinely ambiguous: a real funding CALL names the scheme directly ("ERC Starting Grant:
# open call for host institutions"), but an ALREADY-FUNDED job just as routinely describes its own funding
# source the same way ("this postdoc is supported by an ERC-funded research program") - found live, KTH:
# a real postdoc ad ("Postdoc in complex fluid flows") names "an ERC-funded research program" in its body,
# describing the position's OWN funding, not advertising a call - only counted as a call signal when it is
# NOT in that job-describes-its-own-funding shape.
_ERC_CALL = re.compile(r"\berc\b", re.I)
_ERC_JOB_FUNDING_SOURCE = re.compile(
    r"\berc-funded\b|\berc-supported\b|\bfunded by (?:a|an|the) erc\b|\bsupported by (?:a|an|the) erc\b|"
    r"\bpart of an erc\b", re.I)

_INTERNSHIP = re.compile(r"\binternship\b|\bwork placement\b|\bplacement student\b|\bintern\b(?! *ational)", re.I)
# 'studentship' (a funded PhD position, jobs.ac.uk's own term) is deliberately NOT here - it is a job, not a
# short work placement; a bare 'student' assistant role is ambiguous and stays unclassified rather than guessed.

# A bare 'admission(s)' is deliberately NOT a signal here (removed 2026-09-23, found live on KTH): every one
# of a real PhD ad's own "Admission requirements"/"admitted to postgraduate education" eligibility section
# is standard boilerplate on a genuine EMPLOYMENT ad, not an enrollment/open-day notice - only the far more
# specific phrasings below (an actual open day, or a stated deadline FOR admission/enrolment itself) count.
_ENROLLMENT = re.compile(r"\bopen day\b|\bapplication deadline for (admission|enrolment|enrollment)\b", re.I)

# EN + NL + FR academic position/title vocabulary (packet's own list). Kept as ONE combined rule (not per
# language) since aggregator titles are mixed-language and a position only needs to match once.
_ACADEMIC_POSITION = re.compile(
    # 'post-?doc\w*' (not the narrower '(?:toral)?\b') so it covers the French NOUN form too ('post-doctorat',
    # missing English's '-al' suffix - found live on CNRS, a real gap: 'doctoral'/'doctorate' only cover the
    # English adjective/noun pair, not French's own spelling) as well as 'postdoctoral'/'postdoc'/'postdoctorant'.
    r"\bphd\b|\bdoctoral\b|\bdoctorate\b|\bpost-?doc\w*\b|\bresearch (fellow|associate|scientist)\b|"
    r"\b(assistant|associate|full) professor\b|\bprofessor\b|\blecturer\b|\btenure-?track\b|\bfaculty (position|member)\b|"
    r"\bresearcher\b|\bresearch engineer\b|\bstudentship\b|"
    # Dutch
    r"\bpromovendus\b|\bonderzoeker\b|\buniversitair (docent|hoofddocent|hoogleraar)\b|\bpostdoc\b|"
    # French. 'CR'/'DR' (CNRS's own official job-code abbreviations for chargé/directeur de recherche) are
    # DELIBERATELY not added bare - both are extremely common short tokens in unrelated contexts (a plain
    # 'Dr.' title, generic 'CR' abbreviations) with the whole pattern case-insensitive; the full spelled-out
    # forms already cover the same roles unambiguously.
    r"\bcharg[ée] de recherche\b|\bdirecteur de recherche\b|\bmaitre de conf[ée]rences\b|\bmaître de conf[ée]rences\b|"
    r"\bing[ée]nieur\w*\b|\bdoctorant\w*\b|\bpost-?doctorant\w*\b|\bconcours\b|"
    # Swedish (KTH, live 2026-09-23, a real batch of its own feed): doktorand(er) is KTH's own term for a
    # PhD position (not the French 'doctorant', different spelling - no collision, kept separate);
    # postdoktor(er), forskare (bare - deliberately NOT matching 'forskningshandläggare'/'forskningsingenjör',
    # both excluded above as administrative titles despite the 'forsknings-' prefix, since \b anchors to the
    # whole word 'forskare' itself, which those don't contain), forskningsingenjör (a research ENGINEER,
    # kept apart from the bare exclusion prefix check above precisely because it's the genuine research role
    # the 'forsknings-' prefix can also legitimately mean), lektor (incl. 'universitetslektor'/'biträdande
    # lektor'), licentiat (a real Nordic pre-PhD research degree/position, closest fit is the phd bucket).
    r"\bdoktorand(er)?\b|\bpostdoktor(er)?\b|\bforskare\b|\bforskningsingenj[öo]r(er)?\b|\blektor(er)?\b|"
    r"\blicentiat\b", re.I)

# T33 (packet 018, Q6, PM's own real-DB verification): genuine research-SUPPORT staff titles - not a
# researcher/professor themselves, but a real, directly research-adjacent role (a lab technician, a lab
# operations officer, a research-programme officer) that PM's own audit found wrongly buried in 'other'
# alongside genuinely unrelated institutional roles (drivers, caterers, IT helpdesk) that must stay excluded.
# Checked against the TITLE ALONE, never the combined title+description text (same precedent as _PHD_RULE
# below, for the identical reason: found live building this exact fix - a real 'Medical Lab Scientist' ad's
# own REQUIREMENTS text ('2 years of relevant clinical medical laboratory technician experience') matched
# this shape too, misclassifying a role that never actually NAMES itself as a lab technician at all).
_RESEARCH_SUPPORT_STAFF_TITLE = re.compile(
    r"\bresearch (technician|officer)\b|\blaboratory (technician|officer)\b|\blab technician\b|"
    r"\buser research (officer|specialist|lead)\b", re.I)

# T35/T36 (packet 021), resolving T33's own need_input: PM's own explicit ruling on the 2 titles left open
# there ('Workday Solution Expert', 'Kindergarten Team Lead') - BOTH -> employment, as real paid employee
# roles at the institution itself (an IT enterprise-systems role; a childcare/education team lead at a
# university's own kindergarten), added as title-only patterns "consistent with the existing Officer/
# Technician rules" (PM's own words) - same title-only scoping as _RESEARCH_SUPPORT_STAFF_TITLE, for the
# same reason (a description-text mention must never be enough on its own, see that pattern's own comment).
# PM's own explicit instruction: the existing negative guards (Solution Specialist/Microsoft Entra ID
# Specialist/Admissions Officer) stay 'other' - this is deliberately narrower than a bare 'solution expert'/
# 'team lead' pattern, which would also catch those.
_PM_RULED_STAFF_TITLE = re.compile(r"\bworkday solution expert\b|\bkindergarten team lead\b", re.I)


def classify_kind_intl(title: str, description: str = "") -> str:
    """employment / grant_call / internship / enrollment / other. Checked in this order (first hit wins, same
    'never guess' convention as rs_text.classify_kind): a non-academic role is excluded outright; a genuine
    funding/fellowship CALL is grant_call; a short work placement is internship; an admissions notice is
    enrollment; any other recognised academic position title is employment; anything else is 'other' (hidden)."""
    text = f"{title} {description}"
    if _NON_ACADEMIC_ROLE.search(text):
        return "other"
    if _GRANT_CALL_STRONG.search(text) or (_ERC_CALL.search(text) and not _ERC_JOB_FUNDING_SOURCE.search(text)):
        return "grant_call"
    if _INTERNSHIP.search(text):
        return "internship"
    if _ENROLLMENT.search(text):
        return "enrollment"
    if _ACADEMIC_POSITION.search(text):
        return "employment"
    if _RESEARCH_SUPPORT_STAFF_TITLE.search(title) or _PM_RULED_STAFF_TITLE.search(title):
        return "employment"
    return "other"


# listing.position_kind's own CHECK enum (0003_listing.sql) - 'phd' is a real bucket here (unlike rs_text.py's
# position_kind(), which has no Serbian equivalent and instead splits a PhD-track title into kind='researcher_title'
# + rs_research_title='istrazivac_pripravnik', a column that stays NULL for every international listing).
# 'phd' is checked LAST (position_kind_intl scans this list in order, first hit wins). A bare 'phd'/'doctoral'
# match is NOT safe as a first-choice signal: a real Professor/Lecturer ad's REQUIREMENTS section routinely says
# "candidates must hold a doctoral degree" or "100% of faculty hold doctoral degrees" - found live on jobs.ac.uk,
# not hypothesised - which would otherwise misclassify a professor ad as a PhD studentship. Only a PHRASE naming
# the role itself as a PhD position is trusted; a bare degree-requirement mention is not enough on its own.
_POSITION_RULES_INTL = [
    ("postdoc", r"\bpost-?doc\w*\b|\bpostdoktor(er)?\b"),
    ("professor", r"\b(assistant|associate|full) professor\b|\bprofessor\b|\buniversitair hoogleraar\b"),
    ("lecturer", r"\blecturer\b|\buniversitair (docent|hoofddocent)\b|\bma[iî]tre de conf[ée]rences\b|"
                r"\blektor(er)?\b"),                          # SV: (universitets/biträdande) lektor
    ("research_assistant", r"\bresearch (fellow|associate|scientist|engineer)\b|\bonderzoeker\b|"
                          r"\bcharg[ée] de recherche\b|\bing[ée]nieur de recherche\b|"
                          r"\bforskningsingenj[öo]r(er)?\b"),  # SV: research engineer
    ("researcher_title", r"\bresearcher\b|\bdirecteur de recherche\b|\bforskare\b"),  # SV: forskare
    ("intern", r"\bintern(ship)?\b|\bplacement student\b"),
    # 'fellowship' added 2026-09-23 (KU DK, live): Danish/Nordic universities routinely title a PhD studentship
    # "PhD fellowship in ..." - a real, common phrasing this bucket previously had no noun for at all.
    # 'doktorand(er)'/'licentiat' added 2026-09-23 (KTH, live): Swedish's own PhD-position nouns - licentiat is
    # a genuine Nordic pre-PhD research degree/position, the closest existing bucket to file it under.
    # 'doctoral student' added 2026-09-23 (KTH, live): the site's own English translation of 'doktorand' -
    # a real, standard English term for a PhD position this bucket had no noun for at all until now (only
    # 'doctoral position/studentship/researcher/candidate' were covered; 'student' was missing).
    # '(?<!post-)(?<!post)' guard added 2026-09-23 (Inria, live): a real 'Post-Doctorant F/H ...' title
    # (French for postdoctoral researcher) contains 'Doctorant' right after its own hyphen, with a genuine
    # word boundary there ('-' is a non-word character) - so bare '\bdoctorant\w*\b' matched it too, and
    # because this rule is checked against the TITLE ALONE before the postdoc rule ever runs (the KU DK
    # title-priority fix below), a genuine POSTDOC title was misclassified as phd. The guard rejects a
    # 'doctorant' match immediately preceded by 'post'/'post-' - the postdoc rule (checked first in the
    # normal ordered fallback) still correctly claims those titles once this one stops intercepting them.
    ("phd", r"\bphd (position|studentship|student|candidate|vacancy|fellowship)\b|\bdoctoral (position|"
           r"studentship|researcher|candidate|student)\b|\bstudentship\b|\bpromovendus\b|"
           r"(?<!post-)(?<!post)\bdoctorant\w*\b|\bdoktorand(er)?\b|\blicentiat\b"),
]


_PHD_RULE = next(rx for v, rx in _POSITION_RULES_INTL if v == "phd")


def position_kind_intl(title: str, description: str = "") -> str:
    """listing.position_kind's own enum value (phd/postdoc/professor/lecturer/research_assistant/
    researcher_title/technician/admin/intern/other) - 'other' when nothing specific is named, same convention
    as rs_text.position_kind()'s first element, just without a second (Serbian-only) rs_title value at all.
    The description is also searched: a real title as vague as 'Faculty Position in Finance and Accounting'
    only names the actual rank ('Assistant/Associate/Full Professor') in its body text (jobs.ac.uk, live).

    One rule, 'phd', is checked against the TITLE ALONE first and wins outright if it matches there, before
    any other rule ever sees the combined text. Found live (KU DK, 2026-09-23): a real ad titled "PhD
    fellowship in Computational Pharmacology..." names its "Principal supervisor: Associate Professor X" in
    the body - scanning title+description together let the supervisor's OWN rank outrank the advertised PhD
    role. A title-level 'PhD position/fellowship/studentship/...' statement is essentially never wrong the way
    a bare 'professor'/'research fellow' mention can be (the latter two stay pure combined-text rules
    specifically so a body's more specific 'postdoctoral research fellow' can still out-rank a vaguer title
    like 'Research Fellow in Particle Physics', per the CERN fixture - only 'phd' gets this precedence)."""
    if re.search(_PHD_RULE, title, re.I):
        return "phd"
    text = f"{title} {description}"
    return next((v for v, rx in _POSITION_RULES_INTL if re.search(rx, text, re.I)), "other")
