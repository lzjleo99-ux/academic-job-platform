"""T22 acceptance guard: PM's own 'clean-room bootstrap' requirement (T22) caught a genuine gap live - a
truly fresh venv's `pip install -e .` left ajp.thesis.parser's own `import pypdf` unresolved (installed by
NO declared dependency, present in this session's own long-lived .venv only because something else had
pulled it in at some earlier point, not because pyproject.toml ever asked for it), crashing the whole API on
startup the moment anything imported the thesis pipeline. This test discovers every top-level, non-stdlib
import across src/ajp/**.py and asserts each one is either the project's own package (`ajp`) or resolves
(via a small, real alias map for the handful of import-name != PyPI-distribution-name cases) to a name
declared in pyproject.toml's own [project.dependencies] - so a future connector/module that imports a new
third-party package without adding it to pyproject.toml fails this test immediately, offline, in seconds,
rather than only on someone else's fresh install."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src" / "ajp"
# Hand-parsed rather than via a TOML library: this project targets Python 3.9, which has neither tomllib
# (3.11+) nor any TOML parser as a DECLARED dependency of its own (tomli happens to be present in this dev
# environment only as some other tool's own transitive dependency) - pulling one in just for this test would
# be exactly the class of undeclared-dependency bug the test itself exists to catch. The dependencies array
# is a plain, single-quoted-string-per-line list (see pyproject.toml itself), simple enough to extract with
# a regex rather than a real parser.
_PYPROJECT_TEXT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

# A reasonably complete Python 3.9 stdlib module allowlist (this project's own `requires-python`) - built
# from what this codebase's own imports actually use, not an attempt at a universal list; sys.stdlib_module_names
# does not exist before 3.10, so there is no built-in way to ask the interpreter for this on 3.9 itself.
_STDLIB = {
    "__future__", "abc", "argparse", "array", "ast", "asyncio", "base64", "bisect", "builtins", "bz2",
    "calendar", "collections", "concurrent", "configparser", "contextlib", "copy", "csv", "dataclasses",
    "datetime", "decimal", "difflib", "email", "enum", "errno", "functools", "gc", "getpass", "glob", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "logging", "math", "mimetypes", "multiprocessing", "numbers", "operator", "os",
    "pathlib", "pickle", "platform", "pprint", "queue", "quopri", "random", "re", "secrets", "shlex",
    "shutil", "signal", "site", "smtplib", "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics",
    "string", "struct", "subprocess", "sys", "tempfile", "textwrap", "threading", "time", "tomllib",
    "traceback", "types", "typing", "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref",
    "xml", "zipfile", "zlib", "zoneinfo",
}

# Real, known cases where the import name differs from the PyPI distribution name declared in pyproject.toml.
_IMPORT_TO_DISTRIBUTION = {"docx": "python-docx", "yaml": "pyyaml", "psycopg": "psycopg"}


def _bracketed_string_list(array_name: str) -> set:
    """Every quoted string inside `array_name = [...]` (module-level, e.g. 'dependencies' - multi-line - or
    one entry of [project.optional-dependencies] - single-line, e.g. `browser = ["playwright>=1.45"]`).
    Matches the array as a run of quoted-string items only (`"[^"]*"` swallows a literal ']' inside e.g.
    "psycopg[binary]>=3.2" as ordinary string content, never as the delimiter), so it works for both shapes
    without caring whether the real closing bracket is on the same line or its own - found live building
    this: an earlier version that required the closing ']' to start its own line silently matched nothing at
    all for the single-line optional-dependency entries."""
    m = re.search(rf'^{re.escape(array_name)}\s*=\s*\[\s*((?:"[^"]*"\s*,?\s*|#[^\n]*\n\s*)*)\]',
                 _PYPROJECT_TEXT, re.M)
    if not m:
        return set()
    names = set()
    for entry in re.findall(r'"([^"]+)"', m.group(1)):
        name = re.split(r"[<>=\[; ]", entry, maxsplit=1)[0].strip().lower()
        names.add(name)
    return names


def _declared_dependency_names() -> set:
    """[project.dependencies] (always installed) plus every [project.optional-dependencies] extra (browser,
    similarity, dev) - an import gated behind an optional extra is still a REAL import some install path
    needs declared somewhere, just not unconditionally."""
    names = _bracketed_string_list("dependencies")
    for extra in ("browser", "similarity", "dev"):
        names |= _bracketed_string_list(extra)
    return names


def _top_level_imports() -> set:
    """Real import statements only, via ast - a text/regex scan would also match prose inside a docstring
    or comment that happens to start a line with 'from'/'import' (found live writing this very test: 'the'
    and 'this' both showed up as false "imports" from ordinary sentences before this fix)."""
    names = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_every_third_party_import_has_a_declared_dependency():
    declared = _declared_dependency_names()
    missing = []
    for name in sorted(_top_level_imports()):
        if name == "ajp" or name in _STDLIB:
            continue
        dist_name = _IMPORT_TO_DISTRIBUTION.get(name, name).lower()
        if dist_name not in declared:
            missing.append(f"{name} (as '{dist_name}')")
    assert missing == [], f"imported but not declared in pyproject.toml [project.dependencies]: {missing}"


def test_the_discovery_itself_finds_a_real_nonempty_set_not_a_vacuously_passing_check():
    assert len(_top_level_imports()) >= 20                          # a real, substantial set (40+ at last count)
    assert len(_declared_dependency_names()) >= 10
