"""T25/T33/T36 acceptance: 'matching golden cases incl foreigner-eligible + remote + zero-match', extended
for T36's own real relevance rework. The scoring engine is pure, deterministic Python (no DB access) - every
case here is a plain dict-in, dict-out unit test, with expected values verified by actually running the real
code (never hand-computed) per this project's own live-verification discipline."""
from ajp.applicants.matching import MATCH_WEIGHTS, score_listing


def test_weights_are_committed_and_sum_to_100():
    """The exact numbers the methodology page renders - a drift here would silently desync the docs from
    the real scoring behaviour."""
    assert sum(MATCH_WEIGHTS.values()) == 100


PROFILE = {
    "disciplines": ["Natural sciences"], "discipline_terms": ["physics"],
    "degree": {"v": "phd", "raw": "PhD"},
    "skills": ["Python", "quantum computing"],
    "languages": [{"lang": "english", "level": "fluent"}],
    "desired_countries": ["DE", "NL"], "remote_ok": True, "ft_pt": "full",
    "min_salary_eur_year": 40000,
}


def test_a_listing_matching_every_criterion_scores_near_100():
    """T37 (packet 022, R1) recalibrated skill_relevance's own saturation curve - 2 genuine TITLE-zone hits
    ("quantum computing" x2=2.0, "physics" x1=1.0, both at the title zone weight 1.0) plus 1 requirements-zone
    hit ("python" x1=1.0 at 0.6) already clears the new 2.5 saturation point (3.6 raw / 2.5 = 1.44, capped at
    1.0) - a genuinely strong multi-zone match now correctly reaches the FULL 1.0 fraction, not merely 'over
    0.8', so every one of the 7 criteria is a clean positive 1.0 and the overall score is a real, verified
    exact 100 (re-verified directly against this exact fixture, not hand-computed)."""
    listing = {
        "title_raw": "Postdoc in Quantum Computing and Physics", "requirements_raw": "experience with Python required",
        "raw_text": "quantum computing physics research role using Python",
        "disciplines_euroscivoc": [], "degree_required": "phd", "country_iso2": "DE",
        "location_class": "located", "ft_pt": "full", "language": [{"lang": "en", "required": True}],
        "salary_eur_year_min": 45000, "salary_eur_year_max": 55000,
    }
    result = score_listing(PROFILE, listing)
    assert result["score"] == 100.0
    assert result["counts"] == {"positive": 7, "negative": 0, "unknown": 0}
    assert result["coverage"] == 1.0
    assert result["factors"]["skill_relevance"]["fraction"] == 1.0


def test_a_listing_actively_mismatching_on_everything_it_states_scores_below_the_neutral_midpoint():
    """T33's own neutral-unknown ruling: a listing that genuinely MISMATCHES on every criterion it actually
    states real data for scores below 50 (the neutral midpoint every criterion would sit at if it were
    simply unknown) - never a flat 0, a real mismatch is still real, evaluated information. T36b (packet
    021): discipline and skill_relevance are correctly 'unknown' here, not 'negative' - a bland 'restaurant
    kitchen role' states no discernible topic at all, so failing to overlap the applicant's own CS/physics
    terms is an ABSENCE of evidence, never positive evidence of an actual mismatch."""
    listing = {
        "title_raw": "Chef position", "raw_text": "restaurant kitchen role",
        "disciplines_euroscivoc": [], "degree_required": "none", "country_iso2": "FR",
        "location_class": "located", "ft_pt": "part", "language": [{"lang": "fr", "required": True}],
        "salary_eur_year_min": 20000, "salary_eur_year_max": 25000,
    }
    result = score_listing(PROFILE, listing)
    assert result["score"] == 22.5
    assert result["counts"] == {"positive": 1, "negative": 4, "unknown": 2}
    assert result["factors"]["discipline"]["state"] == "unknown"
    assert result["factors"]["skill_relevance"]["state"] == "unknown"
    assert result["factors"]["location"]["state"] == "negative"
    assert result["factors"]["degree_level"]["state"] == "positive"    # phd > the ad's stated 'none'


def test_a_listing_with_no_comparable_data_at_all_scores_the_pure_neutral_midpoint():
    """T36b (packet 021, PM ruling - the correctness floor): a listing that states nothing comparable at all
    ('Some role', empty body) leaves EVERY criterion 'unknown' now, including skill_relevance and discipline
    - failing to find a keyword match in text that has no real topical content at all is not evidence of a
    mismatch either. This supersedes T33's own earlier version of this test, where the one 'evaluable'
    discipline criterion was a false-positive mismatch under the OLD design (no real listing-side discipline
    signal existed at all before T36a - the old code still called it a mismatch anyway)."""
    listing = {"title_raw": "Some role", "raw_text": ""}
    result = score_listing(PROFILE, listing)
    assert result["score"] == 50.0
    assert result["counts"] == {"positive": 0, "negative": 0, "unknown": len(MATCH_WEIGHTS)}
    assert result["coverage"] == 0.0
    assert all(f["state"] == "unknown" for f in result["factors"].values())


