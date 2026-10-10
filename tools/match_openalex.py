"""Fetch OpenAlex institutions for Serbia (1 gated request/page, raw stored with envelope) and match them
to the org seed. Writes src/ajp/seeds/rs_orgs_openalex.json. No mailto/personal data is sent."""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

from ajp import orgs
from ajp.connectors.base import PoliteClient, UrlManifest
from ajp.rawstore import LocalFsStore, put_raw

FIELDS = "id,display_name,display_name_alternatives,type,country_code,geo,homepage_url,ror,works_count"
URL = f"https://api.openalex.org/institutions?filter=country_code:RS&per-page=200&select={FIELDS}&cursor="
MANIFEST = UrlManifest(r"https://api\.openalex\.org/institutions\?filter=country_code:RS&[^ ]*")


def main() -> int:
    client, store = PoliteClient(rps=1), LocalFsStore("data/raw")
    rows, cursor, keys = [], "*", []
    while cursor:
        r = client.get(URL + cursor, manifest=MANIFEST)
        assert r.status == 200, r.status
        keys.append(put_raw(store, kind="json", source_id="openalex_institutions_rs", url=URL + cursor, content=r.content,
                            fetched_at=r.fetched_at, status=r.status, content_type=r.content_type, robots_allowed=True).key)
        j = json.loads(r.content)
        rows += j["results"]
        cursor = j["meta"].get("next_cursor")
    res = orgs.match_openalex(orgs.load_seed(), rows)
    matched_ids = {m["openalex_id"] for m in res["matches"]}
    res["openalex_total"] = len(rows)
    res["openalex_unmatched_sample"] = [{"id": x["id"], "name": x["display_name"], "type": x["type"]} for x in rows if x["id"] not in matched_ids]
    res["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    res["raw_keys"] = keys
    Path("src/ajp/seeds/rs_orgs_openalex.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print({k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k != "raw_keys"})
    for m in res["matches"]: print(f"{m['slug']:22} {m['score']:5} {m['matched_name']}")
    print("AMBIGUOUS:", [(a['slug'], [(c['name'], c['score']) for c in a['candidates'][:2]]) for a in res['ambiguous']])
    print("UNMATCHED:", [(u['slug'], u['best']['name'], u['best']['score']) for u in res['unmatched']])
    return 0


if __name__ == "__main__":
    sys.exit(main())
