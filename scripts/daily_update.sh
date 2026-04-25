#!/bin/bash
# Daily incremental update for japan-ir-search.
# Runs `update -d` for the last N days so that even if the host was offline
# for a few days the gap is filled. Already-indexed filings are skipped.
#
# Required env:
#   EDINET_API_KEY      EDINET API v2 key (must be exported by caller)
#
# Optional env:
#   JAPAN_IR_SEARCH_DATA  DB directory (default: ~/.japan-ir-search on local,
#                         /data on Fly)
#   CATCHUP_DAYS          Number of days to re-scan (default: 3)
#   JAPAN_IR_SEARCH_BIN   Path to the japan-ir-search executable
#                         (default: auto-detect: /app entry on Fly,
#                          repo .venv on local)

set -uo pipefail

CATCHUP_DAYS="${CATCHUP_DAYS:-3}"

if [[ -z "${EDINET_API_KEY:-}" ]]; then
  echo "ERROR: EDINET_API_KEY is not set" >&2
  exit 1
fi

# Resolve the CLI binary
if [[ -z "${JAPAN_IR_SEARCH_BIN:-}" ]]; then
  if [[ -x "/app/.venv/bin/japan-ir-search" ]]; then
    JAPAN_IR_SEARCH_BIN="/app/.venv/bin/japan-ir-search"
  elif [[ -x "/Users/nobu/projects/japan-ir-search/.venv/bin/japan-ir-search" ]]; then
    JAPAN_IR_SEARCH_BIN="/Users/nobu/projects/japan-ir-search/.venv/bin/japan-ir-search"
  else
    JAPAN_IR_SEARCH_BIN="japan-ir-search"
  fi
fi

echo "=== daily_update.sh start: $(date -u +%FT%TZ) ==="
echo "Bin: ${JAPAN_IR_SEARCH_BIN}"
echo "Catchup days: ${CATCHUP_DAYS}"

# Iterate from oldest to newest so failures on older days do not block today's update.
for ((i=CATCHUP_DAYS-1; i>=0; i--)); do
  if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
    # BSD date (macOS)
    target=$(date -v-${i}d +%Y-%m-%d)
  else
    # GNU date (Linux / Fly container)
    target=$(date -u -d "${i} days ago" +%Y-%m-%d)
  fi
  echo "--- update ${target} ---"
  "${JAPAN_IR_SEARCH_BIN}" update -d "${target}" -t all || {
    echo "WARN: update failed for ${target} (continuing)"
  }
done

echo "=== daily_update.sh done: $(date -u +%FT%TZ) ==="
