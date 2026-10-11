#!/usr/bin/env bash
# F5 (2026-10-11): the T28 agents (scheduler/api/web) write to ~/.ajp/logs/*.log with no rotation at all -
# unbounded growth for a stack meant to run unattended for months, and (before this fix) stdout and stderr
# sharing one file. This caps each log by keeping its most recent lines and truncating in place.
#
# Why "keep the tail, truncate in place" and not a plain rotate/rename: launchd opens StandardOutPath ONCE,
# at job start, and keeps that fd forever. Renaming the file would leave the daemon writing to an unlinked
# inode - the disk space is never reclaimed and the log looks empty until the next restart, i.e. silently
# broken. Truncating the SAME inode keeps launchd's fd valid; launchd opens these in append mode (O_APPEND),
# so the next write lands at the new end of the file rather than at a stale offset.
#
# Deliberately keeps the most recent lines (not the oldest): for a 24/7 daemon the newest evidence is the
# useful part - a crash being diagnosed right now - while the oldest is the part already acted on.
#
# Run by com.ajp.logrotate (daily, and once at load). Safe to run by hand: ./tools/logrotate.sh
set -euo pipefail

LOG_DIR="${AJP_LOG_DIR:-$HOME/.ajp/logs}"
MAX_BYTES="${AJP_LOG_MAX_BYTES:-5242880}"    # 5 MiB per file before it is trimmed
KEEP_LINES="${AJP_LOG_KEEP_LINES:-5000}"     # most recent lines kept when trimming

[ -d "$LOG_DIR" ] || { echo "no log directory at $LOG_DIR - nothing to rotate"; exit 0; }

rotated=0
for f in "$LOG_DIR"/*.log; do
  [ -f "$f" ] || continue                      # an unmatched glob (no .log files yet) is not an error
  size="$(stat -f%z "$f" 2>/dev/null || echo 0)"
  if [ "${size:-0}" -gt "$MAX_BYTES" ]; then
    tmp="$(mktemp "${TMPDIR:-/tmp}/ajp-logtrim.XXXXXX")"
    # `cat > "$f"` (not mv): truncate the original inode in place, keeping launchd's fd valid.
    tail -n "$KEEP_LINES" "$f" > "$tmp" && cat "$tmp" > "$f"
    rm -f "$tmp"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) trimmed $(basename "$f") ${size}B -> $(stat -f%z "$f")B (kept last $KEEP_LINES lines)"
    rotated=$((rotated + 1))
  fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) logrotate done: $rotated file(s) over ${MAX_BYTES}B (dir: $LOG_DIR)"
