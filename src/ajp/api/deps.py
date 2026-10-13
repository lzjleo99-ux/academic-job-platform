"""DB connection dependency. Read-only: the API never writes (ingestion is the connectors' job).

DSN resolution: ``AJP_DSN`` first (this API's own env var), then ``DATABASE_URL`` (shared with
``ajp.migrate``/``ajp.fx`` so one env works for the whole dev stack). Tests override ``get_conn``
directly with the pgserver-backed fixture from ``tests/conftest.py`` - they never set either var.
"""
from __future__ import annotations

import os
import secrets
from typing import Iterator

import psycopg
from fastapi import HTTPException, Request
from psycopg.rows import dict_row


class DsnNotConfigured(RuntimeError):
    pass


def get_dsn() -> str:
    dsn = os.environ.get("AJP_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise DsnNotConfigured("set AJP_DSN or DATABASE_URL")
    return dsn


def get_conn() -> Iterator[psycopg.Connection]:
    try:
        dsn = get_dsn()
    except DsnNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        yield conn


# --------------------------------------------------------------------------- admin writes ----------------
# T49 (2026-10-13): the first endpoints in this package that legitimately need a secret are the ADMIN
# ones - recording a human decision on the classification review queue is not public self-service (that
# is what /v1/applicants and /v1/thesis are, and those are bounded by ajp.api.ratelimit instead, because
# a browser cannot hold a secret). A static token is exactly right for an operator action.
#
# Refuse-when-unconfigured, the same house rule as get_dsn's 503: an admin endpoint with no secret set
# must be OFF, never silently open. It is a feature that is switched on by configuring it, not a door
# that happens to be unlocked.
def require_admin(request: Request) -> None:
    token = os.environ.get("AJP_ADMIN_TOKEN")
    if not token:
        raise HTTPException(
            status_code=503,
            detail="AJP_ADMIN_TOKEN is not set - admin endpoints are disabled until it is configured")
    supplied = request.headers.get("authorization", "")
    if not supplied.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization: Bearer <AJP_ADMIN_TOKEN> required")
    # constant-time: a token check that short-circuits on the first differing byte leaks the token one
    # guess at a time over the network.
    if not secrets.compare_digest(supplied[len("Bearer "):], token):
        raise HTTPException(status_code=401, detail="invalid admin token")
