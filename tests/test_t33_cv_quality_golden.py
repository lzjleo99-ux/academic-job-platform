"""T33 (packet 018, PM-blocking): profile extraction accuracy on PM's own 2 real golden CVs (Q1-Q3), plus a
3rd social-sciences CV proving the Q1 discipline fix never over-corrects. PM's own exact required assertions
from the packet's own `requirements` list.

Two independent fixture sources, both exercised: (a) tests/cv_fixtures/pm_real_*.{docx,pdf} - the ACTUAL
files PM tested with (packet 018's own later `golden_fixture_files` update, /tmp/test_cv.docx + .pdf, copied
in verbatim as committed binary fixtures - the same convention T31 used for PM's own real ground-truth
documents), and (b) this module's own earlier synthetic reconstruction (build_fixtures.py), built from PM's
first-pass textual description before the real files arrived. Both are kept: the real files are what PM
actually verified against and found 2 further real bugs re-running this exact test against them (desired_
cities picking up a trailing 'visa sponsorship...' clause; skills swallowing the next two sections whole on
the PDF, both fixed live - see profile_extract.py's own T33 comments); the synthetic ones stay as a second,
independent regression fixture using slightly different real-CV phrasing shapes."""
from pathlib import Path

from ajp.applicants.profile_extract import DISCIPLINE_KEYWORDS, extract_disciplines, parse_cv_and_extract_profile
from cv_fixtures.build_fixtures import build_mech_eng_cv_pdf, build_ml_cv_docx, build_social_science_cv_docx

FIXTURES = Path(__file__).parent / "cv_fixtures"


def _assert_ml_cv_profile(profile):
    # Q1: disciplines set contains the CS/ICT discipline and NOT Social sciences.
    assert "Natural sciences" in profile["disciplines"]
    assert "Social sciences" not in profile["disciplines"]
    # Q2: skills == the clean 7-item set - never a header, a language line, or a preference sentence.
    assert profile["skills"] == ["Python", "PyTorch", "NLP", "data science", "statistics", "Linux", "Docker"]
    # Q3: desired_countries == DE/SE/DK/NL (order-insensitive).
    assert set(profile["desired_countries"]) == {"DE", "SE", "DK", "NL"}
    assert profile["desired_cities"] == []
    assert profile["remote_ok"] is True
    assert profile["ft_pt"] == "full"
    assert profile["degree"]["v"] in ("postdoc", "phd")   # postdoc/PhD-eligible, per PM's own wording
    assert len(profile["languages"]) == 3
    assert all(l["level"] for l in profile["languages"])
    assert profile["min_salary_eur_year"] == 38000.0


def _assert_mech_eng_cv_profile(profile):
    assert "Engineering and technology" in profile["disciplines"]
    assert "Social sciences" not in profile["disciplines"]
    for skill in ("SolidWorks", "CAD", "FEM", "ANSYS", "Python"):
        assert skill in profile["skills"]
    assert set(profile["desired_countries"]) == {"AT", "DE"}
    assert profile["ft_pt"] == "full"


def test_q1_q2_q3_pms_own_real_ml_cs_docx_file_matches_every_required_assertion():
    """PM's own actual real file (/tmp/test_cv.docx in packet 018's own golden_fixture_files update),
    committed verbatim - not a reconstruction. Found live re-running against it: a real trailing clause
    ('...visa sponsorship desired in EU.') was leaking into desired_cities - fixed in profile_extract.py by
    bounding the desired-location capture to one sentence, never spilling into the next."""
    _, profile = parse_cv_and_extract_profile(FIXTURES / "pm_real_ml_cv.docx")
    _assert_ml_cv_profile(profile)


def test_q1_q2_q3_pms_own_real_mechanical_engineering_pdf_file_matches_every_required_assertion():
    """PM's own actual real file (/tmp/test_cv.pdf), committed verbatim. Found live re-running against it: a
    real inline 'Languages: ...'/'Preferences: ...' header-WITH-content-on-the-same-line shape (not a bare
    header alone on its own line, the only shape the section-boundary fix originally recognised) let the
    whole rest of the document get swallowed into 'skills' - fixed by anchoring the section-boundary check to
    the START of a line (header word + optional colon) rather than requiring nothing after it."""
    _, profile = parse_cv_and_extract_profile(FIXTURES / "pm_real_mech_eng_cv.pdf")
    _assert_mech_eng_cv_profile(profile)


def test_q1_q2_q3_the_ml_cs_golden_cv_docx_matches_pms_own_exact_required_assertions(tmp_path):
    path = build_ml_cv_docx(tmp_path / "ml_cv.docx")
    _, profile = parse_cv_and_extract_profile(path)
    _assert_ml_cv_profile(profile)


def test_q1_q2_q3_the_mechanical_engineering_golden_cv_pdf_matches_pms_own_exact_required_assertions(tmp_path):
    """Proves format-independence: the SAME extraction pipeline, on a PDF this time - the inline
    'Skills: a, b, c.' shape (no separate header line) that previously extracted skills=[] entirely."""
    path = build_mech_eng_cv_pdf(tmp_path / "me_cv.pdf")
    _, profile = parse_cv_and_extract_profile(path)
    for skill in ("SolidWorks", "CAD", "FEM", "ANSYS", "Python"):
        assert skill in profile["skills"]
    assert set(profile["desired_countries"]) == {"AT", "DE"}


def test_the_social_science_cv_still_correctly_classifies_as_social_sciences_no_over_correction(tmp_path):
    """PM's own explicit ask: prove the Q1 fix (removing the ambiguous bare 'education' keyword) never
    over-corrects a genuine social-sciences/humanities candidate out of their own real field."""
    path = build_social_science_cv_docx(tmp_path / "social_cv.docx")
    _, profile = parse_cv_and_extract_profile(path)
    assert "Social sciences" in profile["disciplines"]
    assert profile["skills"] == ["Qualitative research", "SPSS", "survey design", "academic writing"]


# ---- Q1 negative tests, PM's own exact wording: "data science must NOT yield Social sciences; social
# work/sociology CV must still yield Social sciences" -----------------------------------------------------
def test_data_science_and_computer_science_never_yield_social_sciences():
    text = "Skilled in data science, computer science, and machine learning research."
    assert "Social sciences" not in extract_disciplines(text)
    assert "Natural sciences" in extract_disciplines(text)


def test_a_bare_education_section_header_never_yields_social_sciences():
    """The real root cause found live: 'Education' is a near-universal CV section header (listing the
    author's own degrees), not a statement about the ACADEMIC FIELD of education - must never false-trigger
    on its own, whatever discipline the rest of the CV actually states."""
    text = "Skills: Python, Docker.\nEducation\nPhD in Computer Science, University of Belgrade, 2024."
    assert "Social sciences" not in extract_disciplines(text)


def test_a_genuine_education_studies_or_pedagogy_statement_still_classifies_as_social_sciences():
    assert "Social sciences" in extract_disciplines("My research focus is education studies and pedagogy.")
    assert "Social sciences" in extract_disciplines("PhD in Educational Psychology.")


def test_social_work_and_sociology_are_still_correctly_classified():
    assert "Social sciences" in extract_disciplines("Background in sociology and social work research.")


def test_every_discipline_keyword_bucket_is_internally_consistent_no_bucket_is_ever_empty():
    """Sanity guard: every one of the 6 OECD Frascati top-level buckets this module claims to support must
    actually have at least one real keyword - an empty bucket would silently never match anything."""
    for field, keywords in DISCIPLINE_KEYWORDS.items():
        assert keywords, f"{field!r} has no keywords at all"
