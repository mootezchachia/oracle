#!/usr/bin/env bash
# Adds the 3 new QStash schedules for ORACLE notifications:
#   - Morning digest @ 08:00 UTC daily
#   - Silent heartbeat every 12h
#   - Risk check every 6h
#
# Usage:
#   export QSTASH_TOKEN="your-token-here"
#   bash scripts/setup-qstash-schedules.sh
#
# QStash token: https://console.upstash.com/qstash

set -euo pipefail

: "${QSTASH_TOKEN:?Set QSTASH_TOKEN first: export QSTASH_TOKEN=...}"

BASE="https://qstash.upstash.io/v2/schedules"
TARGET="https://oracle-psi-orpin.vercel.app/api/strategy100-run"

add_schedule() {
  local cron="$1"
  local action="$2"
  local label="$3"
  echo "→ Adding: $label  ($cron)  → ?action=$action"
  curl -s -X POST "$BASE/$TARGET?action=$action" \
    -H "Authorization: Bearer $QSTASH_TOKEN" \
    -H "Upstash-Cron: $cron" \
    -H "Upstash-Method: GET" \
    -H "Upstash-Retries: 1" \
    -H "Upstash-Schedule-Id: oracle-$action" | head -c 300
  echo
  echo
}

add_schedule "0 8 * * *"    "daily-digest" "Morning digest"
add_schedule "0 */12 * * *" "health-ping"  "Pipeline heartbeat"
add_schedule "0 */6 * * *"  "risk-check"   "Portfolio risk check"

echo "Done. List schedules with:"
echo "  curl -s -H \"Authorization: Bearer \$QSTASH_TOKEN\" https://qstash.upstash.io/v2/schedules | jq"
