"""FastAPI app factory (packet 007 T14_fastapi).

Writes: the thesis/applicants routers accept uploads (public self-service by product design), and the
classification-review router accepts an operator's decision on a queued classification (admin, bearer
token). Public self-service writes are bounded by ajp.api.ratelimit - a browser cannot hold a secret,
so volume, not identity, is the control that fits them; the admin write needs a real secret and is
gated on AJP_ADMIN_TOKEN (ajp.api.deps.require_admin), refuse-when-unconfigured.

    uvicorn ajp.api.app:app --host 127.0.0.1 --port 8000        # localhost-only dev default; see tools/dev_up.py
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ajp import __version__
from ajp.api.ratelimit import RateLimiter, resolve_rules
from ajp.api.routers import (applicants, channels, classification_review, facets, governance, health,
                             listings, meta, orgs, sources, thesis)

# T15's Next.js dev server only; never a wildcard (no client secrets ever cross this API anyway, but an open
# CORS policy would still let any page read a visitor's data through their browser). A deployed frontend on
# its own origin must be added here explicitly - see the deployment notes.
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
    # T49 (2026-10-13): inbound rate limiting for the public self-service writes. One instance per app, on
    # app.state, so a freshly built app (including every test's) starts with a clean slate.
    app.state.rate_limiter = RateLimiter(resolve_rules())
    for router in (health.router, listings.router, facets.router, orgs.router, sources.router, meta.router,
                  channels.router, thesis.router, applicants.router, governance.router,
                  classification_review.router):
        app.include_router(router)
    return app


app = create_app()
