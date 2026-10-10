from datetime import datetime, timezone

import pytest

from ajp.connectors import jobposting

# ---- fixtures: minimal, redacted re-derivations of the real live pages checked 2026-09-22 (never the actual
# copyrighted HTML - just enough markup to exercise the extractor, per the acceptance criteria). --------------

ACADEMICTRANSFER_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","name":"PhD Position - AcademicTransfer",
 "mainEntity":{"@context":"http://schema.org","@type":"JobPosting",
   "datePosted":"2026-04-13T15:18:08.657203+02:00","validThrough":"2026-10-10T15:18:08.657203+02:00",
   "title":"PhD Position in Formal Methods for Concurrent Cryptographic Protocols",
   "description":"You will carry out research towards developing formal reasoning principles.",
   "baseSalary":{"@type":"MonetaryAmount","currency":"EUR",
     "value":{"@type":"QuantitativeValue","unitText":"MONTH","minValue":3059,"maxValue":3881}},
   "employmentType":"TEMPORARY","jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
     "addressLocality":"Amsterdam","addressCountry":"NL"}},
   "hiringOrganization":{"@type":"Organization","name":"Vrije Universiteit Amsterdam (VU)"}}}
</script>
</head><body></body></html>
"""

JOBS_AC_UK_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Faculty Position in Finance and Accounting",
 "description":"<p>Position: Assistant/Associate/Full Professor.</p>",
 "datePosted":"2026-07-27T00:00:00+00:00","validThrough":"2026-09-25T00:00:00+00:00",
 "employmentType":"Full Time,Fixed-Term/Contract",
 "hiringOrganization":{"@type":"Organization","name":"Shenzhen Technology University"},
 "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Shenzhen",
   "addressCountry":"China"}}]}
</script>
</head><body></body></html>
"""

NATURE_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting","title":"Associate Editor, Nature Communications",
 "datePosted":"2026-09-22T03:40:00.000Z","validThrough":"2026-10-11T22:59:00.000Z",
 "hiringOrganization":{"name":"Springer Nature Ltd","@type":"Organization"},
 "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"DE"}},
                {"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"CN"}}],
 "employmentType":"[\\"FULL_TIME\\"]"}
