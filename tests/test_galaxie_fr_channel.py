"""Galaxie/Odyssee (France): deeplink-only channel (T19 need_input escalation, 2026-09-23 - see this
segment's outbox row: Galaxie's own robots.txt 403s, and the platform is superseded by Odyssee, a JS SPA
with no discoverable public listing route without a scoped Playwright investigation)."""
import pytest
from psycopg import errors

from ajp.channels import galaxie_fr


def test_seed_creates_channel_with_both_links_never_crawled(conn):
    galaxie_fr.seed(conn)
    assert conn.execute("SELECT kind, robots_ok FROM source WHERE id='galaxie_fr'").fetchone() == ("channel", False)
    rows = conn.execute("SELECT slug, url FROM channel_link WHERE source_id='galaxie_fr' ORDER BY slug").fetchall()
    assert rows == [
        ("galaxie", "https://www.galaxie.enseignementsup-recherche.gouv.fr/ensup/cand_recrutement_enseignants_chercheurs.htm"),
        ("odyssee", "https://odyssee.enseignementsup-recherche.gouv.fr/"),
    ]
    with pytest.raises(errors.CheckViolation):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1','galaxie_fr','u','u','t','FR','other')""")


def test_seed_is_idempotent(conn):
    galaxie_fr.seed(conn); galaxie_fr.seed(conn)
    assert conn.execute("SELECT count(*) FROM channel_link WHERE source_id='galaxie_fr'").fetchone()[0] == 2


def test_module_performs_no_http():
    import re
    from pathlib import Path
    text = Path(galaxie_fr.__file__).read_text()
    assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|playwright|ajp\.connectors)", text, re.M)
