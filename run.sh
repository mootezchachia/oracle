#!/bin/bash
# ORACLE — Main Runner
# Usage: ./run.sh [scan|markets|reddit|fred|news|full|simulate]

set -e
cd "$(dirname "$0")"

ACTION=${1:-full}

echo ""
echo "🔮 ══════════════════════════════════════════════"
echo "   ORACLE — Narrative Arbitrage Engine"
echo "   $(date '+%Y-%m-%d %H:%M %Z')"
echo "══════════════════════════════════════════════════"
echo ""

case $ACTION in
  scan|full)
    cd nerve && python3 event_scorer.py
    ;;

  markets)
    cd nerve && python3 polymarket_ws.py
    ;;

  reddit)
    cd nerve && python3 reddit_velocity.py
    ;;

  fred)
    cd nerve && python3 fred_watcher.py
    ;;

  news)
    cd nerve && python3 news_rss.py
    ;;

  simulate)
    echo "  Simulation targets ready in seeds/"
    echo ""
    ls -la seeds/*.md 2>/dev/null || echo "  No seeds yet. Run: ./run.sh full"
    echo ""
    echo "  To simulate, open Claude Code in this directory:"
    echo "  $ claude"
    echo ""
    echo "  Then paste the contents of CLAUDE-CODE-PROMPT.md"
    echo "  Or for a specific seed:"
    echo "  $ cat seeds/YOUR-SEED.md prompts/oracle.md"
    ;;

  *)
    echo "Usage: ./run.sh [command]"
    echo ""
    echo "  full      - Run complete scan (Polymarket + Reddit + FRED + News + Score)"
    echo "  markets   - Scan Polymarket only"
    echo "  reddit    - Scan Reddit only"
    echo "  fred      - Fetch economic data only"
    echo "  news      - Fetch news headlines only"
    echo "  simulate  - Show simulation targets"
    echo ""
    ;;
esac
