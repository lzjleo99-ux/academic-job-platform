"""T29 (packet 017): the one centralized entity-decoding helper every connector's own row-builder (and
ajp.extract_contacts) shares - never a second, drifting copy."""
from ajp.extract.text import decode_bytes, decode_entities


def test_named_entities_are_decoded():
    assert decode_entities("R&amp;D") == "R&D"
    assert decode_entities('Company &quot;BCILITY&quot;') == 'Company "BCILITY"'


def test_numeric_entities_decimal_and_hex_are_decoded():
    assert decode_entities("name&#64;example.com") == "name@example.com"
    assert decode_entities("name&#x40;example.com") == "name@example.com"


def test_a_real_url_query_string_ampersand_is_decoded():
    assert decode_entities("https://x.invalid/job?TrackID=11&amp;utm_source=rss") == \
        "https://x.invalid/job?TrackID=11&utm_source=rss"


def test_none_and_empty_string_pass_through_unchanged_never_raise():
    assert decode_entities(None) is None
    assert decode_entities("") == ""


def test_plain_text_with_no_entities_at_all_is_returned_unchanged():
    assert decode_entities("Postdoc in Quantum Physics") == "Postdoc in Quantum Physics"


def test_idempotency_contract_calling_it_twice_on_already_clean_text_is_a_safe_no_op():
    """The documented contract this module's own docstring makes: a connector that already decoded once,
    whose result is then handed to something that decodes defensively again (e.g. ajp.extract_contacts),
    must never be corrupted by the second call."""
    once = decode_entities("Company &quot;BCILITY&quot; &amp; Partners")
    twice = decode_entities(once)
    assert once == twice == 'Company "BCILITY" & Partners'


def test_double_encoded_source_text_only_resolves_one_level_documented_not_silently_looped():
    """The one case the module's own docstring says is NOT covered: a genuinely double-escaped source
    ('&amp;quot;' in the raw feed/HTML itself) only resolves one level per call - this is a deliberate
    design choice (never loop-decode), not an oversight; verified here so a future change to loop-decode
    "to be safe" cannot land without this test failing first."""
    double_encoded = "&amp;quot;BCILITY&amp;quot;"
    once = decode_entities(double_encoded)
    assert once == "&quot;BCILITY&quot;"          # one level only
    assert decode_entities(once) == '"BCILITY"'    # a caller that wants full resolution calls it again itself


# ---- T35a (packet 021): decode_bytes - the ONE shared charset-respecting decode helper, replacing ~20
# hardcoded-utf-8 connector call sites (T34's own real defect, found on medf.kg.ac.rs's real windows-1251
# server) --------------------------------------------------------------------------------------------------
def test_a_real_windows_1251_declared_page_decodes_to_readable_cyrillic():
    cyrillic = "Пријава на конкурс".encode("windows-1251")
    assert decode_bytes(cyrillic, "text/html; charset=windows-1251") == "Пријава на конкурс"
    # confirms this fixture genuinely exercises the real defect (utf-8 would have produced U+FFFD garbage)
    assert "�" in cyrillic.decode("utf-8", "replace")


def test_a_real_latin1_declared_page_decodes_correctly():
    latin1_text = "Café Müller, résumé".encode("latin-1")
    assert decode_bytes(latin1_text, "text/html; charset=latin-1") == "Café Müller, résumé"


def test_a_real_gbk_declared_page_decodes_correctly():
    gbk_text = "你好，欢迎".encode("gbk")
    assert decode_bytes(gbk_text, "text/html; charset=gbk") == "你好，欢迎"


def test_charset_matching_is_case_and_whitespace_tolerant():
    cyrillic = "Конкурс".encode("windows-1251")
    for header in ("text/html; charset=WINDOWS-1251", "text/html;charset =  windows-1251",
                  "text/html; CHARSET=Windows-1251"):
        assert decode_bytes(cyrillic, header) == "Конкурс"


def test_a_missing_or_misspelled_charset_falls_back_to_utf8_unchanged():
    utf8_bytes = "Конкурс за избор".encode("utf-8")
    assert decode_bytes(utf8_bytes, "text/html") == "Конкурс за избор"
    assert decode_bytes(utf8_bytes, None) == "Конкурс за избор"
    assert decode_bytes(utf8_bytes, "text/html; charset=not-a-real-codec-xyz") == "Конкурс за избор"


def test_a_declared_charset_that_would_itself_fail_to_decode_falls_back_to_utf8():
    """A real, valid codec name that simply does not fit these particular bytes (e.g. a server mis-declaring
    ascii for real utf-8 content) must fall back rather than raise."""
    utf8_bytes = "Résumé café".encode("utf-8")
    assert decode_bytes(utf8_bytes, "text/html; charset=ascii") == "Résumé café"


def test_decode_bytes_is_idempotent_on_already_decoded_str_input():
    assert decode_bytes("already a string", "text/html; charset=windows-1251") == "already a string"
    assert decode_bytes("already a string", None) == "already a string"


def test_decode_bytes_never_raises_on_genuinely_undecodable_bytes_falls_back_to_replace():
    genuinely_bad = b"\xff\xfe\x00\x01not valid in any common text codec here\xff"
    result = decode_bytes(genuinely_bad, "text/html; charset=ascii")
    assert isinstance(result, str)   # never raises, even in the last-resort path
