"""T23: freeze the API's own OpenAPI contract - a change to any route's shape (a field added/removed/
retyped, a param added, a required field made optional or vice versa) must be a DELIBERATE, reviewed
change, never an accidental side-effect of an unrelated edit. This test regenerates the schema from the
real, live FastAPI app object (never from a hand-maintained copy) and diffs it against the committed
snapshot; a genuine, intended change is approved by regenerating the snapshot with
``python tools/update_openapi_snapshot.py`` and committing the diff - the diff itself IS the change's own
review artifact.
"""
from __future__ import annotations

import json
from pathlib import Path

from ajp.api.app import app

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "openapi_schema.json"


def _current_schema() -> dict:
    # app.openapi() memoizes onto app.openapi_schema after the first call - force a fresh build so this test
    # never accidentally passes against a schema some earlier test already cached from a monkeypatched app.
    app.openapi_schema = None
    return app.openapi()


def test_openapi_snapshot_file_exists_and_is_valid_json():
    assert SNAPSHOT_PATH.exists(), (
        f"{SNAPSHOT_PATH} is missing - run `python tools/update_openapi_snapshot.py` once to create it")
    json.loads(SNAPSHOT_PATH.read_text())            # never silently truncated/corrupt


def test_openapi_schema_matches_the_frozen_snapshot():
    current = _current_schema()
    frozen = json.loads(SNAPSHOT_PATH.read_text())
    if current != frozen:
        current_paths = set(current.get("paths", {}))
        frozen_paths = set(frozen.get("paths", {}))
        added_paths = sorted(current_paths - frozen_paths)
        removed_paths = sorted(frozen_paths - current_paths)
        detail = []
        if added_paths:
            detail.append(f"routes added: {added_paths}")
        if removed_paths:
            detail.append(f"routes removed: {removed_paths}")
        if not detail:
            detail.append("same route set, but a schema/param/response shape changed within one or more of them")
        raise AssertionError(
            "OpenAPI contract changed since the frozen snapshot (" + "; ".join(detail) + "). If this change "
            "is intended, run `python tools/update_openapi_snapshot.py` and commit the resulting diff to "
            "tests/fixtures/openapi_schema.json as part of this same change.")


def test_every_route_still_has_a_response_model_or_is_an_explicitly_documented_exception():
    """A route with no response_model silently degrades this snapshot's own protection (FastAPI falls back to
    a bare, unstructured schema) - export/csv-or-json and health/governance's own free-form dicts are the
    only sanctioned exceptions, named here explicitly rather than allowed by omission."""
    _FREEFORM_EXCEPTIONS = {
        ("GET", "/v1/listings/export"),          # csv-or-json StreamingResponse/list, by design
        ("GET", "/v1/governance"),
        ("GET", "/v1/coverage"),
        # T16 thesis module (pre-dates this test) - genuinely free-form dict/list/binary returns, none
        # declares a response_model; named here explicitly rather than left as a silent gap.
        ("GET", "/v1/thesis/{task_id}/report"),        # json-or-pdf, by design (?format=pdf)
        ("GET", "/v1/thesis/templates"),               # plain list
        ("GET", "/v1/thesis/similarity/sources"),      # plain list
        ("POST", "/v1/thesis/{task_id}/similarity"),   # similarity_report_to_dict()'s own dict shape
        ("GET", "/v1/thesis/{task_id}/similarity"),    # same shape, persisted copy
        ("GET", "/v1/thesis/{task_id}/export"),        # raw .docx bytes
    }
    current = _current_schema()
    for path, methods in current.get("paths", {}).items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH"):
                continue
            if (method.upper(), path) in _FREEFORM_EXCEPTIONS:
                continue
            responses = op.get("responses", {})
            ok = responses.get("200") or responses.get("201")
            if ok is None:
                continue
            has_schema = bool(ok.get("content", {}).get("application/json", {}).get("schema"))
            assert has_schema, f"{method.upper()} {path} has no structured 200 response schema"
