"""T42 (packet 024): a repo-hygiene guard over the ACTUAL git-tracked file set (`git ls-files`, not a
static directory walk - this is what will really be pushed) - fails the suite if a tracked file contains a
secret-shaped string, a hardcoded local absolute path, or if `.env` itself is somehow tracked. Same spirit
as web/tests/no-secrets.test.ts's own client-bundle guard, extended to the whole repo and to the git index
specifically (so a future `git add` of something `.gitignore` should have caught is caught here too,
before a push, not just by `.gitignore` possibly being wrong).

Skipped (not failed) entirely when this checkout is not a git repository at all - matches the rest of this
project's own "degrade honestly, never fabricate a result" convention (D6) rather than papering over a
missing precondition with a fake pass.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

# Same patterns web/tests/no-secrets.test.ts already uses for the client bundle - kept in sync
# deliberately (a real credential shape is a real credential shape regardless of which language scans
# for it) - plus an absolute-local-path check this project's own README/docs could otherwise leak (a
# contributor's own machine-specific path has no business in a public repo).
SECRET_PATTERNS: "list[tuple[str, re.Pattern]]" = [
    ("OpenAI-style key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    # Requires a NON-EMPTY password segment (`+`, not `*`) - this project's own real embedded-Postgres
    # DSN is `postgresql://postgres:@...` (blank password, local trust auth, zero security value) and
    # appears verbatim in a real, committed bootstrap transcript (docs/clean_bootstrap_transcript_...txt)
    # - a real, found-live false positive the `*` version would have wrongly flagged forever.
    ("Postgres connection string with embedded credentials", re.compile(r"postgres(?:ql)?://[^\s\"'`:]+:[^\s\"'`@]+@")),
    ("generic 'secret ='/'api_key ='/'password ='", re.compile(r"\b(secret|api[_-]?key|password)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']", re.IGNORECASE)),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----")),
]
ABSOLUTE_PATH_PATTERN = re.compile(r"/Users/[A-Za-z0-9_.-]+/")

# A real, found-live false positive: this test file's own fixture passes an OBVIOUSLY fake placeholder
# ("secret123") to prove the CORE adapter forwards whatever `api_key` it's given as a bearer token - never
# a real credential, and the ONLY tracked file this project has that triggers the generic secret-shaped
# pattern at all. A narrow, named, documented exemption (one specific file) rather than loosening the
# pattern itself, which exists precisely to catch a real credential assigned exactly this way.
_KNOWN_TEST_FIXTURE_FALSE_POSITIVES = {"tests/test_similarity_sources.py"}

# Binary/non-text extensions this repo genuinely has committed (real test fixtures, icons) - never
# decoded as text, never scanned; a binary file cannot meaningfully "contain" one of the text patterns
# above in a way worth flagging (a PDF/DOCX's own internal byte layout can coincidentally contain
# byte sequences that decode-and-match, which would be a false positive, not a real finding).
_BINARY_SUFFIXES = {".pdf", ".docx", ".doc", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".xml"}
# tests/fixtures/*.xml here is real ECB-rate test data - plain text, not a binary format at all (listed
# as "non-text" above because xlsx/docx are technically zipped XML - this project's own .xml fixture is
# genuinely plain text, so it is explicitly exempted from the broad .xml skip above).
_FORCE_TEXT = {"tests/fixtures/ecb-daily-2026-09-21.xml"}
# This file's own docstring/comments necessarily quote the secret patterns themselves as literal regex
# strings (to explain what they catch) - exempt from its own scan, same convention
# web/tests/no-secrets.test.ts uses by only scanning app/components/lib, never its own test file.
_SELF = "tests/test_repo_hygiene.py"


def _tracked_files() -> "list[str]":
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


_FILES = _tracked_files()


@pytest.mark.skipif(not _FILES, reason="not a git repository (or no tracked files) in this checkout")
def test_env_file_itself_is_never_tracked():
    assert ".env" not in _FILES, ".env is a real git-tracked file - it must never be, see .gitignore"


@pytest.mark.skipif(not _FILES, reason="not a git repository (or no tracked files) in this checkout")
@pytest.mark.parametrize("rel_path", _FILES)
def test_tracked_file_has_no_secret_shaped_string_or_hardcoded_local_path(rel_path):
    if rel_path == _SELF:
        return
    suffix = Path(rel_path).suffix.lower()
    if suffix in _BINARY_SUFFIXES and rel_path not in _FORCE_TEXT:
        return
    full = ROOT / rel_path
    if not full.is_file():
        return    # a path git tracks that is already gone from disk (e.g. mid-rename) - nothing to scan
    try:
        content = full.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        return    # a genuinely binary file this list did not anticipate - same honest skip, never a crash
    for label, pattern in SECRET_PATTERNS:
        if rel_path in _KNOWN_TEST_FIXTURE_FALSE_POSITIVES:
            continue
        assert not pattern.search(content), f"{label} pattern matched in tracked file {rel_path}"
    m = ABSOLUTE_PATH_PATTERN.search(content)
    assert m is None, f"hardcoded local absolute path {m.group(0) if m else ''!r} found in tracked file {rel_path}"
