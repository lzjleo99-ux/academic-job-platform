"""T18 acceptance: 'guard test using academics.com robots text as fixture'."""
from ajp.tdm import detect_tdm_reservation

# Exact text fetched live 2026-09-23 from https://www.academics.com/robots.txt (see tdm.py's own docstring).
ACADEMICS_COM_ROBOTS_TXT = """User-agent: *
Disallow: /preview/
Disallow: /athenex/live/websocket


User-agent: Bytespider
Disallow: /

Sitemap: https://storage.googleapis.com/ac_prod_sitemap/prod/COM/sitemap.xml.gz

# Legal notice: academics.com expressly reserves the right to use its content for commercial text and data mining (§ 44 b UrhG).
# The use of robots or other automated means to access academics.com or collect or mine data without
# the express permission of academics.com is strictly prohibited.
# academics.com may, in its discretion, permit certain automated access to certain academics.com pages,
# If you would like to apply for permission to crawl academics.com, collect or use data, please email info@academics.de
"""


def test_academics_com_robots_txt_is_detected_as_a_declared_tdm_reservation():
    assert detect_tdm_reservation(ACADEMICS_COM_ROBOTS_TXT) == "declared"


def test_an_ordinary_robots_txt_with_no_legal_notice_is_none():
    ordinary = "User-agent: *\nDisallow: /private/\nSitemap: https://example.invalid/sitemap.xml\n"
    assert detect_tdm_reservation(ordinary) == "none"


def test_empty_or_absent_robots_txt_is_none():
    assert detect_tdm_reservation("") == "none"
    assert detect_tdm_reservation(None) == "none"       # type: ignore[arg-type]


def test_urhg_44b_citation_alone_is_enough_even_without_the_academics_wording():
    text = "# Note: content is protected under section 44b UrhG.\nUser-agent: *\nAllow: /\n"
    assert detect_tdm_reservation(text) == "declared"


def test_tdm_wording_without_a_permission_mention_is_not_flagged():
    """The bare phrase must be paired with a permission/prohibition clause - a robots.txt that merely
    mentions 'text and data mining' in an unrelated, permissive context (e.g. explaining an opt-IN feed)
    should never be misread as an opt-out."""
    text = "# We publish a feed to support text and data mining research partnerships.\nUser-agent: *\nAllow: /\n"
    assert detect_tdm_reservation(text) == "none"
