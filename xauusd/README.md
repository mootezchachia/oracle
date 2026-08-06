# XAUUSD Sentinel

An institutional-grade monitoring system for Gold. It watches XAUUSD across
M1 → M5 → M15 → H1 → H4 continuously and publishes a signal only when a full
top-down confluence case is made inside an ICT kill zone, away from major
economic releases, with the macro complex agreeing.

**The objective is not signal count. Most of the time the correct output is
"no trade, and here is exactly why" — and the dashboard shows that reasoning
as prominently as it shows a signal.**

---

## What it actually does

```
     ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
     │ MT5 / Yahoo  │   │ ForexFactory │   │ DXY  US10Y   │
     │ M1…H4 candles│   │   calendar   │   │ XAG SPX NDX  │
     └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
            │                  │                  │
            ▼                  ▼                  ▼
   ┌────────────────────────────────────────────────────────┐
   │  H4 trend → H1 confirm → M15 setup → M5 entry → M1      │
   │  structure · liquidity · SMC · price action · indicators│
   └────────────────────────┬───────────────────────────────┘
                            ▼
   ┌────────────────────────────────────────────────────────┐
   │  CONFLUENCE  purity × coverage → raw score              │
   │  CONFIDENCE  × session × news × correlation × vol       │
   │  GATES       kill zone · displacement · R:R · cooldown  │
   └────────────────────────┬───────────────────────────────┘
                            ▼
        Telegram · Discord · Dashboard · TradingView · Journal
```

## Quick start

```bash
cd xauusd
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                              # installs the package, not just deps
```

`pip install -e .` matters: the package lives under `src/`, so installing the
requirements alone leaves `python -m xauusd` unable to find it. If you would
rather not install, prefix every command with `PYTHONPATH=src`.

```bash
python -m xauusd selftest       # end-to-end check, no network needed
python -m xauusd analyse        # one live evaluation, prints the full reasoning
python -m xauusd calendar       # what the news guard currently sees
python -m xauusd run            # continuous monitor + dashboard on :8787
```

Once installed, the config file is found automatically from any working
directory. Override it with `-c /path/to/config.yaml` or `XAUUSD_CONFIG`.

Docker:

```bash
cp .env.example .env            # add your Telegram/Discord credentials
docker compose up -d
open http://localhost:8787
```

---

## Design decisions worth knowing

### The confidence score is purity × coverage

Every check contributes weighted `Evidence` with a direction. The raw score is:

- **purity** — of the evidence that had something to say, what share supports
  the trade. One strong conflicting read costs far more than three silent checks.
- **coverage** — how much supporting weight accumulated, against
  `signals.full_conviction_weight`. Without this, a setup with two
  confirmations and nothing against it would score 100%.

A flawless but thin case tops out near 72%. Only a case that is clean *and*
complete reaches the 90s. Contextual multipliers (session, news, correlation,
volatility, alignment) are then applied — penalties uncapped, the combined
bonus capped at ×1.12, so context can never manufacture conviction the chart
did not earn.

### Confidence is not probability

`confidence` measures how much of the checklist agrees. `probability` is the
estimated chance of TP1 before stop, calibrated from the journal's realised
hit rate once ~25 resolved signals exist. Before that it falls back to a
deliberately conservative mapping capped at 78%. Presenting a 94% checklist
score as a 94% win rate would be dishonest, so the two numbers are separate.

### Hard gates that no score can override

| Gate | Why |
|---|---|
| H4 and H1 must agree | No HTF bias, no trade |
| Inside an ICT kill zone | Outside them, Gold's moves are noise |
| Displacement ≥ 0.8 ATR | Institutional repricing, not drift |
| News blackout / post-release settling | Being flat through FOMC is a position |
| Volatility not `NEWS_SPIKE` / `EXTREME` | You cannot model your own fill |
| Data not stale | A silent feed stall must never read as calm |
| ≥ 6 confirmations, blended R:R ≥ 2.0 | Thin or badly-paid setups are declined |
| Cooldown, reversal cooldown, price dedupe, daily cap | One idea, one alert |

