"""T26 (packet 015): the shared contact extractor - real fixtures for every obfuscation/format shape the
packet itself names, plus the false-positive traps found live while building this module."""
import pytest

from ajp import extract_contacts as ec


# ---------------------------------------------------------------- emails ------------------------------------
def test_mailto_link_is_extracted_and_preferred_over_a_plain_text_duplicate():
    html = '<a href="mailto:hr@example.rs?subject=Application">Apply</a> or email hr@example.rs directly.'
    assert ec.extract_emails(html=html) == ["hr@example.rs"]     # one entry, not two


def test_plain_email_in_body_text_is_found():
    assert ec.extract_emails(text="Send your CV to jobs.dept@university.edu.rs before the deadline.") == \
        ["jobs.dept@university.edu.rs"]


@pytest.mark.parametrize("obfuscated,expected", [
    ("Contact: jane.doe [at] example [dot] com", "jane.doe@example.com"),
    ("Contact: jane.doe(at)example(dot)com", "jane.doe@example.com"),
    ("Contact: jane.doe{at}example{dot}com", "jane.doe@example.com"),
    ("Contact: jane.doe AT example DOT com", "jane.doe@example.com"),
    ("Contact: jane.doe at example dot co dot rs", "jane.doe@example.co.rs"),
])
def test_every_obfuscation_shape_the_packet_names_is_deobfuscated(obfuscated, expected):
    assert ec.extract_emails(text=obfuscated) == [expected]


def test_a_bare_at_in_an_unrelated_sentence_never_false_triggers():
    """'at' is an ordinary English word - only the full local-AT-domain-DOT-tld shape ever counts, not a
    bare 'at' alone with no dot-segment following it."""
    assert ec.extract_emails(text="The interview will take place at the main office next week.") == []


def test_an_image_srcset_style_at_sign_is_never_mistaken_for_an_email():
    """Found live building this module: 'photo@2x.png' in real markup matches the plain email regex's own
    shape (local='photo', domain='2x', tld='png') unless explicitly guarded against."""
    assert ec.extract_emails(html='<img src="photo.jpg" srcset="photo@2x.png 2x">') == []


def test_deduplication_is_case_insensitive_keeping_first_seen_casing():
    text = "Email HR@Example.RS or hr@example.rs for questions."
    assert ec.extract_emails(text=text) == ["HR@Example.RS"]


@pytest.mark.parametrize("html,expected", [
    # nature.com (real ad, 2026-09-28): the site's own numeric-decimal-entity-obfuscated address
    ('<a href="mailto:kowalinski&#64;embl.fr">contact</a>', ["kowalinski@embl.fr"]),
    # a full character-by-character numeric-entity obfuscation (a real anti-scraper WordPress technique) -
    # decodes to 'pmfinfo@pmf.ni.ac.rs'
    ("&#x70;&#109;&#x66;&#105;&#x6e;&#102;&#x6f;&#64;&#x70;&#109;&#x66;&#46;&#x6e;&#105;&#x2e;&#97;&#x63;&#46;&#x72;&#115",
     ["pmfinfo@pmf.ni.ac.rs"]),
])
def test_html_numeric_character_reference_obfuscated_emails_are_decoded(html, expected):
    assert ec.extract_emails(html=html) == expected


def test_a_mailto_href_with_no_real_email_inside_it_at_all_is_never_kept():
    """Found live (nature.com, 2026-09-28): a real ad's own contact link is genuinely broken -
    href='mailto:Mailto:' - no real address anywhere in it. Must never surface 'Mailto' as a contact."""
    assert ec.extract_emails(html='<a href="mailto:Mailto:" target="_blank"></a>') == []


def test_a_mailto_href_containing_the_site_s_own_website_url_instead_of_an_email_is_never_kept():
    """Found live (University of Macau via theunijobs, 2026-09-28): the site's own authoring mistake pastes
    its website URL into a mailto: href. A real, separate, genuinely-valid mailto: link on the very same
    page (vacancy@um.edu.mo) must still be kept - only the broken one is dropped."""
    html = ('<a href="mailto:https://career.admo.um.edu.mo/">Website</a>'
            '<a href="mailto:vacancy@um.edu.mo">Email</a>')
    assert ec.extract_emails(html=html) == ["vacancy@um.edu.mo"]


