"""Playwright/Chromium render path (R2/R3): for JS-rendered pages and legacy-TLS hosts that plain httpx cannot read.

It is a *renderer*, not an evasion tool. Every request the browser makes goes through the same gate as httpx fetches:
  * manifest + robots.txt are checked BEFORE the browser is asked to load anything (a denial never reaches Chromium);
  * requests are intercepted with the DevTools Fetch domain, which pauses EVERY hop, redirects included (Playwright's
    page.route does not see redirect hops; a 302 into a robots-denied path was fetched before this was fixed). Document, XHR,
    fetch, script and stylesheet requests must pass manifest + robots; images, fonts, media, websockets etc. are failed;
  * iframes are never loaded, site isolation is off (so nothing hides in a separate target), service workers are blocked
    and popups cannot open (--block-new-web-contents + window.open neutralised; a popup is its own, uninterceptable target);
  * navigation hops are rate-limited like fetches (per-host 1 rps); identifying UA; real certificate validation
    (ignore_https_errors is never set); no login, no CAPTCHA handling, no cookie-consent clicking.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from ajp.connectors.base import FetchFailed, FetchResult, ManifestViolation, PoliteClient, UrlManifest
from ajp.robots import RobotsDenied

log = logging.getLogger("ajp.render")
_CHECKED_TYPES = {"Document", "XHR", "Fetch", "Script", "Stylesheet"}
_CHROMIUM_ARGS = ["--disable-site-isolation-trials", "--disable-features=IsolateOrigins,site-per-process",
                  "--block-new-web-contents"]       # popups are their own targets (not intercepted), so they must never open
_CONTENT_MIN_CHARS = 400   # same threshold as board.looks_like_js_shell: below this, the page still looks unhydrated
_CONTENT_POLL_MS = 200
_NO_POPUPS = "window.open = function () { return null; };"


class RenderUnavailable(RuntimeError):
    """Playwright or its browser is not installed."""


class PlaywrightRenderer:
    def __init__(self, client: PoliteClient, manifest: UrlManifest, *, timeout_ms: int = 30000, settle_ms: int = 1500):
        self._client, self._manifest = client, manifest
        self._timeout_ms, self._settle_ms = timeout_ms, settle_ms
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._creating = False          # True only while WE open a page; any other new page is a popup
        self.denied: list = []          # every request the gate refused: (resource type, url, reason)

    # -- lifecycle -----------------------------------------------------------------------
    def _ensure(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RenderUnavailable("playwright is not installed") from e
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
        except Exception as e:  # noqa: BLE001
            self._pw.stop()
            self._pw = None
            raise RenderUnavailable(f"chromium could not be launched: {str(e)[:120]}") from e
        # Certificate validation stays ON (default); the option is spelled out so a review can see it.
        self._context = self._browser.new_context(user_agent=self._client.user_agent, ignore_https_errors=False,
                                                  java_script_enabled=True, accept_downloads=False, service_workers="block")
        self._context.add_init_script(_NO_POPUPS)
        self._context.on("page", lambda pg: None if self._creating else self._deny("popup", pg.url, "PopupClosed", close=pg))

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = None

    def __enter__(self) -> "PlaywrightRenderer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- request gate --------------------------------------------------------------------
    def _deny(self, rtype: str, url: str, reason: str, close: Any = None) -> None:
        self.denied.append((rtype, url, reason))
        if close is not None:
            try:
                close.close()
            except Exception:  # noqa: BLE001
                pass

    def _allow(self, params: dict, main_frame_id: str, nav_hops: list) -> bool:
        url, rtype = params["request"]["url"], params.get("resourceType", "Other")
        if url.startswith(("data:", "about:")):
            return True                                                # inline content: no network request
        try:
            if rtype not in _CHECKED_TYPES:
                raise ManifestViolation(f"{rtype} is never loaded")
            if rtype == "Document" and params.get("frameId") != main_frame_id:
                raise ManifestViolation("iframes are never loaded")
            self._client.check(url, self._manifest)                    # manifest + robots for EVERY hop, redirects included
        except (ManifestViolation, RobotsDenied) as e:
            self._deny(rtype, url, type(e).__name__)
            return False
        if rtype == "Document":
            nav_hops.append(url)
            if len(nav_hops) > 1:                                      # redirect hop: same per-host rate limit as any fetch
                self._client.wait_turn(url)
        return True

    # -- hydration wait --------------------------------------------------------------------
    def _wait_for_hydration(self, page: Any) -> None:
        """Some boards render an almost-empty shell at 'load' and fill it in afterwards via JS/XHR (found live on
        agrif.bg.ac.rs: a fixed short sleep after 'load' returned before the board's ~17 items existed in the DOM).
        Wait for the network to go quiet, then poll the body's visible text length until it stops growing (or the
        settle budget runs out) - content/language-agnostic, so it does not depend on a page's own wording. Never
        raises: on any error here the render proceeds with whatever the page currently has."""
        try:
            page.wait_for_load_state("networkidle", timeout=self._settle_ms)
        except Exception:  # noqa: BLE001 - a page with persistent polling/analytics scripts may never go idle
            pass
        deadline = time.monotonic() + self._settle_ms / 1000
        last_len = -1
        while time.monotonic() < deadline:
            try:
                n = page.evaluate("document.body ? document.body.innerText.length : 0")
            except Exception:  # noqa: BLE001
                return
            if n >= _CONTENT_MIN_CHARS and n == last_len:
                return                                          # non-trivial and unchanged since the last poll: settled
            last_len = n
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms == 0:
                return
            page.wait_for_timeout(min(_CONTENT_POLL_MS, remaining_ms))

    # -- public --------------------------------------------------------------------------
    def render(self, url: str) -> FetchResult:
        self._client.check(url, self._manifest)          # a denial raises here, before any browser is involved
        self._client.wait_turn(url)
        self._ensure()
        self._creating = True
        try:
            page = self._context.new_page()
        finally:
            self._creating = False
        cdp = self._context.new_cdp_session(page)
        main_frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        nav_hops: list = []

        def on_paused(params: dict) -> None:
            ok = self._allow(params, main_frame_id, nav_hops)
            if ok:
                cdp.send("Fetch.continueRequest", {"requestId": params["requestId"]})
            else:
                cdp.send("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "BlockedByClient"})

        cdp.on("Fetch.requestPaused", on_paused)
        cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        try:
            try:
                resp = page.goto(url, wait_until="load", timeout=self._timeout_ms)
            except Exception as e:  # noqa: BLE001 - includes TLS validation failures and gate-blocked navigations
                why = f" (gate: {self.denied[-1][2]} for {self.denied[-1][1]})" if self.denied else ""
                raise FetchFailed(f"render failed for {url}: {str(e).splitlines()[0][:120]}{why}") from e
            self._wait_for_hydration(page)
            html = page.content()
            return FetchResult(url=url, final_url=page.url, status=resp.status if resp else 0, content=html.encode("utf-8"),
                               content_type="text/html; charset=utf-8", fetched_at=datetime.now(timezone.utc))
        finally:
            page.close()
