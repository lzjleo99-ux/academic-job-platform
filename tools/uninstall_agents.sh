#!/usr/bin/env bash
# T28 (packet 016): removes the 3 user-level LaunchAgents install_agents.sh installed. Bootout only - this
# NEVER touches ~/.ajp (the real Postgres data, raw store, uploaded CVs/theses) or the repo checkout itself;
# uninstalling the agents is completely separate from deleting any data, on purpose.
#
# Like install_agents.sh, this script itself calls `launchctl bootout` (that is its whole job), but must
# never be RUN by an AI agent working on this codebase - only a human operator does that.
#
# Usage: ./tools/uninstall_agents.sh
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_GUI="gui/$(id -u)"

remove_agent() {
  local name="$1"
  local dest="$AGENTS_DIR/com.ajp.$name.plist"
  if launchctl bootout "$UID_GUI/com.ajp.$name" >/dev/null 2>&1; then
    echo "PASS: com.ajp.$name booted out"
  else
    echo "PASS: com.ajp.$name was not loaded (nothing to boot out)"
  fi
  if [ -f "$dest" ]; then
    rm -f "$dest"
    echo "PASS: removed $dest"
  fi
}

remove_agent scheduler
remove_agent api
remove_agent web

echo
echo "All 3 agents removed. Your data (~/.ajp) was never touched - it is still there for the next install."
