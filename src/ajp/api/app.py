"""FastAPI app factory (packet 007 T14_fastapi). Read-only: no route in this package ever writes to the
database - ingestion is the connectors' own job (ajp.ingest), kept entirely separate.

    uvicorn ajp.api.app:app --host 127.0.0.1 --port 8000        # localhost-only dev default; see tools/dev_up.py
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ajp import __version__
from ajp.api.routers import (applicants, channels, classification_review, facets, governance, health,
                             listings, meta, orgs, sources, thesis)

# T15's Next.js dev server only; never a wildcard (no client secrets ever cross this API anyway, but an open
# CORS policy would still let any page read a visitor's data through their browser).
_DEV_UI_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Academic Job Platform API", version=__version__,
        description="Read-only listing API (M3, packet 007). Every uncertain field carries {v, conf, raw}; "
                    "salary EUR/year figures always carry their fx basis; see /v1/meta.degraded_features for "
                    "known gaps (never silently omitted).",
    )
    # T25 (packet 016): POST/PATCH/DELETE added here alongside GET - the thesis router's own POST endpoints
    # already needed this (a pre-existing gap this docstring's own "GET" list never actually covered), and
    # the new applicants router needs all three for its own upload/edit/delete flow.
    app.add_middleware(CORSMiddleware, allow_origins=_DEV_UI_ORIGINS,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["*"])
    for router in (health.router, listings.router, facets.router, orgs.router, sources.router, meta.router,
                  channels.router, thesis.router, applicants.router, governance.router,
                  classification_review.router):
        app.include_router(router)
    return app


app = create_app()
