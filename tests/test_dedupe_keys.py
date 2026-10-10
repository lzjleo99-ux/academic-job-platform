from ajp.dedupe_keys import canonicalize_url, normalize_title, title_hash


def test_canonicalize_url():
    a = "HTTP://www.Example.org:80/jobs/123/?utm_source=x&b=2&a=1&fbclid=zz#apply"
    b = "https://example.org/jobs/123?a=1&b=2"
    assert canonicalize_url(a) == canonicalize_url(b) == b
    assert canonicalize_url("https://example.org/") == "https://example.org/"
    assert canonicalize_url("https://example.org:8443//a//b/") == "https://example.org:8443/a/b"
    assert canonicalize_url("https://example.org/jobs?id=1") != canonicalize_url("https://example.org/jobs?id=2")


def test_same_ad_in_cyrillic_and_latin_hashes_equal():
    lat = "Istraživač-pripravnik – Đorđe, šumarstvo"
    cyr = "Истраживач-приправник – Ђорђе, шумарство"
    assert normalize_title(lat) == normalize_title(cyr) == "istrazivac pripravnik djordje sumarstvo"
    assert title_hash(lat) == title_hash(cyr)


def test_hash_ignores_case_punctuation_spacing_only():
    assert title_hash("PhD Position  (Physics)!") == title_hash("phd position physics")
    assert title_hash("PhD Position Physics") != title_hash("PhD Position Chemistry")
    assert len(title_hash("x")) == 32
