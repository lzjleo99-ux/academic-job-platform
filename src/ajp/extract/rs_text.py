"""Rule-based extraction for Serbian ads, script-independent (sr-Cyrl and sr-Latn).

Everything is matched on ``normalize_title`` output (Cyrillic transliterated, diacritics stripped,
lower-cased), so one rule set serves both scripts. First cut for T8: precision over recall, and each
extractor returns None / 'other' rather than guessing. Raw spans are returned wherever a value is set.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from ajp.dedupe_keys import normalize_title

ACADEMIC_RX = re.compile(r"\b(fakultet\w*|institut\w*|univerzitet\w*|nauc\w+|istrazivac\w*|istrazivan\w+|asistent\w*|docent\w*|"
                         r"profesor\w*|saradnik\w* u nastavi|laboratorij\w*)\b")

_KIND_RULES = [  # order matters: first hit wins
    # student intake only: a bare 'upis' also occurs in 'upis u Lekarsku komoru' / 'upisan na doktorske studije'
    ("enrollment", r"\bkonkurs za upis\b|\bupis (studenata|kandidata|novih|na (osnovne|master|doktorske|specijalisticke)|u prvu)\b|"
                   r"\bupisni (rok|rokovi)\b|\bprijemni (ispit\w*|test)\b|\brezultati prijemnog\b"),
    ("internship", r"\bpraks\w+\b|\bstudentski posao\b|\bpraktikant\w*\b|\bdemonstrator\w*\b"),
    # 'mobilnost' (Erasmus+/staff-mobility calls) is grant_call per PM's own r7_reminders - pr.ac.rs's category/
    # konkursi/ is mostly this exact shape ('Konkurs za Erazmus+ mobilnost studenata na Univerzitetu u Mariboru').
    ("grant_call", r"\bjavni poziv\b|\bstipendij\w*\b|\bfinansiranj\w*\b|\bprojekat\b|\bprojekt\w*\b|\bfinansijsk\w+ podrsk\w+|"
                   r"\bdodel\w+ (finansijsk\w+|stipendij\w+|sredstav\w+|nagrad\w+)|\bmobilnost\w*\b"),
    # NB: a bare 'konkurs za' / 'oglas za' is NOT an employment marker (student contests, workshops, prizes);
    # likewise a bare 'posao/poslovi' (job/jobs) alone is too common/ambiguous a word to trust standalone - only
    # counted together WITH 'konkurs' nearby (medf.kg.ac.rs names its employment PDFs 'Konkursi_od_DDMMYYYY_
    # POSLOVI.pdf', its own site-specific way of tagging which competitions are job openings).
    ("employment", r"\bradno mest\w*\b|\bradna mesta\b|\bizbor\w* u zvanj\w*\b|\bizbor (nastavnika|saradnika)\b|\bna odredjeno\b|\bna neodredjeno\b|"
                   r"\bzasnivanje radnog odnosa\b|\bpopunjavanj\w+ radnog\b|\botvoren\w* pozicij\w*\b|"
                   r"\bkonkurs\w*.{0,20}\bposlovi\b|\bposlovi\b.{0,20}konkurs\w*"),
]
_POSITION_RULES = [
    ("researcher_title", r"\b(istrazivac[- ]pripravnik|istrazivac[- ]saradnik|naucni saradnik|visi naucni saradnik|naucni savetnik)\b"),
    ("postdoc", r"\bpost-?doc\w*\b|\bpostdoktor\w*\b"),
    ("professor", r"\b(redovni|vanredni)\s+profesor\w*\b|\bprofesor\w*\b|\bdocent\w*\b"),
    ("lecturer", r"\bsaradnik\w* u nastavi\b|\basistent\w*\b|\bpredavac\w*\b|\blektor\w*\b"),
    ("research_assistant", r"\bistrazivac\w*\b"),
    ("technician", r"\btehnicar\w*\b|\blaborant\w*\b|\bstrucni saradnik\b"),
    ("admin", r"\breferent\w*\b|\bsekretar\w*\b|\badministrativ\w*\b|\bsluzbenik\b"),
    ("intern", r"\bpripravnik\w*\b|\bpraktikant\w*\b|\bvolonter\w*\b"),
]
_RS_TITLES = [("istrazivac_pripravnik", r"istrazivac[- ]pripravnik"), ("istrazivac_saradnik", r"istrazivac[- ]saradnik"),
              ("visi_naucni_saradnik", r"visi naucni saradnik"), ("naucni_savetnik", r"naucni savetnik"),
              ("naucni_saradnik", r"naucni saradnik")]  # 'visi ...' before the plain 'naucni saradnik'
# 'student doktorskih studija' (enrolled) deliberately does NOT count as holding a PhD
_DEGREE_RULES = [("phd", r"\bdoktorat\w*\b|\bdoktor nauka\b|\bnaucni stepen doktora\b"),
                 ("msc", r"\bmaster\w*\b|\bspecijalisticke akademske\b|\b300 espb\b"),
                 ("bsc", r"\bosnovne akademske\b|\bosnovnim akademskim\b|\bosnovne strukovne\b")]


def script_of(text: str) -> str:
    cyr = len(re.findall(r"[Ѐ-ӿ]", text))
    lat = len(re.findall(r"[A-Za-zĆćČčĐđŠšŽž]", text))
    if cyr == lat == 0:
        return "und"
    return "sr-Cyrl" if cyr >= lat else "sr-Latn"


def is_academic(text: str) -> bool:
    return bool(ACADEMIC_RX.search(normalize_title(text)))


_ACAD_POSITION = re.compile(r"\b(istrazivac(?!k)\w*|istrazivack\w+ (pozicij|radn)\w+|naucni (saradnik|savetnik)\w*|visi naucni saradnik\w*|"
                            r"docent\w*|profesor\w*|asistent\w*|saradnik\w* u nastavi|lektor\w*|predavac\w*|"
                            r"postdoc\w*|postdoktor\w*|demonstrator\w*)\b")
# school support roles that share the word 'asistent'
_NOT_ACAD_POSITION = re.compile(r"\bpedagosk\w* (\(?romsk\w*\)? )?asistent\w*|\basistent\w* za rome\b|\bromski asistent\w*|"
                                r"\blicni pratilac\b|\bzdravstveni asistent\w*|\bstomatolosk\w* asistent\w*|"
                                # 'Noć istraživača' (Researchers' Night) is a recurring EU-wide public-outreach
                                # event name, not a position - it just happens to contain 'istrazivac'.
                                r"\bnoc istrazivaca\b")
# news NARRATING that a researcher/institute did something (attended, presented, hosted, was awarded) is not a
# job opening, even though it names a researcher; a genuine call is framed as an announcement/invitation, never
# past tense. Stems only (not full verb forms + \w+): Serbian palatalises several of these between the verb and
# its noun ('predstaviti' -> 'predstavljen', 'dodeliti' -> 'dodela', 'osvojiti' -> 'osvojen'), so a literal verb
# prefix like 'predstavi' would miss the noun/passive-participle form actually seen in news headlines.
_NARRATIVE_PAST = re.compile(r"\bucestvova\w*\b|\bucesc\w*\b|\bucesni\w*\b|\bpredstav\w*\b|\bodrza\w*\b|"
                             r"\bdomacin\w*\b|\bdobio\w*\b|\bdobila\w*\b|\bdobili\w*\b|\bdobitni\w*\b|"
                             r"\bosvoj\w*\b|\bobelez\w*\b")
# NOT included: 'dodela/dodeljivanje' (awarding) - tense-neutral in Serbian; 'konkurs za DODELU stipendija' (a
# genuine future call) and 'svecana DODELA priznanja' (a past ceremony) use the identical noun form, so it
# cannot disambiguate anything and was dropped rather than risk the well-established scholarship-call pattern.
# 'ucesce/ucesnik' (participation/participant, NOUN) is the SAME kind of tense-neutral trap, but unlike dodela it
# has an unambiguous FORWARD-LOOKING shape too: 'poziv/konkurs ZA ucesce' ('a call FOR participation') is always
# an invitation, never a narrative - filum.kg.ac.rs's 'Otvoren poziv za ucesce u projektu SPARK' was being
# wrongly hidden by the bare word. Removing 'ucesc*/ucesni*' outright reopened the ORIGINAL narrative-news false
# positives this guard exists for ('Ucesce istrazivaca Instituta na konferenciji', 'Istrazivaci ... ucesnici
# seminara'), so both directions have a real, live counter-example - the fix is this narrow override, not
# removal: the two words still gate UNLESS the call-shaped prefix is right there too.
_CALL_FOR_PARTICIPATION = re.compile(r"\b(poziv\w*|konkurs\w*)\s+za\s+ucesc\w*\b")


# primary/secondary schools and kindergartens: 'asistent u nastavi' there is classroom support, not academia
_K12_EMPLOYER = re.compile(r"\b(osnovn\w+|srednj\w+|os|gimnazij\w*|predskolsk\w+|vrtic\w*|muzick\w+ skol\w+|umetnick\w+ skol\w+)\b")

# Standing PROCEDURAL documents (a price list, a rulebook, a blank application form/template) state the same
# 'izbor u zvanje' phrase a genuine employment ad does, but are neither a call to action nor about a specific
# candidate (so person_specific_procedure doesn't catch them either) - they describe the promotion PROCESS
# itself, reusable across every future opening. Seen live: 'Cenovnik troskova izbora u zvanja' (fee schedule,
# tfn.uns.ac.rs). 'cenovnik'/'tarifnik' (price list/tariff) are narrow, unambiguous document-type labels
# essentially never used to open a genuine ad OR mentioned in passing within one - same trade-off as the
# deliberately-NOT-added 'dodela' above, just with no live counter-example found for these two.
_ADMIN_FEE_DOCUMENT = re.compile(r"\bcenovnik\w*\b|\btarifnik\w*\b")

# 'pravilnik' (rulebook), 'obrazac' (form/template) and 'spisak' (list/roster) are the SAME idea but unsafe to
# search for anywhere in arbitrary-length text: elfak.ni.ac.rs's real ad bodies routinely cite 'Pravilnika o
# blizim uslovima...' and ask candidates to submit a 'spisak radova' (list of publications) and a filled
# 'obrazac' as part of a GENUINE call - scanning a ~2000-char ad body for these words silently hid real open
# positions (found live, not hypothesised). They only mark a standing document when they are what the text
# itself IS ABOUT - true only for a short, title-shaped string (every real false positive seen was well under
# 150 chars: 'Odluka o primeni Pravilnika ... za izbor u zvanje nastavnika', 'Biografski podaci o kandidatima
# ... (obrazac 1A NAUKA)', 'Praksa - spisak zakljucenih ugovora') - so they are gated by length, not searched
# unconditionally like the two fee-schedule terms above.
_STANDING_DOCUMENT_TITLE_ONLY = re.compile(r"\bpravilnik\w*\b|\bobrazac\w*\b|\bspisak\w*\b")
_STANDING_DOCUMENT_TITLE_MAX_LEN = 150


def is_standing_procedure_document(text: str) -> bool:
    """True for a price list, rulebook or blank form template about the promotion PROCESS itself (see
    _ADMIN_FEE_DOCUMENT above) - exposed so callers with their own fallback classification path (e.g.
    board._keep_document's board-section 'hint') can gate on it too, the same way they already gate on
    person_specific_procedure."""
    n = normalize_title(text)
    if _ADMIN_FEE_DOCUMENT.search(n):
        return True
    return len(text) <= _STANDING_DOCUMENT_TITLE_MAX_LEN and bool(_STANDING_DOCUMENT_TITLE_ONLY.search(n))


def academic_ad(position: str, employer: str = "") -> bool:
    """First-cut academic relevance, decided on the *position* only. The employer is deliberately ignored:
    faculties also hire cleaners and clerks, 'Univerzitetski klinicki centar' is a hospital, and the conditions text
    of a hospital ad mentions 'medicinski fakultet'. Pedagogical (Roma) assistants in primary schools are not academic."""
    n = normalize_title(position)
    if _K12_EMPLOYER.search(normalize_title(employer)) or (_NARRATIVE_PAST.search(n) and not _CALL_FOR_PARTICIPATION.search(n)):
        return False
    return bool(_ACAD_POSITION.search(n)) and not _NOT_ACAD_POSITION.search(n)


EXCLUDED_PERSON_SPECIFIC = "person_specific_title_procedure"
# Procedure-stage notices of the academic election process concern a specific candidate (or staff member): a notice that a
# procedure was started, the committee's referat / izvestaj, the decision, the conferred title. They are public legal notices,
# not recruitment, so they never become listings (data minimisation, ZZLP/GDPR; PM ruling R7-refinement).
_PROCEDURE_STRONG = re.compile(
    r"\bpokrenut\w* postupak\b|\bpostupak\w* (za )?sticanj\w+|\breferat\w*\b|\bizvestaj\w*\b|\bodluk\w+ o izboru\b|"
    r"\bstice zvanje\b|\bsteklo zvanje\b|\b(izabran\w*|imenovan\w*|razresen\w*) u zvanje\b|\bpredlog\w* (komisije )?za izbor\b|"
    # dossier ATTACHMENTS for a specific candidate's title procedure (itn.sanu.ac.rs: filenames literally carry the
    # candidate's initials/surname, e.g. 'Izborni-materijal-JZ-naucni-saradnik.pdf', 'Dokumentacija-izbor-za-...').
    # 'prilozi'/'materijal' alone is too generic (an open call's own application form is also an 'attachment'), so
    # only the combined election-dossier phrasing counts, never the bare word.
    r"\bizborn\w* materijal\w*\b|\bdokumentacij\w* .{0,30}\bizbor\w*\b|\bmaterijal\w* za izbor\w*\b|"
    # tfn.uns.ac.rs: a decision setting/changing the DATE of a specific candidate's qualifying lecture
    # ('Odluka o (promeni/utvrdjivanju) termina pristupnog predavanja za kandidat(kinj)u ...') - the scheduling
    # of that lecture is always for one named candidate, so 'termin pristupnog predavanja' alone is a safe marker
    # (a genuine open call never announces a lecture TIME SLOT, only that a procedure exists).
    r"\btermin\w* pristupnog predavanj\w*\b|"
    # pfu.kg.ac.rs: the qualifying/introductory LECTURE ITSELF ('Pristupno(a) predavanje/predavanja u postupku
    # izbora u zvanje docent') is always one candidate's own procedural event, even when the title never names
    # them - the same PM framing already established at the URL level for hit-vb's '/pristupna-predavanja'
    # sibling path (R7), now also caught in board text mixed into a general notices category.
    r"\bpristupn\w* predavanj\w*\b")
_PROCEDURE_REVIEW = re.compile(r"\buvid\w* javnosti\b|\bjavni uvid\b")
_OPEN_CALL = re.compile(r"\b(raspisan\w*|oglasen\w*|raspisuje\w*)\b.*\bkonkurs\b|\bkonkurs\w* za izbor\b|\bkonkurs za (radno mesto|radna mesta)\b|"
                        r"\boglas za (izbor|popunjavanje)\b")


def person_specific_procedure(title: str) -> Optional[str]:
    """EXCLUDED_PERSON_SPECIFIC if the title is a procedure-stage notice, else None. Decided on the notice TYPE, not on
    spotting a name (proper names cannot be told from institution names reliably). An open call ('raspisan konkurs za izbor
    u zvanje ...', 'konkurs za izbor ...') is never excluded by a mere 'uvid javnosti' mention; 'referat', 'izvestaj', 'pokrenut
    postupak', 'odluka o izboru', 'stice zvanje' always are."""
    n = normalize_title(title)
    if _PROCEDURE_STRONG.search(n):
        return EXCLUDED_PERSON_SPECIFIC
    if _PROCEDURE_REVIEW.search(n) and not _OPEN_CALL.search(n):
        return EXCLUDED_PERSON_SPECIFIC
    return None


# pr.ac.rs / filum.kg.ac.rs: 'Javni poziv za solisticki nastup sa Simfonijskim orkestrom' is a PERFORMER
# AUDITION (a student/extracurricular opportunity), not an academic grant, even though it uses the same bare
# 'javni poziv' phrase a real research grant call does. Scoped to co-occurrence WITHIN the title only, so a
# genuine call that happens to use 'nastupni'/'nastupa' (an inaugural LECTURE, 'nastupno predavanje') is
# untouched - PM ruling 2026-09-22, reusable for arts-academy boards in later fan-out.
_JAVNI_POZIV = re.compile(r"\bjavni poziv\b")
_PERFORMANCE_AUDITION = re.compile(r"\bsolistick\w* nastup\w*\b|\baudicij\w*\b|\bnastup\w* sa orkestr\w*\b|"
                                   r"\bnastup\w* sa hor\w*\b|\bza hor\b|\bza orkestar\b")


def classify_kind(text: str, *, section_is_job_board: bool = False) -> str:
    """employment / enrollment / grant_call / internship / other. Unclassified stays 'other' (hidden by default)
    unless the source section itself is a job board AND the text has an employment marker."""
    n = normalize_title(text)
    for kind, rx in _KIND_RULES:
        if re.search(rx, n):
            # 'projekat'/'stipendija' etc. also show up in ordinary news narrating something that already
            # happened (an award ceremony, a finished project's results) - not a call to action. Only
            # employment/grant_call are gated on tense: enrollment/internship markers are specific enough on
            # their own. The standing-document guard applies more broadly - 'spisak zakljucenih ugovora' trips
            # the bare 'praks*' internship marker the same way 'cenovnik ... izbora u zvanja' trips employment's.
            if kind in ("employment", "grant_call") and _NARRATIVE_PAST.search(n) and not _CALL_FOR_PARTICIPATION.search(n):
                continue
            if kind in ("employment", "grant_call", "internship") and is_standing_procedure_document(text):
                continue
            if kind == "grant_call" and _JAVNI_POZIV.search(n) and _PERFORMANCE_AUDITION.search(n):
                continue
            return kind
    return "other"


def position_kind(title: str) -> Tuple[str, Optional[str]]:
    n = normalize_title(title)
    rs = next((v for v, rx in _RS_TITLES if re.search(rx, n)), None)
    kind = next((k for k, rx in _POSITION_RULES if re.search(rx, n)), "other")
    return kind, rs


def degree_required(text: str) -> Optional[str]:
    """Highest explicitly named level; None when the text names no specific level. 'doktor medicine' is an
    MD, not a PhD, so only 'doktorat / doktor nauka / naucni stepen doktora' count."""
    n = normalize_title(text)
    for value, rx in _DEGREE_RULES:
        if re.search(rx, n):
            return value
    return None


def contract_type(text: str) -> Optional[Dict[str, str]]:
    n = normalize_title(text)
    m = re.search(r"\bna odredjeno(?: vreme)?\b", n)
    if m:
        return {"v": "fixed", "raw": m.group(0)}
    m = re.search(r"\bna neodredjeno(?: vreme)?\b", n)
    return {"v": "permanent", "raw": m.group(0)} if m else None


_DUR = re.compile(r"\b(do|na period od|u trajanju od|na|vreme od)\s+(\d{1,2})\s+(mesec\w*|godin\w*)\b")


def duration_months(text: str) -> Optional[Dict[str, Any]]:
    """'do 6 meseci' -> 6 (an upper bound, so conf 0.6). Only explicit month/year spans are read."""
    m = _DUR.search(normalize_title(text))
    if not m:
        return None
    n = int(m.group(2)) * (12 if m.group(3).startswith("godin") else 1)
    return {"v": n, "raw": m.group(0), "conf": 0.6 if m.group(1) == "do" else 0.8}


_DL_A = re.compile(r"\brok za (?:podnosenje )?prijav\w*(?: na (?:konkurs|oglas))? (?:je|iznosi) (\d{1,3}) dan\w*(?P<tail>.{0,140})")
_DL_B = re.compile(r"\bu roku od (\d{1,3}) dan\w* od dana (?:objavljivanja|oglasavanja)(?! (?:resenja|odluke|obavestenja))")
_PUBLICATION = re.compile(r"objavljivanja|oglasavanja")


def deadline_days(text: str) -> Optional[Dict[str, Any]]:
    """Explicit application-deadline statements counted from publication of the ad:
      A  'rok za podnosenje prijava je 15 dana [i pocinje da tece narednog dana] od dana objavljivanja/oglasavanja ...'
      B  '... u roku od 15 dana od dana objavljivanja'
    Other 'N dana od dana ...' sentences (notification of a code number, appeals against a decision) are not deadlines."""
    n = normalize_title(text)
    m = _DL_A.search(n)
    if m and _PUBLICATION.search(m.group("tail")) and not re.search(r"^.{0,140}?objavljivanja (resenja|odluke)", m.group("tail")):
        return {"days": int(m.group(1)), "raw": (m.group(0)[:120]).strip()}
    m = _DL_B.search(n)
    return {"days": int(m.group(1)), "raw": m.group(0)} if m else None
