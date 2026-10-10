#!/usr/bin/env bash
# T28 (packet 016, user's own explicit demand): install the 3 user-level LaunchAgents (scheduler, API, web)
# so this platform stays in sync with online jobs and stays reachable locally WITHOUT a human ever running a
# command by hand again. Idempotent - safe to re-run after a code update (re-bootstraps each agent with
# today's paths/plist content rather than erroring if one is already loaded).
#
# This script itself calls `launchctl bootstrap` (that is its whole job) - but per this project's own
# standing rule, an AI agent working on this codebase must never RUN this script itself; only a human
# operator (or the PM, after reviewing the diff) does that, after everything else in the same packet has
# already been verified. Written and syntax-checked, never executed, by the agent that wrote it.
#
# Usage: ./tools/install_agents.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$REPO_ROOT/.venv/bin"
WEB_DIR="$REPO_ROOT/web"
LOG_DIR="$HOME/.ajp/logs"
AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_GUI="gui/$(id -u)"

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; }
die() { fail "$1"; exit 1; }

echo "== Verifying paths =="
[ -x "$VENV_BIN/ajp-run" ] || die "no $VENV_BIN/ajp-run - run 'pip install -e \".[dev]\"' in a .venv at the repo root first"
[ -x "$VENV_BIN/ajp-api" ] || die "no $VENV_BIN/ajp-api - same venv as above"
[ -x "$VENV_BIN/ajp-init" ] || die "no $VENV_BIN/ajp-init - same venv as above"
pass "venv scripts found at $VENV_BIN"

NPM_BIN="$(command -v npm || true)"
[ -n "$NPM_BIN" ] || die "no npm on PATH - install Node.js first"
NODE_BIN_DIR="$(dirname "$NPM_BIN")"
pass "npm found at $NPM_BIN"

[ -d "$WEB_DIR" ] || die "no $WEB_DIR directory"

echo "== Seeding/upgrading the database (idempotent) =="
"$VENV_BIN/ajp-init"

mkdir -p "$LOG_DIR" "$AGENTS_DIR"
pass "log directory ready at $LOG_DIR"

echo "== Building the production web bundle (falls back to dev mode if this fails) =="
WEB_NPM_SCRIPT="start"
if ( cd "$WEB_DIR" && "$NPM_BIN" run build ) >"$LOG_DIR/web_build.log" 2>&1; then
  pass "production build succeeded - the web agent will run 'npm run start'"
else
  WEB_NPM_SCRIPT="dev"
  fail "production build failed (see $LOG_DIR/web_build.log) - the web agent will run 'npm run dev' instead"
fi

install_plist() {
  local name="$1"
  local template="$REPO_ROOT/tools/launchd/com.ajp.$name.plist"
  local dest="$AGENTS_DIR/com.ajp.$name.plist"
  [ -f "$template" ] || die "missing template $template"
  sed \
    -e "s|__AJP_VENV_BIN__|$VENV_BIN|g" \
    -e "s|__AJP_REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__AJP_LOG_DIR__|$LOG_DIR|g" \
    -e "s|__AJP_NPM_BIN__|$NPM_BIN|g" \
    -e "s|__AJP_NODE_BIN_DIR__|$NODE_BIN_DIR|g" \
    -e "s|__AJP_WEB_DIR__|$WEB_DIR|g" \
    -e "s|__AJP_WEB_NPM_SCRIPT__|$WEB_NPM_SCRIPT|g" \
    "$template" > "$dest"
  plutil -lint "$dest" >/dev/null || die "generated plist is not valid XML: $dest"
  # idempotent: bootout a previous instance of this exact agent first (a no-op, ignored, if it was never
  # loaded), then bootstrap the freshly-written plist - never errors out just because this is a re-run.
  launchctl bootout "$UID_GUI/com.ajp.$name" >/dev/null 2>&1 || true
  launchctl bootstrap "$UID_GUI" "$dest"
  pass "com.ajp.$name installed and bootstrapped ($dest)"
}

echo "== Installing LaunchAgents =="
install_plist scheduler
install_plist api
install_plist web

echo "== Health checks (a few seconds for each service to actually start) =="
sleep 3
if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
  pass "API health check (http://127.0.0.1:8000/health)"
else
  fail "API health check (http://127.0.0.1:8000/health) - check $LOG_DIR/api.log"
fi
if curl -fsS "http://127.0.0.1:3000/" >/dev/null 2>&1; then
  pass "web health check (http://127.0.0.1:3000/)"
else
  fail "web health check (http://127.0.0.1:3000/) - check $LOG_DIR/web.log (a production build/start can take longer than a dev server on first boot - re-run this health check by hand if it failed here)"
fi

echo
echo "Agents are user-level (gui/$(id -u)): they start automatically at login and restart automatically on a"
echo "crash (never on a clean exit, and never more than once every 10 minutes). Logs: $LOG_DIR/{scheduler,api,web}.log"
echo "Pause temporarily (relaunched automatically after the 10-minute throttle above):"
echo "  launchctl kill SIGTERM $UID_GUI/com.ajp.<scheduler|api|web>"
echo "Stop for good (removes it from launchd - it will NOT relaunch, and will not start again at your next login):"
echo "  launchctl bootout $UID_GUI/com.ajp.<scheduler|api|web>   (or run ./tools/uninstall_agents.sh for all three)"