def test_a_mailto_href_with_genuine_extra_text_glued_on_still_salvages_the_real_address():
    """Found live (Swinburne via theunijobs, 2026-09-28): href='mailto:team at talentacquisition@swin.edu.au '
    - the site's own broken markup glues 'team at ' onto the front of an otherwise real address. The real
    address is salvaged; the garbage prefix is not treated as part of it."""
    html = '<a href="mailto:team at talentacquisition@swin.edu.au ">contact</a>'
    assert ec.extract_emails(html=html) == ["talentacquisition@swin.edu.au"]


def test_no_email_anywhere_returns_an_empty_list_never_none_or_fabricated():
    assert ec.extract_emails(html="<p>No contact info here.</p>", text="Nothing to see.") == []


# ---------------------------------------------------------------- phones ------------------------------------
def test_tel_link_is_extracted_exactly_as_linked():
    html = '<a href="tel:+381601234567">Call us</a>'
    assert ec.extract_phones(html=html) == ["+381601234567"]


def test_international_format_in_text_is_found():
    assert ec.extract_phones(text="Reach the office at +381 11 123 4567 during business hours.") == \
        ["+381 11 123 4567"]


@pytest.mark.parametrize("text", [
    "Kontakt telefon: 011/123-4567",
    "Kontakt telefon: 011 123 4567",
    "Kontakt telefon: 011.123.4567",
])
def test_rs_local_grouped_formats_are_found(text):
    assert ec.extract_phones(text=text) == [text.split(": ")[1]]


def test_a_bare_unpunctuated_digit_run_is_never_matched_as_a_phone_number():
    """A postal code, a year, or an ext_uid-shaped number in the same body text must never be mistaken for a
    phone number - only a real, grouped/punctuated or '+'-prefixed shape counts."""
    assert ec.extract_phones(text="Reference number 20261234567, posted in 2026, postal code 21000.") == []


def test_deduplicates_the_same_number_seen_with_different_punctuation():
    text = "Tel: 011/123-4567. You can also reach us at 011 123 4567 by phone."
    assert ec.extract_phones(text=text) == ["011/123-4567"]        # same digits, first-seen form kept


def test_no_phone_anywhere_returns_an_empty_list():
    assert ec.extract_phones(text="No phone number stated in this ad.") == []


@pytest.mark.parametrize("text", [
    "Date limite de candidature : 04/01/2027",   # the exact real CNRS ad text that produced this bug live
    "Deadline: 15/03/2026",
    "Deadline: 2026/03/15",                       # the same date, YYYY/MM/DD order
])
def test_an_application_deadline_date_is_never_mistaken_for_a_local_phone_number(text):
    """Found live (2026-09-28) re-crawling CNRS during a real backfill run: '04/01/2027' has the identical
    grouped-digit punctuation shape _RS_LOCAL_PHONE looks for ('0XX/XXX-XXXX') and was being stored as a
    contact phone number on real production listings."""
    assert ec.extract_phones(text=text) == []


def test_a_real_rs_local_number_that_happens_to_end_in_a_four_digit_group_is_still_found():
    """The date-exclusion guard must not overreach: a real local number ending in a 4-digit group is only
    excluded when that group ALSO reads as a plausible year and the other two groups both fit day/month
    ranges - '011/1234-5678' fails the year check ('5678' is out of the 1900-2100 window) and must still be
    kept."""
    assert ec.extract_phones(text="Kontakt telefon: 011/1234-5678") == ["011/1234-5678"]


# ------------------------------------------------ T30 (packet 019): PM's own 4 adjudicated root causes -------
def test_f10_svg_icon_sprite_path_coordinate_garbage_never_yields_a_phone():
    """T30 case 1 (HIGHEST IMPACT, 1988 real rows on nature_careers/gen_sitemap_html__theunijobs, both the
    Madgex platform): PM's own 4 real captured tokens are literal substrings of an inline SVG icon-sprite's
    <path d="..."> coordinate data, not phone numbers. Must yield zero phones from this fixture."""
    html = (
        '<div class="job-detail">Call our office for details.'
        '<svg style="display:none" aria-hidden="true">'
        '<symbol id="icon-phone" viewBox="0 0 24 24">'
        '<path d="M007.792.301 L064.251.08.377 C04.128.04.264 077.216.179.281 Z"/>'
        '</symbol></svg>'
        '</div>'
    )
    assert ec.extract_phones(html=html) == []


def test_f10_a_self_closing_path_fragment_outside_any_svg_tag_is_also_removed():
    """T30 case 1 defensive insurance (PM's own instruction): a lone <path d="..."/> that survived outside
    any matched <svg>...</svg> pair (e.g. an earlier, cruder HTML-stripping pass removing the outer svg tag
    but not its nested path children) must still never surface as a phone number."""
    html = '<path d="M007.792.301 064.251.08.377"/> Contact us for more information.'
    assert ec.extract_phones(html=html) == []


