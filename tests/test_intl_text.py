import pytest

from ajp.extract import intl_text


@pytest.mark.parametrize("title,expected", [
    ("PhD Position in Formal Methods for Concurrent Cryptographic Protocols", "employment"),
    ("Faculty Position in Finance and Accounting", "employment"),
    ("Postdoctoral Research Associate in Neuroscience", "employment"),
    ("Lecturer in Computer Science", "employment"),
    ("Tenure-Track Assistant Professor of Chemistry", "employment"),
    ("Fully-funded PhD studentship: microstructure and defect control", "employment"),
    ("Promovendus Kunstmatige Intelligentie", "employment"),               # NL: PhD candidate in AI
    ("Onderzoeker Klimaatverandering", "employment"),                     # NL: researcher
    ("Universitair docent Rechten", "employment"),                       # NL: assistant professor (Law)
    ("Chargé de recherche en biologie moléculaire", "employment"),        # FR: research fellow
    ("Doctorant en physique quantique", "employment"),                   # FR: PhD student
    ("ERC Starting Grant: open call for host institutions", "grant_call"),
    ("Marie Skłodowska-Curie Postdoctoral Fellowship", "grant_call"),
    ("Research Fellowship in Environmental Policy", "grant_call"),
    ("Summer Internship in Data Science", "internship"),
    ("Work Placement Student, Chemistry Labs", "internship"),
    ("Undergraduate Admissions Open Day", "enrollment"),
    ("Administrative Assistant, Business School", "other"),
    ("Facilities Manager, Campus Services", "other"),
    ("Catering Assistant, Student Union", "other"),
    ("Sales Executive, University Press", "other"),
    ("International Marketing Officer", "other"),
    ("Random news article about a conference", "other"),
    ("Doktorand inom akustisk källkarakterisering", "employment"),          # SV: PhD position (KTH, live)
    ("Postdoktor inom kuggteknik", "employment"),                          # SV: postdoc
    ("Forskare för undervisning i Kognitiv interaktionsdesign", "employment"),  # SV: researcher
    ("Biträdande lektor i matematik", "employment"),                      # SV: assistant senior lecturer
    ("HR-generalist till KTH (vikariat)", "other"),                       # SV: HR generalist - not academic
    ("Studievägledare - Vikariat", "other"),                              # SV: study counsellor
    ("Verkstadstekniker", "other"),                                       # SV: workshop technician
    ("Forskningshandläggare med samordningsansvar", "other"),             # SV: research ADMINISTRATOR, not a researcher
    # T33 (packet 018, Q6): PM's own real-DB verification - genuine research-support staff, not a bare
    # generic officer/technician/specialist title (those stay excluded, see the negative cases below).
    ("Laboratory Officer", "employment"),
    ("User Research Officer", "employment"),
    ("Research Technician - Molecular Cell Biology - Hubrecht Institute - Utrecht", "employment"),
    ("Lead-Histology Lab Technician, Digital Pathology & Archive", "employment"),
    # T35/T36 (packet 021): PM's own explicit ruling resolving T33's own need_input - both are real paid
    # employee roles at the institution itself, not vendor/generic titles.
    ("Workday Solution Expert - Finance", "employment"),
    ("Kindergarten Team Lead / Bereichsleitung Krippe (m/w/d)", "employment"),
    # Negative guard: a bare generic officer/specialist/technician with NO research/lab qualifier must stay
    # excluded - the fix above must never widen into a blanket 'any officer/specialist title' rule (this
    # project's own real 'other' population is full of these on the exact same boards: IT/HR/finance
    # specialists, drivers, caterers - found live sampling the real DB building this exact fix). PM's own
    # explicit instruction: these 3 stay 'other' even after the Workday/Kindergarten ruling above.
    ("Solution Specialist", "other"),
    ("Microsoft Entra ID & Active Directory Specialist", "other"),
    ("Admissions Officer", "other"),
])
def test_classify_kind_intl(title, expected):
    assert intl_text.classify_kind_intl(title) == expected


def test_a_requirements_text_mention_of_laboratory_technician_experience_never_misclassifies_an_unrelated_role():
    """T33 (packet 018, Q6), found live building the fix above: a real 'Medical Lab Scientist' ad's own
    REQUIREMENTS text ('2 years of relevant clinical medical laboratory technician experience may be
    considered...') matched the new research-support-staff pattern too, when checked against the combined
    title+description text - misclassifying a role that never actually NAMES itself a lab technician. The
    fix is title-only (same precedent as the existing 'phd' rule) - this exact real sentence in the
    description must never flip the result."""
    title = "Medical Lab Scientist I or Medical Lab Scientist II-Core Lab Evening Shift"
    description = ("2 years of relevant clinical medical laboratory technician experience may be considered "
                   "for 1 year of medical technologist experience.")
    assert intl_text.classify_kind_intl(title, description) == "other"


def test_a_phd_ads_own_admission_requirements_boilerplate_is_not_an_enrollment_notice():
    """KTH, live: every one of its own doctoral ads has a standard 'Admission requirements'/'admitted to
    postgraduate education' eligibility section - a bare 'admission(s)' match would misclassify every single
    one of them as an enrollment/open-day notice instead of the genuine employment ad each one is."""
    title = "Doctoral student in acoustic source characterisation"
    description = ("Admission requirements: to be admitted to postgraduate education, the applicant must "
                   "have completed courses corresponding to at least 240 higher education credits.")
    assert intl_text.classify_kind_intl(title, description) == "employment"


