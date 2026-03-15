# ORACLE RUN SUMMARY — 2026-03-15

## Data Collected

### Polymarket (50+ markets scanned)
- 36 sentiment-driven markets identified
- Key cluster: US-Iran conflict (13 markets, $80M+ combined volume)
- Key cluster: Crude oil price targets (10 markets, $30M+ combined volume)
- Key cluster: Fed policy March 18 (4 markets, $399M volume — already resolved at 99.7% no change)
- Key cluster: Geopolitics (8 markets including Russia-Ukraine, Hungary, Greenland)

### Reddit (74 posts across 4 subreddits)
- 14 accelerating posts identified (velocity > 538 pts/hr)
- Dominant theme: US-Iran military escalation (7 of top 10 by velocity)
- Top post: "US attacks Iran's Kharg Island, Trump says" — 19,667 pts
- Fastest: "Israel running critically low on interceptors" — 3,143 pts/hr

### FRED Economic Data
- Unemployment: 4.4% (stable)
- CPI: 327.46 (rising — inflationary pressure accelerating)
- Consumer Sentiment: 56.4 (recovering but historically low)
- Fed Funds: 3.64% (paused after easing cycle)
- WTI Oil: $94.65 (**+17% in 4 days — the signal**)

### News (BBC + NPR, 25 headlines)
- Iran conflict dominates: strikes, warships, embassy evacuations
- Meta-story: prediction markets themselves under scrutiny ("gruesome war bets")
- Russia exploiting Iran war
- Cuba unrest, Bolsonaro hospitalized

## 3 Simulation Targets

| Rank | Target | Event Score | Market | Price |
|------|--------|-------------|--------|-------|
| **1** | **US-Iran Ceasefire March 31** | **0.727** | $8.57M vol | Yes 15% |
| 2 | Crude Oil $120 by March 31 | 0.452 | $3.11M vol | Yes 45% |
| 3 | Iranian Regime Fall before 2027 | 0.425 | $9.81M vol | Yes 38.5% |

## Prediction #001: US-Iran Ceasefire by March 31

**PRIMARY CALL: NO CEASEFIRE (confidence 78/100)**
- Polymarket: 15% Yes
- ORACLE estimate: 8% Yes
- Alpha: 7 percentage points (market overprices Yes)
- Trade: BUY NO at 85¢, target 88-90¢
- Timeframe: Hold to expiry or exit at 90¢+

**Narrative winner:** "No Ceasefire" — dominance 82/100
**Key driver:** Absence of diplomatic signals + economic pain = pessimism
**Trader consensus:** 11.5% fair value, 5:1 capital flow into No

## Tweet (280 chars)
```
ORACLE #001: US-Iran ceasefire by March 31?

Polymarket says 15%. We say 8%.

No diplomatic signals. No back-channels. "No quarter" rhetoric. Oil at $95. 16 days isn't enough.

BUY NO at 85¢. Target: 88-90¢.

https://polymarket.com/event/us-x-iran-ceasefire-by-march-31
```

## Tomorrow
Run `./run.sh full` again to:
1. Check if ceasefire Yes has moved toward our 12-13% target
2. Scan for new events (Fed decision March 18 could be a catalyst)
3. Check oil price trajectory (if $100+ breached, run crude oil simulation)
4. Monitor for diplomatic signals that would invalidate the prediction
5. Consider running simulations on Target #2 (crude oil) and Target #3 (regime fall)

## Files Generated
- `seeds/seed_iran_ceasefire_march31.md` — Seed document for Target 1
- `seeds/seed_crude_oil_120.md` — Seed document for Target 2
- `seeds/seed_iran_regime_2027.md` — Seed document for Target 3
- `alerts/event_scores_20260315.json` — Event scores JSON
- `predictions/active/ORACLE_001_iran_ceasefire_march31.md` — Full prediction
- `predictions/active/ORACLE_001_simulation_A_narrative.md` — 20-persona simulation
- `predictions/active/ORACLE_001_simulation_B_traders.md` — 10-trader simulation
- `predictions/active/ORACLE_001_summary.md` — This file