def test_f10_a_real_svg_icon_next_to_a_genuine_tel_link_still_keeps_the_real_number():
    """The SVG noise-stripping must never swallow a real, separate tel: link sitting right next to it on the
    same real page."""
    html = ('<svg><path d="M04.128.04.264 077.216.179.281"/></svg>'
            '<a href="tel:+46879060 00">Call</a>')
    assert ec.extract_phones(html=html) == ["+46879060 00"]


@pytest.mark.parametrize("text,expected", [
    # PM's own 4 real captured examples (packet 019, T30 case 2): the intl regex used to stop after the
    # first 4-digit group, truncating each of these to a strictly shorter fragment.
    ("Please call +357 25002210 during office hours.", "+357 25002210"),
    ("Office line: +357-22841533.", "+357-22841533"),
    ("Reach the department at +43 1 47654 85501 on weekdays.", "+43 1 47654 85501"),
    ("Switchboard: +358 294140338.", "+358 294140338"),
])
def test_f10_the_exact_real_pm_truncated_international_numbers_are_now_captured_in_full(text, expected):
    assert ec.extract_phones(text=text) == [expected]


def test_f10_a_genuinely_short_real_finnish_number_is_still_kept_not_dropped_by_the_new_minimum():
    """A real, genuinely short international number (Finland's own government switchboard, +358 2941 911,
    10 total digits) must never be dropped as if it were one of the truncated fragments above - the new
    per-country minimum backstop must not overreach past PM's own named truncation cases."""
    assert ec.extract_phones(text="Government switchboard: +358 2941 911.") == ["+358 2941 911"]


@pytest.mark.parametrize("text", [
    # PM's own 2 real captured examples (packet 019, T30 case 3): a DOI's own date-encoded tail matches
    # _RS_LOCAL_PHONE's own grouped-digit shape exactly.
    "See doi:10.22331/q-2021-03-25-421 for the full reference.",
    "See doi:10.22331/q-2020-06-04-279 for the full reference.",
])
def test_f10_a_doi_date_tail_is_never_mistaken_for_a_local_phone_number(text):
    assert ec.extract_phones(text=text) == []


def test_f10_a_bare_date_tail_fragment_that_survives_doi_stripping_is_still_rejected_by_the_backstop():
    """T30 case 3 backstop: even without the full '10.xxxx/' DOI prefix present to strip, a token
    immediately preceded by a bare 'YYYY-MM-' date prefix is still rejected on its own terms."""
    assert ec.extract_phones(text="Published 2021-03-25-421 in the internal registry.") == []


def test_f10_the_exact_real_pm_serbian_document_reference_number_is_never_mistaken_for_a_phone():
    """T30 case 4 (packet 019): PM's own real captured Cyrillic sentence - a legal decision document
    reference number, not a phone number."""
    text = "Одлука бр. III-01-261/12 од 26.03.2026. године о расписивању конкурса."
    assert ec.extract_phones(text=text) == []


@pytest.mark.parametrize("text", [
    "Одлука бр. 01-352/19 од 12.02.2026. године.",
    "Odluka br. 01-228/22 od 03.04.2026. godine.",
])
def test_f10_other_real_rs_document_reference_numbers_with_different_markers_are_also_rejected(text):
    assert ec.extract_phones(text=text) == []


def test_f10_a_real_rs_local_phone_with_a_slash_but_no_document_reference_marker_nearby_is_still_kept():
    """The case-4 rejection must not overreach: a genuine local number with a slash-grouped shape and no
    document-reference marker anywhere nearby must still be found."""
    assert ec.extract_phones(text="Kontakt telefon: 011/243 64 64.") == ["011/243 64 64"]


# ---------------------------------------------------------------- contact name / scope -----------------------
def test_a_labelled_contact_person_is_extracted_with_scope_ad():
    d = ec.extract_contact_name_and_scope("For questions, Contact person: Jane Doe, before applying.")
    assert d == {"contact_name": "Jane Doe", "contact_scope": "ad"}


def test_a_serbian_labelled_contact_is_extracted():
    d = ec.extract_contact_name_and_scope("Kontakt osoba: Marko Marković")
    assert d == {"contact_name": "Marko Marković", "contact_scope": "ad"}


