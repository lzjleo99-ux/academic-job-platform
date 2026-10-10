"""T21 acceptance guard: PM found live (2026-09-24, RC1 acceptance audit) that 5 real, already-built,
already-tested connectors (cern/cnrs/academicpositions/jobs_ac_uk/nature_careers) were never registered by
ANY seeding path - ajp.bootstrap.ensure_sources() is the ONLY place a fresh install (ajp-init, tools/dev_up.py)
calls ensure_source() at all, so a connector missing from there is never scheduled/run in a real deployment
despite the code fully existing and working. This test discovers every connector module that defines its own
ensure_source() and asserts its class name appears as a real call in bootstrap.py's own source - so a NEW
connector added later without wiring it into bootstrap.py fails this test immediately, not silently."""
import re
from pathlib import Path

CONNECTORS_DIR = Path(__file__).parents[1] / "src" / "ajp" / "connectors"
BOOTSTRAP_SRC = (Path(__file__).parents[1] / "src" / "ajp" / "bootstrap.py").read_text(encoding="utf-8")

# ats_lever/ats_greenhouse take ensure_source(conn, tenant) - a REAL org tenant, discovered dynamically
# per-org (T20's own fan-out), not a fixed value bootstrap.py could ever seed ahead of time. Legitimately
# excluded, not an oversight - every OTHER module's ensure_source(conn) takes no further required argument.
_DYNAMIC_PER_TENANT_MODULES = frozenset({"ats_lever", "ats_greenhouse"})


def _connector_class_names_with_ensure_source():
    names = []
    for path in sorted(CONNECTORS_DIR.glob("*.py")):
        if path.stem in _DYNAMIC_PER_TENANT_MODULES:
            continue
        text = path.read_text(encoding="utf-8")
        if "def ensure_source" not in text:
            continue
        m = re.search(r"^class (\w+)\(Connector\):", text, re.M)
        if m:
            names.append((path.stem, m.group(1)))
    return names


def test_every_fixed_seed_connectors_ensure_source_is_actually_called_from_bootstrap():
    missing = [f"{cls} ({mod}.py)" for mod, cls in _connector_class_names_with_ensure_source()
              if f"{cls}.ensure_source(" not in BOOTSTRAP_SRC and f"{cls}(client, store).ensure_source(" not in BOOTSTRAP_SRC]
    assert missing == [], f"connector(s) with ensure_source() never called from ajp.bootstrap: {missing}"


def test_the_discovery_itself_finds_a_real_nonempty_set_not_a_vacuously_passing_check():
    """A regression in the discovery logic itself (e.g. a typo in the glob or the class-name regex) would
    make the test above vacuously pass by finding nothing to check - this guards against that."""
    found = _connector_class_names_with_ensure_source()
    assert len(found) >= 10                                    # a real, substantial set (16 at last count)