### Everything is DST-aware

Sessions are defined in exchange-local time and converted through the IANA tz
database. London and New York change clocks on different dates, so any system
with hard-coded UTC offsets is wrong for several weeks a year — including the
weeks when Gold's session behaviour matters most. Tested explicitly.

### The overlap raises the bar

During the London/New York overlap the *minimum confidence goes up*, not down.
More participants means more liquidity and more deliberate traps.

### The backtester runs the live code

`Backtester` drives the same `SignalEngine` the monitor uses. Lookahead is
prevented structurally: `CSVProvider` holds a cursor and physically cannot
return a bar stamped after it. Higher timeframes are aggregated from the same
base series, so an H4 bar appears only once its final H1 constituent closes.
Trade simulation is pessimistic — entry pays the spread, and a bar touching
both stop and target is scored as the stop.

---

## Data providers

| Provider | Use for | Notes |
|---|---|---|
| **MT5** | Live execution | Windows + running terminal. Broker's own feed, real tick volume, correct symbol names. Auto-detects `XAUUSD`, `XAUUSD.r`, `GOLD`, etc. |
| **Yahoo** | Containers, monitoring, backtesting | No key. `GC=F` as the XAUUSD proxy, `XAUUSD=X` for the quoted price. **~15 min delayed** — the collector accounts for this and warns at startup. H4 is aggregated from H1. |
| **CSV** | Backtesting, tests | MT5 exports and generic OHLCV. |

The chain is configured in `data.providers`; the first that initialises wins
and the rest become hot fallbacks.

> Yahoo's delay means M1 *precision* is not achievable on it. Signals remain
> valid; the entry price will be a few minutes behind. For live trading, run
> the monitor on the Windows host beside your MT5 terminal.

## Economic news

Uses the free, key-less weekly JSON feed behind the Forex Factory calendar.
Events are re-classified for Gold specifically — FOMC, rate decisions, NFP and
CPI are `CRITICAL` regardless of the feed's own rating; a regional survey is
not. Three behaviours:

1. **Blackout** inside the window around a release — no signal at any score.
2. **Post-release settling** — the blackout ending is not the tape being
   tradeable. Trading stays paused until ATR mean-reverts toward its
   pre-release baseline *and* a minimum time has elapsed.
3. **Approach penalty** — outside the blackout, confidence scales down.

If the calendar cannot be fetched, the system **refuses to trade** rather than
assuming an all-clear. A missing calendar is not an empty calendar.

Geopolitical headlines are polled from public RSS and shock vocabulary applies
a further penalty, because Gold's largest moves often have no calendar entry.

## Notifications

```
🟢 BUY XAUUSD

Entry: 4262.10
SL:    4256.40   ($5.70)
TP1:   4270.00   (1.4R)
TP2:   4278.00   (2.8R)
TP3:   4292.00   (5.2R)

Confidence: 93%   (P(TP1) ≈ 66%)
Risk:       0.97%   0.17 lots
Session:    LONDON/NY OVERLAP

Reasons:
  ✔ H4 bullish — higher highs / higher lows
  ✔ H1 break of structure buy through 4258.40
  ✔ Liquidity sweep of equal lows at 4255.10
  ✔ Respecting a buy order block (4259.80–4262.40)
  ✔ Inside the London kill zone
  ✔ DXY, US10Y confirm
  ✔ ATR expansion — moves have room to run
```

Telegram and Discord fire concurrently with retry-and-backoff. If no channel is
configured the payload is still written to the log and the dashboard — an alert
you did not receive is indistinguishable from a setup that never happened.

## Dashboard

Mobile-first, dark/light aware, websocket-pushed with a polling fallback.
Shows the signal or the veto, session and news countdowns that tick locally
between pushes, the per-timeframe bias grid, volatility regime, correlation
desk, recent signals and — importantly — the **veto breakdown**, so you can see
*why* the system has been quiet.

