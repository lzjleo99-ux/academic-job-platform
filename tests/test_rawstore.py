import json
from datetime import datetime, timedelta, timezone

import pytest

from ajp.rawstore import LocalFsStore, get_raw, meta_key, put_raw

T = datetime(2026, 9, 22, 1, 30, tzinfo=timezone(timedelta(hours=2)))  # = 2026-09-21 23:30 UTC


def test_key_layout_and_roundtrip(tmp_path):
    s = LocalFsStore(tmp_path)
    html = "<h1>Истраживач-приправник</h1>".encode()
    ref = put_raw(s, kind="html", source_id="euraxess", url="https://e.test/a?x=1", content=html,
                  fetched_at=T, status=200, content_type="text/html; charset=utf-8")
    assert ref.key == f"raw_html/v1/euraxess/2026/09/21/{ref.sha256}.html.gz"   # UTC date partition
    assert not ref.key.startswith("/")
    body, meta = get_raw(s, ref.key)
    assert body == html
    assert meta["url"] == "https://e.test/a?x=1" and meta["http_status"] == 200 and meta["size"] == len(html)
    assert meta_key(ref.key) == f"raw_html/v1/euraxess/2026/09/21/{ref.sha256}.meta.json"
    assert json.loads(s.get(meta_key(ref.key)))["fetched_at"] == "2026-09-21T23:30:00+00:00"


def test_same_bytes_same_day_same_key_next_day_new_key(tmp_path):
    s = LocalFsStore(tmp_path)
    a = put_raw(s, kind="json", source_id="euraxess", url="u", content=b"{}", fetched_at=T)
    b = put_raw(s, kind="json", source_id="euraxess", url="u", content=b"{}", fetched_at=T)
    c = put_raw(s, kind="json", source_id="euraxess", url="u", content=b"{}", fetched_at=T + timedelta(days=1))
    assert a.key == b.key != c.key and a.sha256 == c.sha256
    assert a.key.startswith("raw_json/v1/")


def test_rejects_unsafe_input(tmp_path):
    s = LocalFsStore(tmp_path)
    for bad in ("../x", "a/b", ".hidden", "UPPER", ""):
        with pytest.raises(ValueError):
            put_raw(s, kind="html", source_id=bad, url="u", content=b"x", fetched_at=T)
    with pytest.raises(ValueError):
        put_raw(s, kind="exe", source_id="ok", url="u", content=b"x", fetched_at=T)
    with pytest.raises(ValueError):
        put_raw(s, kind="html", source_id="ok", url="u", content=b"x", fetched_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        s.get("../../etc/passwd")


def test_corruption_is_detected(tmp_path):
    s = LocalFsStore(tmp_path)
    ref = put_raw(s, kind="html", source_id="ok", url="u", content=b"hello", fetched_at=T)
    import gzip
    s.put(ref.key, gzip.compress(b"tampered"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        get_raw(s, ref.key)
