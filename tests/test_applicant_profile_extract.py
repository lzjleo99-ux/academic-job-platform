"""T25 acceptance: CV text -> structured profile extraction. D6 unchanged - every field is either real,
matched signal or an honest empty/None, never fabricated."""
import pytest

from ajp.applicants import profile_extract as pe

CV = """Jane Doe

PhD in Physics, University of Belgrade, 2022
Postdoctoral Researcher, Max Planck Institute, 2022-2024

Research interests: quantum physics, computer science, materials science

Skills:
Python, MATLAB, machine learning, data analysis, LaTeX

Languages:
English: fluent
Serbian: native
German: basic

I have 5 years of experience in experimental physics research.
Open to remote work. Full-time preferred.
Desired location: Germany, Netherlands, Serbia

Expected salary: EUR 45000 per year

I am a Serbian citizen and eligible to work in the EU without sponsorship.
"""


def test_disciplines_are_matched_by_real_keyword_not_guessed():
    assert set(pe.extract_disciplines(CV)) == {"Natural sciences", "Engineering and technology"}


def test_no_discipline_keyword_at_all_returns_an_empty_list_never_guessed():
    assert pe.extract_disciplines("A CV with no field-specific keywords at all.") == []


@pytest.mark.parametrize("text,expected", [
    ("I hold a Ph.D. in Chemistry.", "phd"),
    ("Postdoctoral researcher since 2023.", "postdoc"),
    ("Associate Professor of Biology.", "prof"),
    ("M.Sc. in Computer Science, 2019.", "msc"),
    ("B.Sc. in Mathematics, 2017.", "bsc"),
])
def test_degree_extraction_picks_the_real_stated_level(text, expected):
    d = pe.extract_degree(text)
    assert d is not None and d["v"] == expected


def test_no_degree_stated_at_all_returns_none():
    assert pe.extract_degree("A CV with no degree information stated.") is None


def test_highest_of_several_stated_degrees_wins():
    """Professor is checked before postdoc/phd/msc/bsc - a CV listing its whole academic history reports
    the highest career stage, not merely the FIRST one matched."""
    d = pe.extract_degree("B.Sc. in Physics (2015), M.Sc. (2017), Ph.D. (2021), now Associate Professor.")
    assert d["v"] == "prof"


# ---- T36e (packet 021): distinguishing a CURRENTLY-PURSUED level from a COMPLETED one, and excluding a
# purely ASPIRATIONAL mention (something the applicant is SEEKING, not a credential they hold or are even
# enrolled in) from either. -----------------------------------------------------------------------------
def test_a_phd_candidate_is_in_progress_not_a_completed_phd():
    d = pe.extract_degree("PhD candidate in Machine Learning, University of Belgrade (expected 2027).")
    assert d["v"] == "phd" and d["in_progress"] is True and d["highest_completed"] is None


def test_doctoral_researcher_and_doctoral_student_are_also_recognised_as_the_phd_bucket():
    """T36e: PM's own explicit real-English phrasing this project had no noun for at all until now."""
    assert pe.extract_degree("Doctoral researcher at KTH.")["v"] == "phd"
    assert pe.extract_degree("Doctoral student, Faculty of Sciences.")["v"] == "phd"


def test_ma_and_meng_are_recognised_as_the_master_level_bucket():
    assert pe.extract_degree("MA in Linguistics, 2019.")["v"] == "msc"
    assert pe.extract_degree("M.Eng in Mechanical Engineering, 2020.")["v"] == "msc"


def test_a_completed_degree_and_a_separately_stated_in_progress_one_are_both_captured():
    """PM's own real golden CV shape: 'PhD candidate in Machine Learning... (expected 2027). MSc in Computer
    Science.' - the in-progress PhD is the primary (more advanced) value, with the separately-stated,
    already-completed MSc kept alongside it, not lost."""
    d = pe.extract_degree("PhD candidate in Machine Learning (expected 2027). MSc in Computer Science.")
    assert d["v"] == "phd" and d["in_progress"] is True
    assert d["highest_completed"] == {"v": "msc", "raw": "MSc"}


def test_a_completed_degree_mentioned_right_before_an_in_progress_ones_own_expected_year_is_never_contaminated():
    """Found live building this exact fix: a fixed-width lookback window (not sentence-scoped) let the
    PhD's own 'expected 2027' wrongly mark the VERY NEXT sentence's separately-stated, already-completed MSc
    as if it were ALSO still in progress, losing it entirely (a real regression this test locks in)."""
    d = pe.extract_degree("PhD candidate (expected 2027). MSc in Physics.")
    assert d["highest_completed"] == {"v": "msc", "raw": "MSc"}


def test_seeking_a_postdoctoral_position_is_never_mistaken_for_an_attained_or_in_progress_postdoc():
    """T36e (packet 021), PM's own real golden CV: 'Seeking full-time PhD or postdoctoral research
    positions...' - a stated PREFERENCE/GOAL, never a credential the applicant holds or is enrolled in.
    Found live: this was previously extracted as an ATTAINED postdoc, since the bare word 'postdoctoral'
    appearing anywhere in the CV was enough with no regard for the 'seeking' context in front of it."""
    assert pe.extract_degree("Seeking a postdoctoral research position in AI.") is None
    assert pe.extract_degree("Looking for a PhD position in computer science.") is None


