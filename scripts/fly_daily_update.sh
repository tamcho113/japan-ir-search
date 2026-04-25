#!/bin/bash
# Daily incremental update for japan-ir-search (Fly.io side).
# Wakes the auto-stopped Fly machine via `flyctl ssh console -C` and runs
# `japan-ir-search update -d <date>` for the last N days. The machine
# auto-stops again afterwards.
#
# Required env:
#   EDINET_API_KEY  (already set as a Fly secret — not needed here)
#
# Optional env:
#   FLY_APP            Default: japan-ir-search
#   CATCHUP_DAYS       Default: 3
#   FLYCTL             Path to flyctl (default: ~/.fly/bin/flyctl)

set -uo pipefail

FLY_APP="${FLY_APP:-japan-ir-search}"
CATCHUP_DAYS="${CATCHUP_DAYS:-3}"
FLYCTL="${FLYCTL:-$HOME/.fly/bin/flyctl}"

if [[ ! -x "${FLYCTL}" ]]; then
  echo "ERROR: flyctl not found at ${FLYCTL}" >&2
  exit 1
fi

echo "=== fly_daily_update.sh start: $(date -u +%FT%TZ) ==="
echo "App: ${FLY_APP}, catchup days: ${CATCHUP_DAYS}"

# Ensure at least one machine is started (auto-stop leaves no started VMs,
# and `ssh console -C` does not auto-start in that case).
echo "--- starting machines ---"
"${FLYCTL}" machine list --app "${FLY_APP}" --json \
  | python3 -c '
import json, sys
for m in json.load(sys.stdin):
    if m.get("state") != "started":
        print(m["id"])
' | while read -r mid; do
    [[ -n "${mid}" ]] && "${FLYCTL}" machine start "${mid}" --app "${FLY_APP}" || true
done

# Wait briefly for the machine to accept SSH connections.
sleep 10

# Iterate oldest-to-newest (BSD date for macOS host)
for ((i=CATCHUP_DAYS-1; i>=0; i--)); do
  target=$(date -v-${i}d +%Y-%m-%d)
  echo "--- fly update ${target} ---"
  # `flyctl ssh console -C` auto-starts a stopped machine.
  "${FLYCTL}" ssh console --app "${FLY_APP}" \
      -C "/app/.venv/bin/japan-ir-search update -d ${target} -t all" \
      || echo "WARN: fly update failed for ${target} (continuing)"
done

echo "=== fly_daily_update.sh done: $(date -u +%FT%TZ) ==="
