#!/bin/bash
# ORACLE — Main Runner
# Usage: ./run.sh [command]

set -e
cd "$(dirname "$0")"

ACTION=${1:-full}

echo ""
echo "🔮 ══════════════════════════════════════════════"
echo "   ORACLE — Narrative Arbitrage Engine v2"
echo "   $(date '+%Y-%m-%d %H:%M %Z')"
echo "══════════════════════════════════════════════════"
echo ""

case $ACTION in
  full)
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

  dashboard|dash|ui)
    echo "  Starting ORACLE Dashboard on http://localhost:3000"
    echo ""
    python3 server.py
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

  alerts|notify)
    cd nerve && python3 notifier.py check
    ;;

  calibrate|score)
    cd nerve && python3 calibration.py check && python3 calibration.py report
    ;;

  scan|targets)
    cd nerve && python3 market_scanner.py
    ;;

  backtest)
    cd nerve && python3 backtest.py
    ;;

  # ═══ NEW: Signal Fusion & Analysis Commands ═══

  fuse|fusion)
    cd nerve && python3 signal_fusion.py
    ;;

  ta|technical)
    cd nerve && python3 technical.py
    ;;

  ensemble)
    cd nerve && python3 ensemble.py
    ;;

  prices|price)
    cd nerve && python3 price_ws.py
    ;;

  crypto|15m|btc)
    cd nerve && python3 crypto_15m.py
    ;;

  executor|trade|paper)
    cd nerve && python3 executor.py
    ;;

  100|strategy|testnet)
    cd nerve && python3 strategy_100.py ${2:-scan} ${3:-} ${4:-}
    ;;

  # ═══ Combined Pipelines ═══

  alpha)
    echo "  ─── ORACLE Alpha Pipeline ───"
    echo "  Step 1: Full data scan"
    cd nerve && python3 event_scorer.py
    echo ""
    echo "  Step 2: Price feeds"
    python3 price_ws.py
    echo ""
    echo "  Step 3: Signal fusion"
    python3 signal_fusion.py
    echo ""
    echo "  Step 4: Ensemble prediction"
    python3 ensemble.py
    echo ""
    echo "  ═══ Alpha pipeline complete ═══"
    ;;

  crypto-alpha)
    echo "  ─── ORACLE Crypto 15m Pipeline ───"
    echo "  Step 1: Price feeds (multi-exchange)"
    cd nerve && python3 price_ws.py
    echo ""
    echo "  Step 2: 15m market discovery + analysis"
    python3 crypto_15m.py
    echo ""
    echo "  ═══ Crypto pipeline complete ═══"
    ;;

  *)
    echo "Usage: ./run.sh [command]"
    echo ""
    echo "  ─── Data Collection ───"
    echo "  full          Run complete scan (Polymarket + Reddit + FRED + News + Score)"
    echo "  scan          Scan and rank top simulation targets"
    echo "  markets       Scan Polymarket only"
    echo "  reddit        Scan Reddit only"
    echo "  fred          Fetch economic data only"
    echo "  news          Fetch news headlines only"
    echo ""
    echo "  ─── Analysis ───"
    echo "  fuse          Run signal fusion engine (combine all signals)"
    echo "  ta            Run technical analysis module (RSI, MACD, BB, VWAP, HA)"
    echo "  ensemble      Run multi-model ensemble predictor"
    echo "  prices        Fetch real-time prices (Binance + Coinbase + CoinGecko)"
    echo "  crypto        Scan 15-minute crypto prediction markets"
    echo ""
    echo "  ─── Execution ───"
    echo "  executor      Check trade executor status (paper trading)"
    echo "  100           Run \$100 strategy (scan|auto|status|check|reset|bonds|crashes)"
    echo "                  auto [5] [0]: autonomous mode (interval mins, max cycles)"
    echo "  alpha         Run full alpha pipeline (scan → prices → fusion → ensemble)"
    echo "  crypto-alpha  Run crypto 15m pipeline (prices → market discovery → analysis)"
    echo ""
    echo "  ─── Operations ───"
    echo "  dashboard     Launch live dashboard on http://localhost:3000"
    echo "  simulate      Show simulation targets"
    echo "  alerts        Check for alerts and notifications"
    echo "  calibrate     Check resolutions and print scoreboard"
    echo "  backtest      Run historical backtesting analysis"
    echo ""
    ;;
esac