def test_a_real_attained_degree_elsewhere_still_wins_over_an_unrelated_aspirational_mention():
    d = pe.extract_degree("MSc in Physics, 2020. Seeking a postdoctoral research position.")
    assert d["v"] == "msc" and d["in_progress"] is False


def test_pms_own_real_golden_cvs_produce_exactly_the_degree_shape_pm_named():
    """PM's own explicit T36e acceptance: 'ML golden -> doctoral in progress + completed master; mech golden
    -> doctoral in progress'."""
    from pathlib import Path
    from ajp.applicants.profile_extract import parse_cv_and_extract_profile
    fixtures = Path(__file__).parent / "cv_fixtures"
    _, ml_profile = parse_cv_and_extract_profile(fixtures / "pm_real_ml_cv.docx")
    assert ml_profile["degree"]["v"] == "phd" and ml_profile["degree"]["in_progress"] is True
    assert ml_profile["degree"]["highest_completed"]["v"] == "msc"
    _, mech_profile = parse_cv_and_extract_profile(fixtures / "pm_real_mech_eng_cv.pdf")
    assert mech_profile["degree"]["v"] == "phd" and mech_profile["degree"]["in_progress"] is True


def test_skills_are_extracted_only_from_an_explicit_skills_section():
    assert pe.extract_skills(CV) == ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"]


def test_no_skills_section_returns_an_empty_list_never_guessed_from_prose():
    assert pe.extract_skills("I am skilled in many things, including cooking.") == []


def test_the_skills_section_never_swallows_the_next_unrelated_section():
    """Found live building a PDF fixture: a document with no blank-line paragraph break after the skills
    list (PDF text extraction does not preserve one the way DOCX's own paragraph structure does) must still
    stop at the next real section header, not swallow the rest of the whole document as 'skills'."""
    text = "Skills:\nPython, SQL\nLanguages:\nEnglish: fluent\nExperience:\n5 years in industry"
    assert pe.extract_skills(text) == ["Python", "SQL"]


def test_languages_and_their_stated_levels_are_extracted():
    langs = {l["lang"]: l["level"] for l in pe.extract_languages(CV)}
    assert langs == {"English": "fluent", "Serbian": "native", "German": "basic"}


def test_a_language_named_with_no_level_is_kept_with_a_null_level_not_dropped():
    langs = pe.extract_languages("Languages:\nFrench\n")
    assert langs == [{"lang": "French", "level": None}]


def test_no_languages_section_at_all_returns_an_empty_list():
    assert pe.extract_languages("Nothing about languages here.") == []


def test_desired_countries_are_resolved_via_a_real_pycountry_lookup():
    d = pe.extract_desired_locations(CV)
    assert set(d["desired_countries"]) == {"DE", "NL", "RS"}
    assert d["desired_cities"] == []


def test_a_generic_word_like_preferred_never_false_triggers_the_location_detector():
    """Found live: 'Full-time preferred.' matched the bare word 'preferred' before this was fixed to require
    a real location noun ('preferred location/country/city') right after it."""
    d = pe.extract_desired_locations("Full-time preferred. No location preference stated anywhere.")
    assert d == {"desired_countries": [], "desired_cities": []}


def test_an_unresolvable_town_name_is_kept_as_a_city_candidate_not_discarded():
    d = pe.extract_desired_locations("Desired location: Springfield")
    assert d["desired_cities"] == ["Springfield"] and d["desired_countries"] == []


def test_no_desired_location_statement_at_all_returns_empty_lists():
    assert pe.extract_desired_locations("A CV with no location preference stated.") == \
        {"desired_countries": [], "desired_cities": []}


def test_work_prefs_extracts_remote_ft_pt_years_salary_and_visa_note():
    prefs = pe.extract_work_prefs(CV)
    assert prefs["remote_ok"] is True
    assert prefs["ft_pt"] == "full"
    assert prefs["years_experience"] == 5
    assert prefs["min_salary_eur_year"] == 45000.0
    assert prefs["foreigner_visa_note"] == "Serbian citizen"


def test_a_monthly_salary_figure_is_annualized():
    prefs = pe.extract_work_prefs("Expected salary: EUR 3000 per month")
    assert prefs["min_salary_eur_year"] == 36000.0


def test_no_work_prefs_stated_at_all_are_all_none():
    prefs = pe.extract_work_prefs("A CV with no work preferences stated at all.")
    assert prefs == {"remote_ok": None, "ft_pt": None, "years_experience": None,
                     "min_salary_eur_year": None, "foreigner_visa_note": None}


def test_extract_profile_combines_every_field():
    profile = pe.extract_profile(CV)
    assert set(profile) >= {"disciplines", "degree", "skills", "languages", "desired_countries",
                            "desired_cities", "remote_ok", "ft_pt", "years_experience",
                            "min_salary_eur_year", "foreigner_visa_note"}
    assert profile["degree"]["v"] == "postdoc"
    assert profile["skills"] == ["Python", "MATLAB", "machine learning", "data analysis", "LaTeX"]


def test_an_empty_cv_extracts_an_honest_all_empty_profile_never_fabricated():
    profile = pe.extract_profile("")
    assert profile["disciplines"] == [] and profile["skills"] == [] and profile["languages"] == []
    assert profile["degree"] is None and profile["remote_ok"] is None
