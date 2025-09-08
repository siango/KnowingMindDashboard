#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
export TZ="${TZ:-Asia/Bangkok}"

TODAY="$(date +%F)"
# yesterday (toybox-friendly): 86400 วินาที
YDAY="$(date -d "@$(( $(date +%s) - 86400 ))" +%F 2>/dev/null || echo "")"

TOTAL_NOW=$(awk -F, 'NR>1 {s+=$3} END{print s+0}' projects.csv)
OVERALL=$(awk -F, 'NR>1 {n++; s+=$2} END{ if(n==0) print 0; else printf "%.0f", s/n }' projects.csv)

mkdir -p docs/data
# snapshot ของเมื่อวาน (ถ้ายังไม่มี)
if [ -n "$YDAY" ] && [ -f "docs/dashboard.json" ] && [ ! -f "docs/data/$YDAY.json" ]; then
  jq -c . docs/dashboard.json > "docs/data/$YDAY.json" || true
fi

TOTAL_YDAY=0
[ -n "$YDAY" ] && [ -f "docs/data/$YDAY.json" ] && TOTAL_YDAY=$(jq -r '.total_valuation_mln // 0' "docs/data/$YDAY.json")
DELTA=$(( TOTAL_NOW - TOTAL_YDAY ))

# ใช้ todos จาก dashboard.json ถ้ามี ไม่งั้นดึงจาก checklist.json
if jq -e '.todos' docs/dashboard.json >/dev/null 2>&1; then
  TODOS_JSON="$(jq '.todos' docs/dashboard.json)"
else
  TODOS_JSON="$(cat checklist.json 2>/dev/null || echo '[]')"
fi

jq -n \
  --arg date "$TODAY" \
  --argjson overall "$OVERALL" \
  --argjson total "$TOTAL_NOW" \
  --argjson delta "$DELTA" \
  --argfile todos <(echo "$TODOS_JSON") \
  '{date:$date, overall_pct:$overall, total_valuation_mln:$total, delta_mln:$delta, projects:[], todos:$todos}' \
  > docs/dashboard.json

cat > README.md <<MD
# KnowingMind — Daily Dashboard
- Date: $TODAY
- Overall: ${OVERALL}%
- Total Valuation: ${TOTAL_NOW} M THB
- Δ vs Yesterday: ${DELTA} M THB
MD

git add projects.csv checklist.json docs/dashboard.json docs/data README.md
git commit -m "Daily update: $TODAY (overall=${OVERALL}%, total=${TOTAL_NOW}M, delta=${DELTA}M)" || true
git pull --rebase || true
git push || true