def test_a_generic_hr_department_label_with_no_personal_name_is_scope_org_hr():
    d = ec.extract_contact_name_and_scope("For more information, contact our HR Department.")
    assert d == {"contact_name": None, "contact_scope": "org_hr"}


def test_no_labelled_contact_at_all_returns_none_never_guessed_from_prose():
    d = ec.extract_contact_name_and_scope("This is a great opportunity for early-career researchers.")
    assert d == {"contact_name": None, "contact_scope": None}


# ------------------------------------------------ real garbage found live re-crawling, 2026-09-28 -----------
def test_a_lowercase_signature_repeat_on_the_next_blank_line_is_never_appended_to_the_name():
    """Real ad text: 'Contact: Alexander Bismarck \\n\\n  alexander...' (a signature block repeating the
    first name in lowercase below a blank line). The shared re.IGNORECASE previously made '[A-Z...]' match
    a lowercase letter too, AND the name group's own separator crossed the blank line - both are now fixed."""
    text = "Contact: Alexander Bismarck \n\n  alexander.bismarck@example.edu"
    assert ec.extract_contact_name_and_scope(text)["contact_name"] == "Alexander Bismarck"


def test_a_lowercase_word_right_after_the_name_is_never_appended():
    """Real ad text: 'Contact: Amber Smith at amber.smith@...' - 'at' is lowercase and must stop the match,
    not be swept in as if it were a 4th capitalized name token."""
    text = "Contact: Amber Smith at amber.smith@example.edu for questions."
    assert ec.extract_contact_name_and_scope(text)["contact_name"] == "Amber Smith"


def test_a_capitalized_label_on_the_next_line_is_never_appended_to_the_name():
    """Real ad text: 'Contact: Chang Liu     \\n\\n     Tel: ...' - 'Tel' is capitalized (so case alone
    cannot rule it out) but sits on its own line after a blank line and must not be swept into the name."""
    text = "Contact: Chang Liu     \n\n     Tel: +44 20 7946 0000"
    assert ec.extract_contact_name_and_scope(text)["contact_name"] == "Chang Liu"


@pytest.mark.parametrize("text", [
    "Contact: E-mail: jobs@example.edu",
    "Contact: Tel: +44 20 7946 0000",
    "Contact name: (to be confirmed)",
])
def test_a_single_word_field_label_or_unfilled_placeholder_is_never_treated_as_a_real_name(text):
    """'E-mail'/'Tel' are syntactically capitalized single words that satisfy the pattern's own shape but are
    field labels, not names; 'Contact name:' is a real, live unfilled template placeholder."""
    assert ec.extract_contact_name_and_scope(text)["contact_name"] is None


def test_a_real_contact_name_field_on_its_own_line_is_found_even_after_an_earlier_unrelated_contact_word():
    """Real ad text (nature.com, 2026-09-28): an intro line 'please contact:' with nothing useful right
    after it (a blank line, then a separate structured mini-form), followed by its OWN real field line
    'Contact name: Sarah Dricken'. The first 'contact:' occurrence must fail closed (never grab the wrong
    text across the blank line - previously this produced the literal placeholder-looking value 'Contact
    name' by matching two arbitrary words after the FIRST 'contact:'), and the search must still find the
    real, later, correctly-labelled 'Contact name:' line."""
    text = ("For more information, please contact:  \r\n\r\n Contact name: Sarah Dricken \r\n\r\n"
            " Title: Department Administrator \r\n\r\n Email address: sarah-dricken@uiowa.edu")
    assert ec.extract_contact_name_and_scope(text)["contact_name"] == "Sarah Dricken"


# ---------------------------------------------------------------- the combined entrypoint ----------------------
def test_extract_contacts_returns_exactly_the_four_listing_columns():
    html = '<a href="mailto:hr@example.rs">HR</a> <a href="tel:+381601234567">Call</a>'
    text = "Contact person: Jane Doe. Kontakt telefon: 011/123-4567."
    d = ec.extract_contacts(html=html, text=text)
    assert set(d.keys()) == {"contact_emails", "contact_phones", "contact_name", "contact_scope"}
    assert d["contact_emails"] == ["hr@example.rs"]
    assert d["contact_phones"] == ["+381601234567", "011/123-4567"]
    assert d["contact_name"] == "Jane Doe" and d["contact_scope"] == "ad"


def test_extract_contacts_with_nothing_present_returns_real_empty_values_never_fabricated():
    d = ec.extract_contacts(html="<p>Apply online.</p>", text="Apply online.")
    assert d == {"contact_emails": [], "contact_phones": [], "contact_name": None, "contact_scope": None}
