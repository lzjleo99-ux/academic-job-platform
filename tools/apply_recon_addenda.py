"""Apply pm/recon/t5_bg_board_addenda.json to src/ajp/seeds/rs_orgs.json (robots-gated). Run after build_rs_org_seed.py."""
import json, sys
from pathlib import Path

from ajp import orgs
from ajp.connectors.base import PoliteClient

ADD = Path(sys.argv[1] if len(sys.argv) > 1 else "../pm/recon/t5_bg_board_addenda.json")
SEED = orgs.SEED_PATH
data = json.loads(SEED.read_text(encoding="utf-8"))
client = PoliteClient(rps=1)
log = orgs.apply_board_addenda(data["orgs"], json.loads(ADD.read_text())["addenda"], client.allowed)
CFG = SEED.parent / "rs_board_config.json"          # engineering-owned crawl tuning, re-applied every time
if CFG.exists():
    log["crawl_config_applied"] = orgs.apply_crawl_config(data["orgs"], json.loads(CFG.read_text())["entries"])
SEED.write_text(json.dumps(data, ensure_ascii=False, indent=1))
print(json.dumps(log, ensure_ascii=False, indent=1))