def test_a_genuine_open_day_is_still_recognised_as_enrollment():
    assert intl_text.classify_kind_intl("Undergraduate Admissions Open Day") == "enrollment"


def test_a_job_funded_by_an_erc_grant_is_not_misclassified_as_a_grant_call():
    """KTH, live: a real postdoc ad ('Postdoc in complex fluid flows') names 'an ERC-funded research
    program' in its own body - describing the position's funding source, not advertising a funding call."""
    title = "Postdoc in complex fluid flows"
    description = "The position is supported by an ERC-funded research program on turbulence modelling."
    assert intl_text.classify_kind_intl(title, description) == "employment"


def test_a_genuine_erc_call_is_still_recognised_as_grant_call():
    assert intl_text.classify_kind_intl("ERC Starting Grant: open call for host institutions") == "grant_call"


def test_non_academic_role_wins_even_with_academic_context_nearby():
    # a real university facilities role - the employer name alone must never flip the classification
    assert intl_text.classify_kind_intl("Facilities Manager", "University of Oxford, Estates Services") == "other"


def test_description_is_also_searched():
    assert intl_text.classify_kind_intl("Open Position", "We are seeking a postdoctoral researcher in genomics.") == "employment"


def test_a_degree_requirement_mention_does_not_misclassify_a_professor_ad_as_a_phd_position():
    """jobs.ac.uk, live: 'Faculty Position in Finance and Accounting' names the rank ('Assistant/Associate/Full
    Professor') but its REQUIREMENTS section separately says 'Ph.D. degree' and '100% of faculty hold doctoral
    degrees' - a bare 'doctoral'/'phd' match would misclassify this as a PhD studentship, not a professor role."""
    title = "Faculty Position in Finance and Accounting"
    description = ("Position: Assistant/Associate/Full Professor. Requirements: Ph.D. degree; 100% of the "
                    "faculty hold doctoral degrees, and over 90% have overseas experience.")
    assert intl_text.position_kind_intl(title, description) == "professor"


def test_genuine_phd_studentship_is_still_recognised():
    assert intl_text.position_kind_intl("Fully-funded PhD studentship: microstructure and defect control") == "phd"
    assert intl_text.position_kind_intl("Doctoral Candidate in Machine Learning") == "phd"


def test_phd_fellowship_titled_ad_is_not_misclassified_by_its_supervisors_own_professor_title():
    """KU DK, live: a real PhD fellowship ad's body names its 'Principal supervisor: Associate Professor X' -
    the title alone ('PhD fellowship in ...') must win before the description is ever searched, otherwise the
    supervisor's own rank, not the advertised role, gets matched first."""
    title = "PhD fellowship in Computational Pharmacology and Data Science"
    description = "Principal supervisor: Associate Professor Alexander Hauser, Department of Drug Design."
    assert intl_text.position_kind_intl(title, description) == "phd"


def test_french_post_doctorat_noun_form_is_recognised_not_just_the_english_adjective():
    """CNRS, live: 'Post-doctorat' (the French NOUN form, missing English's '-al' suffix) was falling through
    both classify_kind_intl (kind='other') and position_kind_intl entirely - 'doctoral'/'doctorate' only cover
    the English pair, and the old 'post-?doc(?:toral)?\\b' pattern required an exact '-toral' suffix or nothing."""
    title = "Post-doctorat: Synthèse et valorisation de polyacides ricinoléiques (PRIC)"
    assert intl_text.classify_kind_intl(title) == "employment"
    assert intl_text.position_kind_intl(title) == "postdoc"


@pytest.mark.parametrize("title,expected", [
    ("Assistant Professor of Economics", "professor"),
    ("Lecturer in Law", "lecturer"),
    ("Postdoctoral Research Associate", "postdoc"),          # 'phd' rule doesn't fire; postdoc checked first anyway
    ("Research Fellow in Physics", "research_assistant"),
    ("PhD Position in Robotics", "phd"),
    ("Fully-funded PhD studentship", "phd"),
    ("Summer Internship in Data Science", "intern"),
    ("Administrative Assistant", "other"),
    ("Doktorand inom kvantinformationsteori", "phd"),                     # SV, KTH live
    ("Postdoktor inom strömningsmekanik", "postdoc"),                     # SV
    ("Forskare i hållbara finansmarknader", "researcher_title"),          # SV
    ("Forskningsingenjör inom precisionsmedicin", "research_assistant"),  # SV: research engineer
    ("Biträdande lektor i matematik", "lecturer"),                        # SV
    ("Licentiat inom framställning av metallpulver", "phd"),              # SV: pre-PhD research degree
    ("Doctoral student in acoustic source characterisation", "phd"),      # KTH's own EN translation of doktorand
    ("Post-Doctorant F/H Transfert d'attaques entre jeux de données par IA", "postdoc"),  # Inria, live: not phd
    ("Doctorant F/H Problèmes inverses pour les modèles stochastiques", "phd"),  # still phd without the 'post-' prefix
])
def test_position_kind_intl(title, expected):
    assert intl_text.position_kind_intl(title) == expected
