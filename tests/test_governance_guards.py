"""Grep-style guards over the source tree (packet 003 done_definition)."""
import re
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "ajp"
ROOT = Path(__file__).parents[1]
ALLOWED_DEEPLINK_MODULE = SRC / "channels" / "euraxess.py"
CHANNELS_DIR = SRC / "channels"


def py_files():
    return [p for p in SRC.rglob("*.py")]


def test_no_euraxess_job_or_api_url_constants_outside_the_deeplink_generator():
    bad = re.compile(r"euraxess\.ec\.europa\.eu|/jobs/search|/jobs/\{|/jobs/\d|job_country|/api/xmlguidelines", re.I)
    offenders = [str(p.relative_to(SRC)) for p in py_files() if p != ALLOWED_DEEPLINK_MODULE and bad.search(p.read_text())]
    assert offenders == [], f"EURAXESS URL constants outside the deeplink generator: {offenders}"


def test_deeplink_module_performs_no_http():
    """Every module under channels/ (not just euraxess.py - T13 wave B added several more deeplink-only
    sources) must never fetch anything: it only generates links and seeds source/channel_link rows."""
    for p in CHANNELS_DIR.glob("*.py"):
        text = p.read_text()
        assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|playwright|ajp\.connectors)", text, re.M), p
        assert not re.search(r"\.(get|post|fetch|request)\(", text.replace("conn.execute", "")), p


def test_normalizer_does_no_http_either():
    text = (SRC / "euraxess_normalize.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|playwright|ajp\.connectors)", text, re.M)


def test_tls_verification_is_never_disabled():
    pat = re.compile(r"verify\s*=\s*False|--insecure|CERT_NONE|check_hostname\s*=\s*False|SECLEVEL|_create_unverified", re.I)
    assert [str(p) for p in py_files() if pat.search(p.read_text())] == []


def test_only_the_polite_client_talks_http():
    """Connectors and jobs must go through PoliteClient; raw httpx use is confined to it."""
    offenders = [str(p.relative_to(SRC)) for p in py_files()
                 if re.search(r"^\s*(import httpx|from httpx)", p.read_text(), re.M)
                 and p.name not in ("base.py", "robots.py")]
    assert offenders == [], offenders


def test_no_personal_contact_hardcoded():
    assert [str(p) for p in py_files() if re.search(r"@(gmail|outlook|yahoo|hotmail)\.", p.read_text(), re.I)] == []


# ---------------------------------------------------------------- T26 (packet 015) -----------------------------
def test_every_connector_that_builds_a_row_also_wires_the_shared_contact_extractor():
    """packet 015's own audit: 'ZERO connectors populate emails/phones' - a real, live-found gap, closed by
    ajp.extract_contacts + wiring it into every row-building module. This is the permanent guard against a
    FUTURE connector shipping the same gap again: any module that sets extraction_family (the field every
    real row builder sets, per ajp.ingest.LISTING_COLUMNS) must also reference extract_contacts somewhere in
    its own source - never silently skipped."""
    connectors_dir = SRC / "connectors"
    offenders = []
    for p in connectors_dir.glob("*.py"):
        text = p.read_text()
        if "extraction_family" in text and "extract_contacts" not in text:
            offenders.append(str(p.relative_to(SRC)))
    assert offenders == [], f"row-building connector(s) never wire the shared contact extractor: {offenders}"


# ---------------------------------------------------------------- T23: explicit timeout on every outbound call ---
def test_every_httpx_client_construction_sets_an_explicit_timeout():
    """T23: every real (non-test) httpx.Client(...) this project ever constructs must set timeout= explicitly
    - never rely on httpx's own library default, which this project has never audited or committed to. httpx
    is confined to base.py/robots.py (test_only_the_polite_client_talks_http, above); this checks the ONE real
    construction site inside base.py itself carries the param (PoliteClient's own `timeout` __init__ param
    already defaults it for every real caller - see PoliteClient.__init__ - none of which passes its own
    already-built httpx.Client in, so this one constructor is genuinely the only place that matters)."""
    text = (SRC / "connectors" / "base.py").read_text()
    constructions = re.findall(r"httpx\.Client\([^)]*\)", text)
    assert constructions, "expected at least one real httpx.Client(...) construction in base.py"
    offenders = [c for c in constructions if "timeout" not in c]
    assert offenders == [], f"httpx.Client(...) constructed without an explicit timeout=: {offenders}"


# ---------------------------------------------------------------- T22: no secret ever committed in an .env.example ---
_SECRET_SHAPED_KEY = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|_PW|CREDENTIAL)\b", re.I)


def _env_example_files():
    return list(ROOT.glob(".env*.example")) + list(ROOT.glob("*/.env*.example"))


def test_env_example_files_exist():
    """A regression in the discovery glob itself would make the guard below vacuously pass by finding
    nothing to check."""
    assert len(_env_example_files()) >= 1


def test_no_env_example_ever_carries_a_real_looking_secret_value():
    """Every KEY/SECRET/TOKEN/PASSWORD/CREDENTIAL-shaped var in a committed .env*.example file must have an
    EMPTY value (a placeholder to fill in locally, in .env - which is gitignored - never a real committed
    one) - a non-secret-shaped var (a plain URL, a directory path) may have a real, non-secret example value."""
    offenders = []
    for path in _env_example_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if _SECRET_SHAPED_KEY.search(key) and value.strip():
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} {key}")
    assert offenders == [], f"secret-shaped var(s) with a non-empty example value: {offenders}"