def test_a_fully_remote_listing_matches_a_remote_ok_applicant_even_with_no_desired_countries_stated():
    profile = {"remote_ok": True}
    listing = {"location_class": "remote", "country_iso2": None}
    result = score_listing(profile, listing)
    assert result["factors"]["location"]["fraction"] == 1.0
    assert "remote" in result["factors"]["location"]["reason"]


def test_zero_stated_profile_data_leaves_every_criterion_unknown_and_a_neutral_score():
    """T33 (packet 018, PM ruling): an applicant profile with nothing extracted at all is not the same as a
    genuine mismatch - every criterion is 'unknown' (there is nothing on the applicant's side to compare),
    so the score is the pure neutral midpoint (50), never a 0 that would misrepresent 'we don't know' as
    'this is a bad fit'."""
    empty_profile = {"disciplines": [], "degree": None, "languages": [], "desired_countries": [],
                     "remote_ok": None, "ft_pt": None, "min_salary_eur_year": None, "skills": []}
    listing = {
        "title_raw": "Postdoc in Quantum Physics", "raw_text": "physics research role",
        "degree_required": "phd", "country_iso2": "DE", "location_class": "located", "ft_pt": "full",
        "language": [{"lang": "en", "required": True}], "salary_eur_year_max": 55000,
    }
    result = score_listing(empty_profile, listing)
    assert result["score"] == 50.0
    assert result["counts"] == {"positive": 0, "negative": 0, "unknown": len(MATCH_WEIGHTS)}
    assert result["coverage"] == 0.0
    assert all(f["state"] == "unknown" for f in result["factors"].values())


def test_a_foreigner_eligible_relevant_case_uses_the_applicants_desired_country_not_citizenship():
    """This engine's own location criterion is about desired countries/remote policy, not visa eligibility
    (a listing's own foreigner_eligible field is a separate, listing-side fact the UI shows directly
    alongside the match - not folded into this score, since an applicant's own eligibility is self-reported
    free text, never a field this engine could verify)."""
    profile = {**PROFILE, "desired_countries": ["RS"]}
    listing = {"title_raw": "Postdoc", "raw_text": "role", "country_iso2": "RS", "location_class": "located"}
    result = score_listing(profile, listing)
    assert result["factors"]["location"]["fraction"] == 1.0


def test_salary_below_the_applicants_stated_minimum_scores_that_criterion_zero():
    listing = {"salary_eur_year_max": 30000}
    result = score_listing(PROFILE, listing)
    assert result["factors"]["salary"]["fraction"] == 0.0


def test_salary_falls_back_to_min_when_the_listing_states_no_max():
    listing = {"salary_eur_year_min": 50000}
    result = score_listing(PROFILE, listing)
    assert result["factors"]["salary"]["fraction"] == 1.0


def test_no_salary_stated_by_either_side_is_unknown_not_scored_as_a_mismatch():
    result = score_listing({"min_salary_eur_year": None}, {"salary_eur_year_max": 50000})
    assert result["factors"]["salary"]["state"] == "unknown"
    result2 = score_listing({"min_salary_eur_year": 40000}, {})
    assert result2["factors"]["salary"]["state"] == "unknown"


# ---- T36a/b/c/d (packet 021): PM's own named real-world regression - a real ML/CS candidate must rank a
# genuinely relevant AI/CS listing FAR above an unrelated one bucketed in the SAME broad discipline field
# (the actual "tie farm" bug: a flat 6-bucket discipline match alone cannot tell 'robot learning' apart from
# 'plant pathogen interactions', both landing under the same broad 'Natural sciences' umbrella). ------------
ML_CS_PROFILE = {
    "disciplines": ["Natural sciences"],
    "discipline_terms": ["machine learning", "artificial intelligence", "computer science", "data science"],
    "degree": {"v": "phd", "raw": "PhD"},
    "skills": ["Python", "PyTorch", "NLP", "data science", "statistics", "Linux", "Docker"],
    "languages": [{"lang": "English", "level": "C1"}, {"lang": "Serbian", "level": "native"}],
    "desired_countries": ["DE", "SE", "DK", "NL"], "remote_ok": True, "ft_pt": "full",
    "min_salary_eur_year": 38000,
}


