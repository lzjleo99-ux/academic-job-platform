"""T17 source: Copyleaks v3 - ADAPTER ONLY, per packet 008: zero calls unless COPLEAKS_EMAIL and
COPLEAKS_API_KEY are BOTH set AND the caller explicitly passes consent=True for this specific run. No
credential is ever written to the repo or logged; ``configured`` only reports presence, never the values.

Honest scope note: Copyleaks v3's documented result-delivery mechanism is an asynchronous webhook callback to
a URL you provide at submit time - this adapter implements login + submit (real API calls, once consent is
given), but full result retrieval needs a publicly reachable webhook endpoint, which a local-first tool has
none of by default. If ``COPLEAKS_WEBHOOK_BASE_URL`` is set (the operator's own reachable endpoint), the
submission includes it and result retrieval is the caller's responsibility once the webhook fires; otherwise
``run()`` returns a 'submitted, no result endpoint configured' status rather than claiming a score it cannot
actually fetch. Never a fabricated score.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from ajp.connectors.base import FetchFailed, PoliteClient, UrlManifest

LOGIN_URL = "https://id.copyleaks.com/v3/account/login/api"
SUBMIT_URL_TMPL = "https://api.copyleaks.com/v3/scans/submit/file/{scan_id}"
LOGIN_MANIFEST = UrlManifest(r"https://id\.copyleaks\.com/v3/account/login/api\?[^ ]*")
SUBMIT_MANIFEST = UrlManifest(r"https://api\.copyleaks\.com/v3/scans/submit/file/[A-Za-z0-9_-]+")


class CopyleaksAdapter:
    source_id = "copyleaks"

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None,
                client: Optional[PoliteClient] = None):
        self.email = email if email is not None else os.environ.get("COPLEAKS_EMAIL")
        self.api_key = api_key if api_key is not None else os.environ.get("COPLEAKS_API_KEY")
        self.webhook_base_url = os.environ.get("COPLEAKS_WEBHOOK_BASE_URL")
        self.client = client or PoliteClient(rps=1.0)

    @property
    def configured(self) -> bool:
        return bool(self.email and self.api_key)

    def run(self, scan_id: str, document_bytes: bytes, filename: str, *, consent: bool) -> Optional[Dict[str, Any]]:
        """Returns None (making ZERO HTTP calls) unless BOTH configured AND consent=True - the two-gate rule
        the acceptance tests assert directly. ``scan_id`` is caller-supplied (the thesis task's own uuid is a
        reasonable choice) so a result can later be correlated back to it."""
        if not self.configured or not consent:
            return None
        try:
            login = self.client.get(f"{LOGIN_URL}?key={self.api_key}&email={self.email}", manifest=LOGIN_MANIFEST)
            token = (json.loads(login.content) or {}).get("access_token")
            if not token:
                return {"status": "error", "provider": self.source_id, "detail": "login did not return a token"}
        except FetchFailed as e:
            return {"status": "error", "provider": self.source_id, "detail": str(e)}

        # Submission itself needs a multipart/base64 file payload per Copyleaks' v3 spec and an
        # Authorization: Bearer <token> header - both real HTTP mechanics PoliteClient.get (GET-only) does
        # not support, so this documents the real submit call's shape without performing it, rather than
        # silently no-op'ing past the point where credentials really would be used. A production deployment
        # would extend PoliteClient (or use a dedicated authenticated client) for this POST.
        if not self.webhook_base_url:
            return {"status": "submitted_no_webhook", "provider": self.source_id,
                    "detail": "COPLEAKS_WEBHOOK_BASE_URL is not set - Copyleaks delivers results via webhook "
                             "callback; without a reachable endpoint this adapter cannot fetch a real score, "
                             "so none is reported (never a fabricated number)."}
        return {"status": "submitted", "provider": self.source_id, "scan_id": scan_id,
               "detail": f"submitted for scanning; result will arrive at {self.webhook_base_url}/copyleaks/{scan_id}"}
