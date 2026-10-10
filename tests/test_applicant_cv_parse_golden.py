"""T25 acceptance: 'CV fixtures (pdf/docx), parse -> profile golden'. Both formats carry IDENTICAL text
content (tests/cv_fixtures) so the same golden profile must come out of either one - a real regression test
against the exact PDF-vs-DOCX text-extraction difference found live building this module (pdfplumber
collapses blank-line paragraph breaks DOCX preserves; see test_applicant_profile_extract's own regression
test for the specific bug this caused and fixed)."""
from ajp.applicants.profile_extract import parse_cv_and_extract_profile
from cv_fixtures.build_fixtures import build_docx, build_pdf

GOLDEN_PROFILE = {
    "disciplines": ["Natural sciences", "Engineering and technology"],
    # T36c (packet 021): the SPECIFIC keywords that matched (not the whole bucket) - this CV's own text
    # states "quantum physics, computer science, materials science" verbatim, plus "machine learning" among
    # its own skills-adjacent research interests.
    "discipline_terms": ["physics", "computer science", "machine learning", "materials science"],
    # T36e (packet 021): degree now also carries in_progress/highest_completed (both purely additive) -
    # neither the PhD (2022, no in-progress marker) nor the Postdoctoral role (2022-2024, ditto) here is
    # marked in-progress/aspirational, so the highest of the two (postdoc) is simply the primary value with
    # nothing separately "completed" beyond itself.
    "degree": {"v": "postdoc", "raw": "Postdoctoral", "in_progress": False, "highest_completed": None},
    "skills": ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"],
    "languages": [{"lang": "English", "level": "fluent"}, {"lang": "Serbian", "level": "native"},
                 {"lang": "German", "level": "basic"}],
    "desired_countries": ["DE", "NL", "RS"], "desired_cities": [],
    "remote_ok": True, "ft_pt": "full", "years_experience": 5,
    "min_salary_eur_year": 45000.0, "min_salary_raw": "Expected salary: EUR 45000 per year",
    "foreigner_visa_note": "Serbian citizen",
}


def test_docx_fixture_matches_the_golden_profile_exactly(tmp_path):
    path = build_docx(tmp_path / "cv.docx")
    _, profile = parse_cv_and_extract_profile(path)
    assert profile == GOLDEN_PROFILE


def test_pdf_fixture_matches_the_identical_golden_profile(tmp_path):
    path = build_pdf(tmp_path / "cv.pdf")
    _, profile = parse_cv_and_extract_profile(path)
    assert profile == GOLDEN_PROFILE


def test_full_text_is_returned_alongside_the_profile_for_storage(tmp_path):
    path = build_docx(tmp_path / "cv.docx")
    text, _ = parse_cv_and_extract_profile(path)
    assert "Jane Doe" in text and "PhD in Physics" in text