`GET /api/snapshot` is the contract; the page is just one consumer. Point
Grafana or a shell script at the same JSON.

| Route | Purpose |
|---|---|
| `GET /` | Dashboard |
| `GET /api/snapshot` | Full state |
| `GET /api/signals?limit=N` | Journal history |
| `GET /api/health` | 200 ready / 503 warming up |
| `GET /ws` | Websocket push |
| `POST /tv/webhook` | TradingView inbound context |

## Auto-trading a demo account

Execution is **off by default**. The system is a monitor first. When you turn
it on it places real orders on whatever account the terminal is logged into —
so the first gate is the account type.

### The safety contract

| Gate | Behaviour |
|---|---|
| `require_demo_account: true` | The MT5 broker **refuses to arm** on a live account and releases the terminal. An unrecognised account type is treated as live, never as "probably demo". |
| `symbol_allowlist: [XAUUSD]` | Checked against the symbol the broker *resolved*, so `XAUUSD.r` passes and `EURUSD` cannot. |
| `magic: 20260806` | Tags every order. The system never modifies or closes a position it did not open — your manual trades on the same account are untouchable. |
| `max_open_positions: 1` | Two XAUUSD positions is one position at double the intended risk. |
| `max_daily_loss_percent: 3.0` | Disarms for the rest of the UTC day. The counter only ever rolls *forward*, so a backwards clock jump cannot clear it. |
| `kill_switch_file` / `XAUUSD_KILL_SWITCH` | Stops all new entries immediately, no restart or config edit. |
| Algo Trading check | If the terminal has algo trading disabled, it refuses to arm rather than sending orders that will be rejected. |

### Step 1 — paper mode, anywhere

Prove the whole path works before any terminal is involved. Paper mode needs no
MT5, runs on Linux or macOS, and performs fills, break-even moves, partial
closes and trailing for real in software.

```bash
XAUUSD_EXECUTION__ENABLED=true \
XAUUSD_EXECUTION__MODE=paper \
python -m xauusd run
```

Watch the **Execution** card on the dashboard. Leave it running for a few days.

### Step 2 — MT5 demo account

On Windows, with a demo account logged into the terminal and **Algo Trading
enabled** (the toolbar button):

```powershell
pip install -r requirements-mt5.txt

$env:XAUUSD_EXECUTION__ENABLED = "true"
$env:XAUUSD_EXECUTION__MODE = "mt5"
python -m xauusd run
```

The startup log tells you exactly what it armed against:

```
MT5 execution armed: DEMO account 5001234 on MetaQuotes-Demo,
symbol=XAUUSD, balance=10000.00 USD, magic=20260806
```

If it says `EXECUTION DISARMED`, read the reason — it is one of the gates
above, and every one of them is protecting you from something.

### What it does once filled

The broker holds a hard stop and the final target, so a crashed monitor leaves
a **protected** position rather than a naked one. In software, on a 15-second
loop:

- **break-even** at +1R (a hair past entry, so BE still covers costs)
- **partial closes** at TP1 and TP2 per `risk.partial_percents`
- **ATR trailing** after +1.5R, and the stop only ever moves forward

### Manual controls

```bash
python -m xauusd positions     # account, and every position we own
python -m xauusd flatten       # EMERGENCY: close everything, now
touch data/HALT                # stop new entries; existing ones stay managed
```

### Honest limitations

- **The MT5 path is untested against a real terminal.** It is covered by a
  faithful fake that reproduces the failure modes that actually bite — filling
  modes, minimum stop distances, volume steps, foreign positions — but I could
  not run it against MetaTrader from this environment. Watch the first few
  trades on demo.
- **Paper mode flatters you.** Fills pay a fixed spread; slippage, requotes,
  partial fills, swap and commission are not modelled. Paper beats demo, and
  demo beats live.