</script>
</head><body></body></html>
"""

NO_JOBPOSTING_HTML = """<html><head>
<script type="application/ld+json">{"@type":"WebPage","name":"Applied Science jobs in China"}</script>
</head><body></body></html>"""


def test_extract_jobposting_finds_bare_and_mainentity_wrapped():
    assert jobposting.extract_jobposting(JOBS_AC_UK_HTML)["title"] == "Faculty Position in Finance and Accounting"
    at = jobposting.extract_jobposting(ACADEMICTRANSFER_HTML)
    assert at["title"] == "PhD Position in Formal Methods for Concurrent Cryptographic Protocols"


def test_extract_jobposting_returns_none_when_absent():
    assert jobposting.extract_jobposting(NO_JOBPOSTING_HTML) is None
    assert jobposting.extract_jobposting("<html><body>no json-ld at all</body></html>") is None


def test_academictransfer_row_iso2_country_and_salary():
    jp = jobposting.extract_jobposting(ACADEMICTRANSFER_HTML)
    row = jobposting.jobposting_to_row(jp, "https://www.academictransfer.com/en/jobs/360110/phd/", "academictransfer", "raw/k1")
    assert row["country_iso2"] == "NL" and row["city"] == "Amsterdam"
    assert row["kind"] == "employment" and row["position_kind"] == "phd"
    assert row["salary_min"] == 3059 and row["salary_max"] == 3881 and row["salary_currency"] == "EUR" and row["salary_period"] == "month"
    assert row["posted_at"].year == 2026 and row["deadline"].year == 2026
    assert row["raw_html_ref"] == "raw/k1" and row["org_raw"] == "Vrije Universiteit Amsterdam (VU)"


def test_jobs_ac_uk_row_resolves_full_country_name_via_pycountry():
    jp = jobposting.extract_jobposting(JOBS_AC_UK_HTML)
    row = jobposting.jobposting_to_row(jp, "https://www.jobs.ac.uk/job/DQH648/faculty-position", "jobs_ac_uk", "raw/k2")
    assert row["country_iso2"] == "CN"                     # 'China' (a full name, not an ISO2 code) resolved
    assert row["kind"] == "employment" and row["position_kind"] == "professor"
    assert "<p>" not in row["raw_text"]                    # HTML stripped from the description


def test_nature_row_uses_the_first_location_with_a_resolvable_country():
    jp = jobposting.extract_jobposting(NATURE_HTML)
    row = jobposting.jobposting_to_row(jp, "https://www.nature.com/naturecareers/job/1/x/", "nature_careers", "raw/k3")
    assert row["country_iso2"] == "DE"                     # first of two jobLocation entries
    assert row["ft_pt"] == "full"                          # employmentType came as a JSON-encoded string list


def test_unresolvable_country_persists_the_row_with_null_country_and_a_location_class():
    """T23 (PM ruling R2): an unresolvable country is never fabricated (still None, D6 unchanged) - but the
    row itself is persisted now, not discarded, with a real location_class in its place (migration 0020)."""
    jp = {"title": "Some Job", "jobLocation": {"address": {"addressCountry": "Nowhereland"}}}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/1", "src", "raw/k")
    assert row is not None and row["country_iso2"] is None
    assert row["location_class"] == "unknown"
    jp_no_location = {"title": "Some Job"}
    row2 = jobposting.jobposting_to_row(jp_no_location, "https://x.invalid/1", "src", "raw/k")
    assert row2 is not None and row2["country_iso2"] is None and row2["location_class"] == "unknown"


def test_no_title_still_returns_none_nothing_to_show_at_all():
    assert jobposting.jobposting_to_row({}, "https://x.invalid/1", "src", "raw/k") is None


def test_f10_a_json_ld_description_with_a_literal_html_entity_is_decoded():
    """T29 (packet 017, PM-verified live): a JobPosting JSON-LD 'description' is JSON text, not HTML -
    json.loads() never decodes an HTML entity. A real, common site-side templating bug (HTML-escaping text
    before it's put in a field that never needed it) left a literal '&amp;' in 1082 real rows' raw_text
    across academictransfer/nature_careers/jobs_ac_uk/theunijobs before this fix."""
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}},
          "description": "R&amp;D role - reports to the Head of Science &amp; Engineering."}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/1", "src", "raw/k")
    assert row["raw_text"] == "R&D role - reports to the Head of Science & Engineering."


def test_no_salary_and_no_dates_leaves_those_fields_unset_not_fabricated():
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}}}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/2", "src", "raw/k")
    assert "salary_min" not in row and "posted_at" not in row and "deadline" not in row


# ---------------------------------------------------------------- F6 (PM live audit 2026-09-24, jobs.ac.uk) ----
def test_a_single_value_salary_stated_as_minvalue_with_a_placeholder_zero_maxvalue_collapses_to_one_value():
    """The real live jobs.ac.uk payload (Oxford DSK277, PM's own audit 2026-09-24): baseSalary.value states
    minValue=57986, maxValue=0 - a placeholder zero, not a genuine 0 upper bound - which crashed ingest by
    violating listing_salary_range's own CHECK (min <= max). Must collapse to a single value (min=max),
    never left as an invalid inverted range."""
    jp = {"title": "Associate Professor", "jobLocation": {"address": {"addressCountry": "GB"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "GBP",
                          "value": {"@type": "QuantitativeValue", "minValue": 57986, "maxValue": 0, "unitText": "YEAR"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/DSK277", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == 57986 and row["salary_max"] == 57986


def test_a_single_value_salary_stated_as_maxvalue_with_a_placeholder_zero_minvalue_collapses_to_one_value():
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "GBP",
                          "value": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 45000, "unitText": "YEAR"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/X", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == 45000 and row["salary_max"] == 45000


def test_schema_org_single_value_property_with_no_min_or_max_at_all_is_used_as_both_bounds():
    jp = {"title": "Lecturer", "jobLocation": {"address": {"addressCountry": "GB"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "GBP",
                          "value": {"@type": "QuantitativeValue", "value": 42000, "unitText": "YEAR"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/Y", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == 42000 and row["salary_max"] == 42000


def test_f6b_a_placeholder_zero_maxvalue_QUOTED_AS_A_STRING_still_collapses_to_one_value():
    """F6b: found live re-crawling jobs.ac.uk again 2026-09-28 - a real payload (ZJU-UoE Institute,
    DSM149) states minValue/maxValue as JSON STRINGS ('"500000.00"'/'"0.00"'), not numbers. Plain Python
    truthiness treats a non-empty string as truthy even when it reads as zero, so the original F6 fix (which
    only handled a real JSON number 0) never collapsed this one, and it reached the DB as an inverted
    500000.00 > 0.00 range and crashed the whole jobs_ac_uk run (CheckViolation on listing_salary_range,
    reproduced live, not hypothetical)."""
    jp = {"title": "Faculty Position in Biomedical Sciences", "jobLocation": {"address": {"addressCountry": "CN"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "CNY",
                          "value": {"@type": "StructuredValue", "unitText": "YEAR",
                                    "minValue": "500000.00", "maxValue": "0.00"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/DSM149", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == row["salary_max"] == "500000.00"   # collapsed, original string precision kept


def test_f6b_a_genuine_string_encoded_range_is_left_untouched():
    """The same string-typed payload shape, but with two real, distinct, non-zero bounds - must NOT collapse
    (only a placeholder-zero bound collapses); _salary_bound_num is used only to decide collapsing, never to
    coerce a real string amount into a float that would lose it its own stated precision."""
    jp = {"title": "Postdoc", "jobLocation": {"address": {"addressCountry": "CN"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "CNY",
                          "value": {"@type": "StructuredValue", "unitText": "YEAR",
                                    "minValue": "300000.00", "maxValue": "500000.00"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/DSM150", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == "300000.00" and row["salary_max"] == "500000.00"


def test_a_genuine_real_salary_range_is_left_untouched():
    jp = {"title": "Postdoc", "jobLocation": {"address": {"addressCountry": "GB"}},
          "baseSalary": {"@type": "MonetaryAmount", "currency": "GBP",
                          "value": {"@type": "QuantitativeValue", "minValue": 35000, "maxValue": 45000, "unitText": "YEAR"}}}
    row = jobposting.jobposting_to_row(jp, "https://jobs.ac.uk/job/Z", "jobs_ac_uk", "raw/k")
    assert row["salary_min"] == 35000 and row["salary_max"] == 45000
    assert row["status"] == "unknown"                      # neither a deadline nor a posted date - never 'open'


def test_expired_deadline_sets_status_expired_display_only():
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}},
          "validThrough": "2000-01-01T00:00:00Z"}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/3", "src", "raw/k")
    assert row["status"] == "expired"


def test_sitemap_locs_survives_octet_stream_wrong_content_type_and_mixed_loc_shapes():
    xml = (
        '<?xml version="1.0"?><urlset><url><loc>https://a.example/1</loc></url>'
        '<url><loc>//a.example/2</loc></url><url><loc>a.example/3</loc></url></urlset>'
    )
    assert jobposting.sitemap_locs(xml, "a.example") == [
        "https://a.example/1", "https://a.example/2", "https://a.example/3"]


@pytest.mark.parametrize("loc,default_host,expected", [
    ("https://www.jobs.ac.uk/job/X/y", "www.jobs.ac.uk", "https://www.jobs.ac.uk/job/X/y"),
    ("//www.jobs.ac.uk/job/X/y", "www.jobs.ac.uk", "https://www.jobs.ac.uk/job/X/y"),
    ("www.jobs.ac.uk/job/X/y", "www.jobs.ac.uk", "https://www.jobs.ac.uk/job/X/y"),
    ("emploi.cnrs.fr/Offres/CDD/UMR1-X/Default.aspx", "emploi.cnrs.fr", "https://emploi.cnrs.fr/Offres/CDD/UMR1-X/Default.aspx"),
])
def test_normalize_sitemap_loc_handles_every_shape_seen_live(loc, default_host, expected):
    assert jobposting.normalize_sitemap_loc(loc, default_host) == expected


# ---------------------------------------------------------------- T18: free-text ATS locations -----------------
def test_country_from_freetext_location_resolves_an_unambiguous_us_state():
    assert jobposting.country_from_freetext_location("New York, NY") == "US"


def test_country_from_freetext_location_resolves_a_full_country_name():
    assert jobposting.country_from_freetext_location("Amsterdam, Netherlands") == "NL"


@pytest.mark.parametrize("token", ["MD", "DE", "GA", "IN", "LA", "MA", "MT", "PA", "SC"])
def test_country_from_freetext_location_never_guesses_a_us_state_iso_country_collision(token):
    """Found live building ats_lever.py: 'Baltimore, MD' resolved to Moldova via a naive lookup before this
    fix - MD/DE/GA/IN/... are simultaneously real US state abbreviations AND real ISO country codes
    (Moldova/Germany/Gabon/India/Laos/Morocco/Malta/Panama/Seychelles). D6: never guessed either way."""
    assert jobposting.country_from_freetext_location(f"Somewhere, {token}") is None


def test_country_from_freetext_location_never_guesses_the_georgia_full_name_collision():
    """Found live crawling a real Greenhouse board 2026-09-23: 'Atlanta, Georgia' resolved to the COUNTRY
    Georgia (a genuine, unambiguous pycountry name match) before the full-state-name list existed."""
    assert jobposting.country_from_freetext_location("Atlanta, Georgia") is None
    assert jobposting.country_from_freetext_location("Houston, Texas") == "US"        # Texas has no country collision


def test_country_from_freetext_location_handles_remote_and_bare_city_as_unresolvable():
    assert jobposting.country_from_freetext_location("Remote") is None
    assert jobposting.country_from_freetext_location(None) is None
    assert jobposting.country_from_freetext_location("Zagreb") is None    # no comma at all - no region token to read


# ---------------------------------------------------------------- classify_location (T23, PM ruling R2) -------
def test_classify_location_is_remote_on_a_real_remote_work_phrase():
    d = jobposting.classify_location("Software Engineer - fully remote", has_country=False)
    assert d["location_class"] == "remote"
    assert d["location_class_raw"] == "fully remote"
    assert d["location_class_conf"] == 0.8


def test_classify_location_is_hybrid_and_wins_over_a_passing_remote_mention():
    """A real, common shape: 'hybrid role with some remote days' - hybrid is the more specific, more
    informative fact and is checked (and reported) first."""
    d = jobposting.classify_location("Data Analyst - hybrid role, some remote days available", has_country=False)
    assert d["location_class"] == "hybrid"


def test_classify_location_never_matches_a_bare_remote_in_an_unrelated_sense():
    """'remote sensing' is a real discipline name (found on real Inria/academic ads) - a bare 'remote' alone
    must never be treated as a work-arrangement signal."""
    d = jobposting.classify_location("PhD Position in Remote Sensing of Forest Canopies", has_country=False)
    assert d["location_class"] == "unknown"                     # no country given either, in this example


def test_classify_location_is_located_when_a_country_was_resolved_and_no_remote_signal_exists():
    d = jobposting.classify_location("Lecturer in Physics, Belgrade", has_country=True)
    assert d["location_class"] == "located"
    assert d["location_class_conf"] is None and d["location_class_raw"] is None


def test_classify_location_is_unknown_when_neither_a_country_nor_a_remote_signal_exists():
    d = jobposting.classify_location("Research Associate", has_country=False)
    assert d["location_class"] == "unknown"


def test_classify_location_recognises_the_serbian_latin_phrasing():
    d = jobposting.classify_location("Programer - rad na daljinu", has_country=False)
    assert d["location_class"] == "remote"


# ---------------------------------------------------------------- T26 (packet 015) -----------------------------
def test_apply_url_always_falls_back_to_the_ad_s_own_canonical_page():
    """No JobPosting field here carries a distinct 'apply here' link of its own - the ad's own page is
    always where a candidate would go to apply, so apply_url defaults to it (never left blank)."""
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}}}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/job/1", "src", "raw/k")
    assert row["apply_url"] == "https://x.invalid/job/1"


def test_contact_email_and_phone_from_the_detail_page_html_are_extracted():
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}}}
    html = '<html><body><a href="mailto:hr@example.rs">HR</a> <a href="tel:+381601234567">Call</a></body></html>'
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/job/2", "src", "raw/k", html=html)
    assert row["contact_emails"] == ["hr@example.rs"]
    assert row["contact_phones"] == ["+381601234567"]


def test_no_html_passed_still_returns_the_row_with_empty_contact_fields_never_a_crash():
    jp = {"title": "Research Fellow", "jobLocation": {"address": {"addressCountry": "GB"}}}
    row = jobposting.jobposting_to_row(jp, "https://x.invalid/job/3", "src", "raw/k")
    assert row["contact_emails"] == [] and row["contact_phones"] == []
    assert row["contact_name"] is None and row["contact_scope"] is None
