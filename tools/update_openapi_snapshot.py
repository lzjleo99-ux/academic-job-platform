"""T23: regenerate tests/fixtures/openapi_schema.json from the real, live FastAPI app object - the ONLY
sanctioned way to update the frozen contract test_openapi_snapshot.py enforces. Never hand-edit the JSON
file directly; run this after a deliberate, reviewed API change and re-run pytest to confirm the new
snapshot is what was intended (a diff of the committed file IS the change's own review artifact - the PM's
own review of an API-contract change is exactly a diff of this file).

    python tools/update_openapi_snapshot.py
"""
from __future__ import annotations

import json
from pathlib import Path

from ajp.api.app import app

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "openapi_schema.json"


def main() -> None:
    schema = app.openapi()
    SNAPSHOT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
