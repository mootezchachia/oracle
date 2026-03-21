# ORACLE v2 — The Perfect Setup

> Inspired by the best open-source prediction market bots, multi-agent LLM research,
> and real-time crypto analysis systems. Zero external dependencies beyond Python stdlib + requests.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ORACLE v2 ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  LAYER 1: DATA COLLECTION (24/7 automated)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Polymarket│ │  Reddit  │ │   FRED   │ │ News RSS │ │ Price WS │ │
│  │ Gamma API│ │ Velocity │ │ Economic │ │ 5 feeds  │ │ 3 exch.  │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ │
│       │             │            │             │            │       │
│  LAYER 2: ANALYSIS ENGINE                                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Signal Fusion Engine                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐│  │
│  │  │Narrative │ │Technical │ │Sentiment │ │  Price Action    ││  │
│  │  │Ambiguity │ │ RSI MACD │ │ Reddit   │ │  Momentum/Spike  ││  │
│  │  │Scoring   │ │ BB VWAP  │ │ News vel │ │  Volume/Spread   ││  │
│  │  │          │ │ Heiken A │ │          │ │                  ││  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘│  │
│  │       └──────────┬──┴───────────┘                 │          │  │
│  │            Weighted Fusion (time-decayed)          │          │  │
│  │                      │                             │          │  │
│  └──────────────────────┼─────────────────────────────┘          │  │
│                         │                                        │  │
│  LAYER 3: ENSEMBLE PREDICTION                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐               │  │
│  │  │ Claude │ │  GPT   │ │ Gemini │ │ Local  │               │  │
│  │  │  35%   │ │  30%   │ │  20%   │ │  15%   │               │  │
│  │  │Narrat. │ │Quant.  │ │News    │ │Tech.   │               │  │
│  │  └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘               │  │
│  │      └──────┬───┴──────────┘           │                    │  │
│  │      Weighted Voting (calibration-adjusted)                  │  │
│  └──────────────────────┬───────────────────────────────────────┘  │
│                         │                                          │
│  LAYER 4: EXECUTION                                                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Confidence Gate → Kelly Sizing → Risk Limits → Execute      │  │
│  │  (min 50% conf)   (quarter-K)   ($10 max pos)  (paper mode) │  │
│  │                                  ($20 daily SL)              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  LAYER 5: CALIBRATION (the moat)                                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Brier Score → Model Weight Adjustment → Signal Reweighting  │  │
│  │  Resolution tracking → Pattern database → Prior injection    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  DASHBOARD: React + Vite + Tailwind (Vercel)                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Predictions | Signal Fusion | Ensemble | Markets | TA | P&L │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## What's New in v2

### 1. Signal Fusion Engine (`nerve/signal_fusion.py`)
**Inspired by:** aulekator's 7-phase weighted voting pipeline

- Combines narrative, technical, sentiment, and price signals
- Time-decayed weights (instant/short/medium/long half-lives)
- Agreement scoring (how much do signals agree?)
- Confidence-gated output (only recommend when signals converge)

### 2. Technical Analysis (`nerve/technical.py`)
**Inspired by:** FrondEnt's PolymarketBTC15mAssistant + FreqTrade/FreqAI

- RSI (14-period, oversold/overbought zones)
- MACD (12/26/9, histogram momentum)
- Bollinger Bands (20-period, mean reversion + trend)
- VWAP (cumulative volume-weighted price)
- Heiken Ashi (smoothed trend detection)
- All indicators output normalized signals for fusion engine

### 3. Multi-Model Ensemble (`nerve/ensemble.py`)
**Inspired by:** Fully-Autonomous bot's GPT/Claude/Gemini voting + multi-agent LLM paper

- 4 specialist agents: Narrative (Claude), Quantitative (GPT), News (Gemini), Technical (Local)
- Weighted voting with calibration-adjusted weights
- Agreement-gated confidence (only high-conviction when models agree)
- Specialist prompts per agent type