def test_a_real_relevant_ai_listing_outranks_an_unrelated_biorecovery_postdoc_for_the_ml_cs_candidate():
    """PM's own named real defect: a KTH-style 'Postdoc in Robot Learning for Manipulation' and an unrelated
    'Postdoc in Bioresource Recovery' were indistinguishable at 100 under the old (T25) design, and merely
    tied at the same ~87.5 under T33's own neutral-unknown fix alone (both landing in the same broad
    'Natural sciences' discipline bucket, discipline being the only real topical signal that existed before
    T36). T36a (listing-side discipline inference) + T36c (the new skill_relevance factor) together now
    separate them by nearly 80 points."""
    kth_ai = {
        "title_raw": "Postdoc in Robot Learning for Manipulation",
        "raw_text": "machine learning and computer science research using Python",
        "degree_required": "phd", "country_iso2": "SE", "location_class": "located", "ft_pt": "full",
        "language": [{"lang": "english", "required": True}], "salary_eur_year_max": 45000,
    }
    # A genuinely different top-level field (Agricultural sciences, not Natural sciences) - the real, live
    # PM example (a plant-pathology/biorecovery role) that this project's own flat discipline scheme WOULD
    # still have bucketed alongside a biology-flavoured 'Natural sciences' listing had it mentioned biology
    # by name; this fixture is deliberately titled/worded to land in a clearly distinct real bucket instead.
    biorecovery = {
        "title_raw": "Postdoc in Bioresource Recovery",
        "raw_text": "agriculture and forestry resource management research",
        "degree_required": "phd", "country_iso2": "IT", "location_class": "located", "ft_pt": "full",
        "language": [{"lang": "italian", "required": True}], "salary_eur_year_max": 32000,
    }
    kth_result = score_listing(ML_CS_PROFILE, kth_ai)
    bio_result = score_listing(ML_CS_PROFILE, biorecovery)
    # T36c/T36f (found live building the mechanical-CV ranking gate): skill_relevance is now scoped to the
    # SPECIFIC discipline keyword(s) the CV's own text actually states, never the whole discipline bucket's
    # sibling keyword list - 'robot learning' is not lexically identical to 'machine learning', so this was
    # a real, honest, slightly LOWER score than an earlier (buggy) version of this factor gave, not a
    # regression: the CV genuinely never states 'robot'/'robotics' anywhere, and a keyword-only factor
    # cannot claim credit for a synonym/related-concept overlap it has no real basis for (D6).
    # T38 (packet 022) rebalanced discipline 20->15 / skill_relevance 15->20 - 82.4 at that point.
    # T40 (packet 023, PM ruling OPTION_B): PM's own frozen C1_ML_AI_CORE cluster explicitly links 'robot
    # learning' <-> 'machine learning'/'artificial intelligence' - this is now a genuine TITLE-zone cluster
    # hit (this fixture's own title literally IS "Postdoc in Robot Learning for Manipulation") instead of
    # D6 ever claiming an un-stated synonym - PM's OWN curated table is the explicit, reviewed authority for
    # this specific equivalence, not a guess this module invented. Real, re-verified score: 98.4 (both roots
    # 'machine learning' and 'artificial intelligence' are present in this profile's own discipline_terms
    # and both belong to C1 - the tie-break picks the alphabetically-earlier root, 'artificial intelligence',
    # for the reason text's own tilde suffix, deterministically).
    assert kth_result["score"] == 98.4
    assert bio_result["score"] == 15.0
    assert kth_result["score"] > bio_result["score"]
    assert kth_result["score"] >= 80.0
    assert bio_result["score"] < 50.0
    assert "robot learning (title; ~ artificial intelligence)" in kth_result["factors"]["skill_relevance"]["reason"]


