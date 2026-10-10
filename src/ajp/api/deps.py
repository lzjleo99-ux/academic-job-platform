"""DB connection dependency. Read-only: the API never writes (ingestion is the connectors' job).

DSN resolution: ``AJP_DSN`` first (this API's own env var), then ``DATABASE_URL`` (shared with
``ajp.migrate``/``ajp.fx`` so one env works for the whole dev stack). Tests override ``get_conn``
directly with the pgserver-backed fixture from ``tests/conftest.py`` - they never set either var.
"""
from __future__ import annotations

import os
from typing import Iterator

import psycopg
from fastapi import HTTPException
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