### 4. Real-Time Price Feed (`nerve/price_ws.py`)
**Inspired by:** aulekator's multi-exchange pipeline + Kalshi-CryptoBot's median aggregation

- Binance REST (klines for any timeframe)
- Coinbase REST (spot prices)
- CoinGecko (broader market data)
- Median price aggregation (manipulation resistant)
- Spike detection (rapid price movements)
- Cross-exchange divergence detection

### 5. Trade Executor (`nerve/executor.py`)
**Inspired by:** aulekator's risk management + discountry's TP/SL

- Kelly criterion position sizing (quarter-Kelly for safety)
- Configurable stop-loss / take-profit
- Confidence + edge + agreement gates
- Daily loss limit protection
- Cooldown between trades
- Paper trading by default

### 6. 15m Crypto Markets (`nerve/crypto_15m.py`)
**Inspired by:** All 15m Polymarket bots + flash crash strategy

- Auto-discovers active 15m crypto markets on Polymarket
- Flash crash detector (rapid probability drops)
- Full pipeline: discovery → price fetch → TA → fusion → recommendation
- Multi-crypto support (BTC, ETH, SOL, XRP)

## CLI Commands

```bash
# ─── Data Collection ───
./run.sh full          # Complete scan (existing)
./run.sh markets       # Polymarket only
./run.sh reddit        # Reddit velocity
./run.sh fred          # FRED economic data
./run.sh news          # News RSS

# ─── Analysis (NEW) ───
./run.sh fuse          # Signal fusion engine
./run.sh ta            # Technical analysis
./run.sh ensemble      # Multi-model ensemble
./run.sh prices        # Real-time prices (3 exchanges)
./run.sh crypto        # 15m crypto markets

# ─── Execution (NEW) ───
./run.sh executor      # Trade executor status
./run.sh alpha         # Full pipeline: scan → prices → fusion → ensemble
./run.sh crypto-alpha  # Crypto pipeline: prices → 15m markets → analysis

# ─── Operations ───
./run.sh dashboard     # Live dashboard
./run.sh calibrate     # Resolution tracking
./run.sh backtest      # Historical analysis
```

## Dashboard

The React dashboard now includes:
- **Signal Fusion panel** — live fusion results with signal breakdown
- **Ensemble panel** — model votes and agreement visualization
- **Executor panel** — open positions, P&L, trade history
- **15m Crypto panel** — active crypto markets with recommendations
- **TA Signals panel** — technical indicator readings

## Design Principles

1. **Zero marginal cost** — runs on Claude Max + free APIs only
2. **No external ML dependencies** — all TA computed in pure Python
3. **Paper trading by default** — never risks real money without explicit opt-in
4. **Calibration-driven** — every prediction tracked, weights adjusted by results
5. **Narrative-first** — ORACLE's unique edge is narrative simulation, everything else supports it
6. **Modular** — each module works standalone or plugged into the fusion engine

## What Makes This "Perfect"

| Capability | Source of Inspiration | ORACLE Implementation |
|---|---|---|
| Multi-exchange price aggregation | aulekator, Kalshi-CryptoBot | `price_ws.py` — Binance + Coinbase + CoinGecko median |
| Weighted signal fusion | aulekator's 7-phase pipeline | `signal_fusion.py` — time-decayed weighted voting |
| Technical analysis | FrondEnt, FreqTrade | `technical.py` — RSI, MACD, BB, VWAP, Heiken Ashi |
| Multi-model ensemble | Fully-Autonomous bot, academic papers | `ensemble.py` — 4 specialists with calibrated weights |
| Flash crash detection | discountry's probability spike bot | `crypto_15m.py` — FlashCrashDetector class |
| Risk-managed execution | aulekator, discountry | `executor.py` — Kelly sizing, TP/SL, daily limits |
| 15m crypto markets | All Polymarket BTC bots | `crypto_15m.py` — auto-discovery + full pipeline |
| Narrative simulation | ORACLE original (no competitor has this) | `event_scorer.py` + `prompts/oracle.md` |
| Calibration moat | ORACLE original | `calibration.py` → feeds back into all weights |