- **Signal price is not fill price.** The signal is computed on a candle close;
  the order fills at the live quote moments later. Planned and realised risk
  differ slightly, and the position tracks the realised one.
- **Run it on demo until the journal has a real sample.** Not one good week —
  a sample. The optimiser wants ~25 resolved signals before it will adjust a
  single weight, and that is a reasonable bar for you too.

## Backtesting

```bash
python -m xauusd backtest XAUUSD_M1.csv --base M1 --step M5 \
    --start 2025-01-01 --out report.json
```

Reports win rate, profit factor, expectancy, average R:R, max drawdown, longest
losing run, and breakdowns by session, kill zone, month, direction and
confidence bucket — plus the veto ledger. Everything is in **R units**, which
is the only size-independent way to compare.

A full H4 warm-up needs ~35 days of continuous data. Shorter datasets still
run, but the H4 EMA200 is unavailable and the run logs a warning rather than
quietly producing a degraded backtest that looks like a real one.

## Learning

Every decision is journalled to SQLite — including rejections, because the
record of what was declined and why is the most useful diagnostic there is.
Emitted signals are stored with their full evidence set and resolved against
subsequent price.

The optimiser then:

- compares each evidence code's win rate against the baseline and adjusts its
  weight, **shrunk toward the baseline by sample size** and bounded to ±35%, so
  a lucky six-trade run cannot double a weight;
- builds a confidence-bucket calibration table that replaces the conservative
  fallback probability mapping.

## Configuration

`config/config.yaml` is fully commented. Any value can be overridden by
environment variable using `XAUUSD_` and `__` as the nesting separator:

```bash
XAUUSD_SIGNALS__MIN_CONFIDENCE=93
XAUUSD_RISK__MAX_RISK_PERCENT=0.5
XAUUSD_NOTIFY__TELEGRAM__BOT_TOKEN=123:abc
```

Secrets should always come from the environment, never the committed YAML.

## Tests

```bash
python -m pytest -q          # 230 tests, ~9s, fully offline
```

The suite asserts both directions of the core property: the engine **can**
publish on a textbook setup (a system that never signals is broken, not
selective), and it **declines** on every gate individually.

## Project layout

```
src/xauusd/
  models.py        domain types
  config.py        YAML + env configuration
  data/            MT5 / Yahoo / CSV providers, async collector, candle store
  sessions/        DST-aware sessions and ICT kill zones
  news/            calendar, classifier, headline monitor, trading guard
  analysis/        indicators, structure, SMC, price action, volatility,
                   correlation, multi-timeframe engine
  engine/          confluence, confidence, risk, state, orchestration
  notify/          Telegram, Discord, formatting
  dashboard/       async web dashboard
  backtest/        replay engine and metrics
  learning/        journal and adaptive optimiser
  execution/       broker abstraction, MT5 and paper brokers, order manager
  integrations/    TradingView bridge
```

The analysis layer is pure — same inputs, same outputs, no I/O, no clocks.
That is what lets the backtester and the live engine share one code path.

---

## Limitations, stated plainly

- **Yahoo is delayed ~15 minutes.** Fine for monitoring and backtesting;
  unsuitable for M1-precision entries. Use MT5 for live execution.
- **Delta and open interest are proxies.** True order-flow delta needs bid/ask
  tick data that retail feeds rarely expose. `delta_proxy` signs volume by
  close position within the bar and is labelled as a proxy everywhere.
- **Probability is uncalibrated until the journal fills.** The fallback mapping
  is conservative on purpose.
- **Backtest fills are optimistic in one respect** — it assumes you were at the
  screen when the signal fired. Slippage beyond the configured spread
  allowance is not modelled.
- **Execution is opt-in and demo-first.** With `execution.enabled: false`
  (the default) the system places no orders at all, and signals are decision
  support for a human. Turning it on is a deliberate act with a demo-account
  gate in front of it.
- **A backtest is not a forecast.** Past performance in replay says nothing
  certain about tomorrow, and this system has not been validated live.