def test_a_mostly_sparse_listing_population_never_mass_inflates_to_near_100_scores():
    """PM's own named real defect: 2,381 of 3,948 open listings scored a perfect 100 for one real CV under
    the T25 design, because a sparse listing rescaled its tiny evaluable sliver to 100%. A synthetic stand-in
    population (2 genuinely well-matched listings, 48 sparse ones stating almost nothing) must show the
    same real *proportion* of high scorers and the same median-convergence bound T36d's own gate documents -
    asserted here directly rather than only described in prose."""
    rich_listings = [
        {"title_raw": "PhD in Machine Learning", "raw_text": "computer science and data science research",
         "degree_required": "phd", "country_iso2": "DE", "location_class": "located", "ft_pt": "full",
         "language": [{"lang": "english", "required": True}], "salary_eur_year_max": 45000},
        {"title_raw": "Postdoc in AI", "raw_text": "artificial intelligence and computer science",
         "degree_required": "phd", "country_iso2": "NL", "location_class": "located", "ft_pt": "full",
         "language": [{"lang": "english", "required": True}], "salary_eur_year_max": 42000},
    ]
    sparse_listings = [{"title_raw": f"Some role {i}", "raw_text": ""} for i in range(48)]
    all_scores = [score_listing(ML_CS_PROFILE, lst)["score"] for lst in rich_listings + sparse_listings]
    high_scorers = [s for s in all_scores if s >= 90.0]
    assert len(high_scorers) / len(all_scores) < 0.05          # PM's own documented bound
    median = sorted(all_scores)[len(all_scores) // 2]
    assert 40.0 <= median <= 65.0                              # T36d's own documented convergence bound
    assert all(0.0 <= s <= 100.0 for s in all_scores)          # sanity: no score ever out of range


def test_no_negative_factor_is_ever_emitted_on_a_listing_with_genuinely_no_evaluable_data():
    """T36d gate #4 (packet 021): across every one of the 7 criteria, a listing that states essentially
    nothing must never show a single 'negative' state - only 'unknown' or (for whichever criteria the
    listing DOES state something real for) 'positive'/'negative' backed by real evidence."""
    listing = {"title_raw": "Some role", "raw_text": ""}
    result = score_listing(ML_CS_PROFILE, listing)
    assert result["counts"]["negative"] == 0
    assert all(f["state"] != "negative" for f in result["factors"].values())


def test_a_phrase_stated_under_two_castings_in_skills_and_discipline_terms_is_deterministic():
    """T39 (packet 022), found live doing a before/after byte-identical comparison for the persistence
    switch: PM's own real ML CV states 'NLP' as a skill and separately matches 'nlp' as a discipline term
    (from elsewhere in the same CV) - deduping through a plain Python set let EITHER casing 'win' in the
    displayed reason text depending on hash-randomized set iteration order, observed differing between two
    separate server process restarts scoring the IDENTICAL request. Both castings must now always produce
    the exact same casing (whichever string sorts first; uppercase ASCII letters sort before lowercase, so
    'NLP' here) regardless of which field order they are read from - the POINT is determinism, not which
    specific casing wins."""
    listing = {"title_raw": "Research Role", "requirements_raw": "NLP required", "raw_text": ""}
    profile_a = {"skills": ["NLP"], "discipline_terms": ["nlp"], "disciplines": ["Natural sciences"]}
    profile_b = {"skills": [], "discipline_terms": ["nlp", "NLP"], "disciplines": ["Natural sciences"]}
    reason_a = score_listing(profile_a, listing)["factors"]["skill_relevance"]["reason"]
    reason_b = score_listing(profile_b, listing)["factors"]["skill_relevance"]["reason"]
    assert reason_a == reason_b == "matches: NLP (requirements)"


# ---- T39 (packet 022): persisted listing-side inference, read instead of recomputed when present --------
def test_a_persisted_inferred_disciplines_column_is_used_in_place_of_the_live_regex_scan():
    """T39 (packet 022): a listing whose own `inferred_disciplines` was precomputed at ingest must score
    IDENTICALLY to the same listing with the column NULL (the on-the-fly fallback) - this is a pure perf
    switch, never a new source of truth. The listing's own title here is DELIBERATELY misleading (it alone
    would infer 'Social sciences' live) so a test relying on the STORED value (a different, 'Natural
    sciences' bucket here) proves the persisted column, not the live title scan, is what actually drove it."""
    profile = {"disciplines": ["Natural sciences"], "discipline_terms": ["machine learning"]}
    base_listing = {"title_raw": "Research Economist", "raw_text": "", "requirements_raw": ""}
    persisted = {**base_listing, "inferred_disciplines": {"buckets": ["Natural sciences"], "zone": "title"}}
    result = score_listing(profile, persisted)
    assert result["factors"]["discipline"]["state"] == "positive"
    assert "Natural sciences" in result["factors"]["discipline"]["reason"]


def test_a_null_inferred_disciplines_column_falls_back_to_the_live_scan_unchanged():
    profile = {"disciplines": ["Natural sciences"]}
    listing = {"title_raw": "Postdoc in Machine Learning", "raw_text": "", "requirements_raw": ""}
    with_column = {**listing, "inferred_disciplines": None}
    without_column = dict(listing)
    assert score_listing(profile, with_column) == score_listing(profile, without_column)


# ---- T37 (packet 022): best-evidence saturation curve, generic-term stoplist, zone-tagged reasons -------
def test_a_single_exact_title_hit_alone_clears_the_080_floor():
    """R1 (packet 022): PM's own explicit calibration target - ONE exact multi-word applicant phrase found
    in the listing's own TITLE must alone be worth >= 0.80, never merely the ~0.5 the old (pre-T37)
    calibration gave it."""
    profile = {"skills": ["machine learning"]}
    listing = {"title_raw": "Postdoc in Machine Learning", "raw_text": "", "requirements_raw": ""}
    result = score_listing(profile, listing)
    assert result["factors"]["skill_relevance"]["fraction"] >= 0.80
    assert result["factors"]["skill_relevance"]["state"] == "positive"
    assert "machine learning (title)" in result["factors"]["skill_relevance"]["reason"]


def test_two_title_zone_hits_saturate_at_the_full_1_0():
    """R1 (packet 022): '2+ strong title/requirements hits saturate at 1.0'."""
    profile = {"skills": ["machine learning", "computer vision"]}
    listing = {"title_raw": "Machine Learning and Computer Vision Postdoc", "raw_text": "", "requirements_raw": ""}
    result = score_listing(profile, listing)
    assert result["factors"]["skill_relevance"]["fraction"] == 1.0


def test_body_only_evidence_is_capped_well_below_the_title_hit_floor():
    """R1 (packet 022): the body-stuffing cap - an unrelated listing's own long body text can incidentally
    contain a real applicant phrase, but body-only evidence must never approach what a single real title hit
    is worth on its own (0.80) - the cap's own proportion (0.4 of saturation, at the body zone weight 0.3)
    is architecturally independent of the saturation point itself (see matching.py's own
    _BODY_ONLY_CAP_FRACTION comment for the algebra). This fixture's own title is deliberately NOT PM's real
    KTH 'Robot Learning for Manipulation' title any more (T40, packet 023, moved that exact real case to
    0.92/98.4 in test_a_real_relevant_ai_listing_outranks... above, via its own cluster-sibling title hit) -
    using a genuinely cluster-unreachable title ('Computer Vision Applications') keeps THIS test's own
    body-only premise honest."""
    profile = {"skills": ["machine learning"]}
    listing = {"title_raw": "Postdoc in Computer Vision Applications", "requirements_raw": None,
              "raw_text": "strong background in robotics and machine learning is required"}
    result = score_listing(profile, listing)
    assert result["factors"]["skill_relevance"]["fraction"] == 0.12
    assert result["factors"]["skill_relevance"]["fraction"] < 0.80


def test_generic_stoplisted_terms_never_count_as_a_skill_relevance_match():
    """R2 (packet 022): PM's own real named defect - a mechanical-engineering CV's own bare, generic
    'engineering' mention (from an unrelated sentence, e.g. 'seeking positions in engineering') must never
    let it match an unrelated electrical/chemical/civil listing's own bare 'engineering' mention, even though
    both literally share that one word. Defensive: filtered here even though this profile is hand-built as
    if it were an UNFILTERED, pre-T37 profile (an old persisted applicant row) - matching.py's own
    _compiled_relevance_pattern must re-apply the stoplist itself, never only trust the extractor."""
    profile = {"skills": [], "discipline_terms": ["engineering"]}
    listing = {"title_raw": "Electrical Engineering or Automation Technology", "raw_text": "", "requirements_raw": ""}
    result = score_listing(profile, listing)
    # nothing left to search for at all once 'engineering' is stoplisted out - genuinely nothing comparable.
    assert result["factors"]["skill_relevance"]["state"] == "unknown"


def test_generic_stoplisted_terms_are_excluded_at_extraction_time_too():
    """R2 (packet 022): PM's own exact real defect text - the mechanical CV's own Preferences sentence
    states 'Seeking full-time PhD research positions in engineering', a bare generic mention, while its
    Education section separately states 'Mechanical Engineering' (specific) - only the specific phrase must
    survive into discipline_terms."""
    from ajp.applicants.profile_extract import extract_matched_discipline_terms
    text = ("PhD candidate in Mechanical Engineering. Seeking full-time PhD research positions in "
           "engineering.")
    terms = extract_matched_discipline_terms(text)
    assert "mechanical engineering" in terms
    assert "engineering" not in terms


def test_computer_science_and_computer_engineering_survive_the_stoplist_bare_computer_does_not():
    """R2 (packet 022): PM's own explicit instruction - bare 'computer' is stoplisted, but 'computer
    science'/'computer engineering' (a different string entirely) are deliberately NOT."""
    from ajp.applicants.profile_extract import extract_matched_discipline_terms
    terms = extract_matched_discipline_terms("MSc in Computer Science. Worked as a computer technician.")
    assert "computer science" in terms
    assert "computer" not in terms


def test_skill_relevance_reason_names_the_matched_phrase_and_its_zone():
    """R5 (packet 022): 'reasons must list the actual matched phrases and the zone'. Title names a real,
    inferable topic ('Computer Science') so skill_relevance is genuinely evaluable (not 'unknown') - the
    actual python match itself comes from the separate requirements zone."""
    profile = {"skills": ["python"]}
    listing = {"title_raw": "Computer Science Research Role", "requirements_raw": "Python required", "raw_text": ""}
    result = score_listing(profile, listing)
    assert "python (requirements)" in result["factors"]["skill_relevance"]["reason"]


# ---- T40 (packet 023, PM ruling OPTION_B): frozen skill-equivalence clusters ------------------------------
# Profiles shaped like the two REAL golden CVs (the exact discipline_terms/skills those real files actually
# extract to, confirmed live against the running API before writing these) - not the broader synthetic
# ML_CS_PROFILE above, so the tilde-reason root is unambiguous (the real ML CV's own discipline_terms never
# include "artificial intelligence" at all, only "machine learning" - see the real CV's own text).
_ML_GOLDEN_SHAPED_PROFILE = {
    "disciplines": ["Natural sciences"],
    "discipline_terms": ["machine learning", "computer science", "nlp", "data science"],
    "skills": ["Python", "PyTorch", "NLP", "data science", "statistics", "Linux", "Docker"],
    "desired_countries": ["DE", "SE", "DK", "NL"],
}
_MECH_GOLDEN_SHAPED_PROFILE = {
    "disciplines": ["Engineering and technology"],
    "discipline_terms": ["mechanical engineering", "solidworks", "ansys"],
    "skills": ["SolidWorks", "CAD", "FEM", "ANSYS", "Python", "materials testing"],
    "desired_countries": ["AT", "DE"],
}


def test_t40_kth_shaped_title_gets_a_cluster_hit_naming_machine_learning():
    """T40 regression #1 (packet 023): the real KTH shape - PM's own C1_ML_AI_CORE links 'robot learning' to
    'machine learning', which the real ML golden CV's own discipline_terms literally states."""
    listing = {"title_raw": "Postdoc in Robot Learning for Manipulation",
              "raw_text": "machine learning and computer science research", "requirements_raw": ""}
    result = score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["fraction"] >= 0.80
    assert "robot learning (title; ~ machine learning)" in result["reason"]


def test_t40_nlp_professor_title_gets_a_cluster_hit_naming_nlp():
    """T40 regression #2: C2_NLP links the full phrase to the 'NLP' abbreviation the real CV states."""
    listing = {"title_raw": "Assistant Professor of Natural Language Processing",
              "raw_text": "", "requirements_raw": ""}
    result = score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["fraction"] >= 0.80
    assert "natural language processing (title; ~ NLP)" in result["reason"]


def test_t40_cad_postdoc_title_gets_a_cluster_hit_naming_cad():
    """T40 regression #3: C4_CAD_DESIGN_TOOLS links the spelled-out phrase to the 'CAD' abbreviation the
    real mechanical CV states - the real SUSMAT defect."""
    listing = {"title_raw": "Postdoc Position in Computer-Aided Design", "raw_text": "", "requirements_raw": ""}
    result = score_listing(_MECH_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["fraction"] >= 0.80
    assert "computer-aided design (title; ~ CAD)" in result["reason"]


def test_t40_electrical_engineering_gains_no_cluster_credit_from_the_mechanical_cv():
    """T40 regression #4: PM's own explicit exclusion - electrical engineering is NOT in C8 with
    'mechanical engineering' (the T36c cross-pollution lesson, re-confirmed for the cluster mechanism)."""
    listing = {"title_raw": "Electrical Engineering and Automation Technology", "raw_text": "", "requirements_raw": ""}
    result = score_listing(_MECH_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["state"] == "negative"
    assert result["fraction"] == 0.0


def test_t40_chemical_engineering_gains_no_cluster_credit_from_the_mechanical_cv():
    """T40 regression #5."""
    listing = {"title_raw": "Assistant Professor in Chemical Engineering - Process Design",
              "raw_text": "", "requirements_raw": ""}
    result = score_listing(_MECH_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["state"] == "negative"
    assert result["fraction"] == 0.0


def test_t40_a_body_only_cluster_hit_still_stays_at_the_012_cap():
    """T40 regression #6: 'statistical analysis' (C3, a sibling of the ML CV's own 'data science') found
    ONLY in an unrelated biology ad's own body text must still be capped exactly like a literal body-only
    hit - the cluster mechanism adds candidate PHRASES, never a second, looser cap."""
    listing = {"title_raw": "Postdoc in Molecular Biology",
              "raw_text": "statistical analysis experience required", "requirements_raw": ""}
    result = score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing)["factors"]["skill_relevance"]
    assert result["fraction"] <= 0.12
    assert "~ data science" in result["reason"]


def test_t40_bare_ai_never_matches_a_french_jai_sentence():
    """T40 regression #7: bare 'AI' is deliberately NOT a cluster member (PM's own exclusion - French "j'ai"
    and other false positives); a French sentence full of "j'ai" must never produce a C1 hit, or any hit at
    all here - there is no real, inferable topic on either side for this fixture either, so BOTH discipline
    and skill_relevance land on the SAME honest 'unknown' (absence of evidence), never a false 'ai' match."""
    listing = {"title_raw": "Poste de chercheur - j'ai besoin d'un candidat",
              "raw_text": "j'ai une expertise en recherche et en gestion de projet", "requirements_raw": ""}
    result = score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing)
    assert result["factors"]["discipline"]["state"] == "unknown"
    assert result["factors"]["skill_relevance"]["state"] == "unknown"


def test_t40_stoplist_still_wins_even_after_cluster_expansion():
    """T40 regression #8: a profile whose only term is a bare stoplisted generic yields NO candidate phrase
    at all (pattern None -> 'unknown'), regardless of cluster membership - the stoplist is applied to the
    ROOT before cluster expansion is even considered, so a stoplisted root can never contribute its own
    cluster siblings either."""
    engineering_only = {"skills": [], "discipline_terms": ["engineering"]}
    listing = {"title_raw": "Electrical Engineering and Automation Technology", "raw_text": "", "requirements_raw": ""}
    assert score_listing(engineering_only, listing)["factors"]["skill_relevance"]["state"] == "unknown"
    computer_only = {"skills": ["computer"], "discipline_terms": []}
    listing2 = {"title_raw": "Computer Science Research Role", "raw_text": "", "requirements_raw": ""}
    assert score_listing(computer_only, listing2)["factors"]["skill_relevance"]["state"] == "unknown"


def test_t40_cluster_symmetry_links_fire_in_both_directions():
    """T40 regression #9 (packet 023's own 'Symmetry' item) - verified live, both directions DO get a real,
    positive cluster-linked hit (the link fires either way, which is what this test actually proves), but
    NOT the literal same fraction PM's own wording also asked for: per the packet's own explicit 'credit'
    rule ("phrase weight is determined by the SURFACE phrase... NOT by the applicant term"), a profile
    stating the single-token 'NLP' that matches a listing's multi-word 'Natural Language Processing' title
    earns the multi-word weight (fraction 0.80), while the REVERSE (profile states the multi-word phrase,
    listing states the single-token 'NLP') only earns the single-token weight (fraction 0.40) - the two
    directions' MATCHED SURFACE FORMS have different word counts, so they cannot both earn the same credit
    under a surface-driven weight rule. Reported to PM as a found tension between the 'credit' rule and the
    'symmetry' test wording, both in packet 023; resolved here by honoring the explicit, repeated 'credit'
    rule and testing what is actually true (bidirectional linkage) rather than forcing equal numbers."""
    a_profile = {"skills": ["NLP"], "disciplines": ["Natural sciences"]}
    a_listing = {"title_raw": "Natural Language Processing Postdoc", "raw_text": "", "requirements_raw": ""}
    a_result = score_listing(a_profile, a_listing)["factors"]["skill_relevance"]
    b_profile = {"skills": ["natural language processing"], "disciplines": ["Natural sciences"]}
    b_listing = {"title_raw": "NLP Postdoc", "raw_text": "", "requirements_raw": ""}
    b_result = score_listing(b_profile, b_listing)["factors"]["skill_relevance"]
    assert a_result["state"] == "positive" and b_result["state"] == "positive"
    assert "~ NLP" in a_result["reason"] and "~ natural language processing" in b_result["reason"]
    assert a_result["fraction"] == 0.80 and b_result["fraction"] == 0.40


def test_t40_determinism_two_identical_computes_give_byte_identical_output():
    """T40 regression #10 (unit-level; the full live-DB byte-identical check runs separately against the
    real ~/.ajp DB, the same methodology T39's own before/after comparison used)."""
    listing = {"title_raw": "Postdoc in Robot Learning for Manipulation",
              "raw_text": "machine learning and computer science research", "requirements_raw": ""}
    assert score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing) == score_listing(_ML_GOLDEN_SHAPED_PROFILE, listing)


# ---- T41 (packet 024): Aho-Corasick engine must be byte-identical to the old regex engine -----------------
from ajp.applicants.matching import _score_skill_relevance  # noqa: E402


def _identity_fixtures():
    """Every real, meaningfully-distinct shape this module's own tests have exercised the regex engine
    with so far (T37-T40's own fixtures) - title/requirements/body-zone hits, cluster hits (several
    clusters), negative guards, stoplist, body-only capping, saturating multi-hit, and completely empty/
    topic-less listings. Used by the identity-diff test below, AND is exactly the same battery the live
    before/after comparison against the real ~/.ajp DB (t41_report.md) is built on."""
    return [
        (_ML_GOLDEN_SHAPED_PROFILE, {"title_raw": "Postdoc in Robot Learning for Manipulation",
                                     "raw_text": "machine learning and computer science research", "requirements_raw": ""}),
        (_ML_GOLDEN_SHAPED_PROFILE, {"title_raw": "Assistant Professor of Natural Language Processing",
                                     "raw_text": "", "requirements_raw": ""}),
        (_MECH_GOLDEN_SHAPED_PROFILE, {"title_raw": "Postdoc Position in Computer-Aided Design",
                                       "raw_text": "", "requirements_raw": ""}),
        (_MECH_GOLDEN_SHAPED_PROFILE, {"title_raw": "Electrical Engineering and Automation Technology",
                                       "raw_text": "", "requirements_raw": ""}),
        (_MECH_GOLDEN_SHAPED_PROFILE, {"title_raw": "Assistant Professor in Chemical Engineering - Process Design",
                                       "raw_text": "", "requirements_raw": ""}),
        (_ML_GOLDEN_SHAPED_PROFILE, {"title_raw": "Postdoc in Molecular Biology",
                                     "raw_text": "statistical analysis experience required", "requirements_raw": ""}),
        (_ML_GOLDEN_SHAPED_PROFILE, {"title_raw": "Poste de chercheur - j'ai besoin d'un candidat",
                                     "raw_text": "j'ai une expertise en recherche et en gestion de projet", "requirements_raw": ""}),
        ({"skills": [], "discipline_terms": ["engineering"]},
         {"title_raw": "Electrical Engineering and Automation Technology", "raw_text": "", "requirements_raw": ""}),
        ({"skills": ["NLP"], "disciplines": ["Natural sciences"]},
         {"title_raw": "Natural Language Processing Postdoc", "raw_text": "", "requirements_raw": ""}),
        ({"skills": ["natural language processing"], "disciplines": ["Natural sciences"]},
         {"title_raw": "NLP Postdoc", "raw_text": "", "requirements_raw": ""}),
        (PROFILE, {"title_raw": "Postdoc in Quantum Computing and Physics",
                  "requirements_raw": "experience with Python required",
                  "raw_text": "quantum computing physics research role using Python"}),
        (ML_CS_PROFILE, {"title_raw": "Postdoc in Robot Learning for Manipulation",
                        "raw_text": "machine learning and computer science research using Python",
                        "degree_required": "phd", "country_iso2": "SE"}),
        ({"skills": ["machine learning"]}, {"title_raw": "Postdoc in Computer Vision Applications",
                                            "requirements_raw": None,
                                            "raw_text": "strong background in robotics and machine learning is required"}),
        ({"skills": []}, {"title_raw": "Some role", "raw_text": ""}),
        ({}, {}),
    ]


def test_t41_aho_corasick_and_regex_engines_agree_on_every_fixture_byte_for_byte():
    """T41's own core correctness requirement (packet 024): the regex and Aho-Corasick engines must be
    byte-identical to one another, REGARDLESS of which one is the current default (packet 025's own
    OPTION_A_ACCEPTED ruling keeps "regex" as the default; this test's own job is to prove the two agree,
    not to assert either one's default status) - for every one of this module's own real fixtures:
    fraction, reason string (including tilde roots and zones), and state, with no exceptions."""
    mismatches = []
    for profile, listing in _identity_fixtures():
        regex_result = _score_skill_relevance(profile, listing, engine="regex")
        ac_result = _score_skill_relevance(profile, listing, engine="aho_corasick")
        if regex_result != ac_result:
            mismatches.append((profile, listing, regex_result, ac_result))
    assert not mismatches, f"{len(mismatches)} fixture(s) disagree between engines: {mismatches}"


def test_t41_default_engine_is_regex_aho_corasick_available_but_not_faster():
    """T41 (packet 024): both engines are implemented and proven byte-identical, but real, repeated,
    interleaved measurement found Aho-Corasick does NOT outperform the existing regex engine at this
    project's own real vocabulary scale - "regex" stays the default; see this module's own comment and
    the T41 need_input event for the full measured evidence."""
    from ajp.applicants.matching import SKILL_MATCH_ENGINE
    assert SKILL_MATCH_ENGINE == "regex"


def test_every_factor_carries_its_own_committed_weight_and_a_human_reason():
    listing = {
        "title_raw": "Postdoc in Quantum Physics", "raw_text": "physics research role",
        "degree_required": "phd", "country_iso2": "DE", "location_class": "located", "ft_pt": "full",
        "language": [{"lang": "en", "required": True}], "salary_eur_year_max": 55000,
    }
    result = score_listing(PROFILE, listing)
    for name, factor in result["factors"].items():
        assert factor["weight"] == MATCH_WEIGHTS[name]
        assert isinstance(factor["reason"], str) and factor["reason"]
        assert factor["state"] in ("positive", "negative", "unknown")
