"""T13 wave B: deeplink-only channels for sources this project never fetches (bot-walled, needs a key it
doesn't have, no academic filter found, or a stated legal restriction). Same seed()/idempotency pattern as
euraxess.py, tested once per module rather than duplicating the full euraxess suite for each."""
import pytest
from psycopg import errors

from ajp.channels import academics_com, higheredjobs, infostud, jobbnorge

MODULES = [higheredjobs, jobbnorge, infostud, academics_com]


@pytest.mark.parametrize("mod", MODULES, ids=lambda m: m.SOURCE_ID)
def test_seed_is_idempotent_and_channel_kind(conn, mod):
    mod.seed(conn); mod.seed(conn)
    kind, robots_ok = conn.execute("SELECT kind, robots_ok FROM source WHERE id=%s", (mod.SOURCE_ID,)).fetchone()
    # infostud is the one deliberate exception: robots.txt genuinely ALLOWS its listing pages (verified live) -
    # the reason it stays deeplink is the missing academic filter, not a robots/technical block, so robots_ok=true
    # here accurately reflects the site's own policy, unlike the other three (all robots_ok=false for their own reasons).
    assert kind == "channel" and robots_ok is (mod.SOURCE_ID == "infostud")
    links = conn.execute("SELECT slug, url FROM channel_link WHERE source_id=%s", (mod.SOURCE_ID,)).fetchall()
    assert links and all(url.startswith("https://") for _, url in links)


@pytest.mark.parametrize("mod", MODULES, ids=lambda m: m.SOURCE_ID)
def test_channel_source_can_never_have_listings(conn, mod):
    mod.seed(conn)
    with pytest.raises(errors.CheckViolation, match="deeplink channel"):
        conn.execute("""INSERT INTO listing (ext_uid, source_id, url, url_canon, title_raw, country_iso2, position_kind)
                        VALUES ('1', %s, 'u', 'u', 't', 'RS', 'other')""", (mod.SOURCE_ID,))


def test_higheredjobs_deeplinks_go_to_the_human_browsable_category_page_not_the_walled_rss_feed():
    links = higheredjobs.deeplinks()
    assert len(links) > 1
    for d in links:
        assert "categoryFeed.cfm" not in d.url            # never link to the Imperva-walled feed itself


def test_jobbnorge_notes_the_missing_api_key():
    d, = jobbnorge.deeplinks()
    assert d.country_iso2 == "NO" and "key" in d.description.lower()


def test_infostud_notes_no_academic_filter_was_found():
    d, = infostud.deeplinks()
    assert d.country_iso2 == "RS"


def test_academics_com_never_hardcodes_a_bypass_and_names_the_permission_contact():
    d, = academics_com.deeplinks()
    assert "permission" in d.description.lower()
    assert academics_com.PERMISSION_CONTACT == "info@academics.de"
