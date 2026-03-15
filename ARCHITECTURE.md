# ORACLE — Narrative Arbitrage Engine
## System Architecture v1.0

**Mission:** Detect world events before Polymarket reprices them, simulate which narrative wins, predict the repricing direction and speed, and publish timestamped predictions that build a verifiable track record.

**Core Constraint:** $0 marginal cost per prediction. Everything runs on Claude Max subscription + free APIs.

---

## What Claude Max Actually Gives Us

| Capability | How We Use It |
|---|---|
| Claude Code (~225-900 messages/5hr) | The simulation brain. Runs swarm simulations, writes code, orchestrates everything. |
| Cowork (background tasks) | Event monitoring. Claude Desktop watches feeds and flags events while you do other work. |
| Web search (built into claude.ai) | Real-time data gathering. Current news, polls, Polymarket prices — no API needed. |
| Extended thinking | Deep narrative analysis on ambiguous events. |
| Google integrations | Read/write Google Docs for prediction archives, Sheets for scoreboard. |
| Claude.ai chat | Quick ad-hoc simulations when Claude Code is overkill. |

**Key insight:** Claude Max at $100-200/month replaces what would cost $500-3000/month in API calls. We never call the API directly. Every computation runs through Claude Code or Cowork.

---

## System Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║                        ORACLE SYSTEM                            ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  ┌────────────────────────────────────────────────────────────┐  ║
║  │              LAYER 1: THE NERVE CENTER                     │  ║
║  │                                                            │  ║
║  │   Automated Data Collectors (Python cron jobs)             │  ║
║  │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │  ║
║  │   │Polymarket│ │ Reddit   │ │  FRED    │ │  RSS/    │    │  ║
║  │   │WebSocket │ │ Velocity │ │ Economic │ │  News    │    │  ║
║  │   │ realtime │ │ Tracker  │ │ Releases │ │  Feeds   │    │  ║
║  │   └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │  ║
║  │        │             │            │             │          │  ║
║  │        └─────────────┴────────────┴─────────────┘          │  ║
║  │                          │                                 │  ║
║  │                    events.jsonl                             │  ║
║  │              (append-only event log)                        │  ║
║  └──────────────────────────┬─────────────────────────────────┘  ║
║                             │                                    ║
║                    ┌────────▼────────┐                           ║
║                    │  EVENT SCORER   │                           ║
║                    │  (Python)       │                           ║
║                    │                 │                           ║
║                    │ • Narrative     │                           ║
║                    │   ambiguity     │                           ║
║                    │ • Market impact │                           ║
║                    │ • Timing window │                           ║
║                    │ • Volume check  │                           ║
║                    └────────┬────────┘                           ║
║                             │                                    ║
║                    alert.json (if score > threshold)             ║
║                             │                                    ║
║  ┌──────────────────────────▼─────────────────────────────────┐  ║
║  │              LAYER 2: THE SIMULATION ENGINE                 │  ║
║  │              (Claude Code — Max subscription)               │  ║
║  │                                                            │  ║
║  │  Input: alert.json + seed data assembled by scripts        │  ║
║  │                                                            │  ║
║  │  ┌─────────────────────────────────────────────────────┐   │  ║
║  │  │  SIMULATION A: PUBLIC REACTION                      │   │  ║
║  │  │  20 agents simulating how the actual population     │   │  ║
║  │  │  interprets the event. Which narrative wins?        │   │  ║
║  │  │  Output: narrative_dominance_score, sentiment_shift  │   │  ║
║  │  └─────────────────────────────────────────────────────┘   │  ║
║  │                                                            │  ║
║  │  ┌─────────────────────────────────────────────────────┐   │  ║
║  │  │  SIMULATION B: TRADER REACTION                      │   │  ║
║  │  │  10 agents simulating how Polymarket traders        │   │  ║
║  │  │  will reprice. How fast? How far?                   │   │  ║
║  │  │  Output: predicted_price_move, timing_estimate      │   │  ║
║  │  └─────────────────────────────────────────────────────┘   │  ║
║  │                                                            │  ║
║  │  Output: prediction.json + full_simulation.md              │  ║
║  └──────────────────────────┬─────────────────────────────────┘  ║
║                             │                                    ║
║  ┌──────────────────────────▼─────────────────────────────────┐  ║
║  │              LAYER 3: PUBLISH & TRACK                       │  ║
║  │                                                            │  ║
║  │  ┌────────────┐  ┌─────────────┐  ┌────────────────────┐  │  ║
║  │  │ Twitter/X  │  │ Predictions │  │ Resolution Tracker │  │  ║
║  │  │ Auto-post  │  │ Archive     │  │ + Brier Scores     │  │  ║
║  │  │            │  │ (Git repo)  │  │ + Calibration DB   │  │  ║
║  │  └────────────┘  └─────────────┘  └────────────────────┘  │  ║
║  └────────────────────────────────────────────────────────────┘  ║
║                                                                  ║
║  ┌────────────────────────────────────────────────────────────┐  ║
║  │              LAYER 4: THE MEMORY (Calibration)             │  ║
║  │                                                            │  ║
║  │  After each resolution, stores:                            │  ║
║  │  • narrative_prediction vs narrative_actual                 │  ║
║  │  • repricing_speed_predicted vs repricing_speed_actual      │  ║
║  │  • magnitude_predicted vs magnitude_actual                  │  ║
║  │  • time_of_day, day_of_week, market_category               │  ║
║  │  • event_type, volume_at_prediction                         │  ║
║  │                                                            │  ║
║  │  Feeds back into simulation prompts as calibration context  │  ║
║  └────────────────────────────────────────────────────────────┘  ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Layer 1: The Nerve Center (Fully Automated)

This layer runs 24/7 on a VPS or your local machine. No Claude involved.
All data sources are free.

### 1A. Polymarket Real-Time Feed

```
Source: wss://ws-subscriptions-clob.polymarket.com/ws/
Backup: GET https://gamma-api.polymarket.com/markets (polling)
Cost: $0
Auth: None

What it captures:
- Price changes on all active markets (real-time via WebSocket)
- Volume spikes (sudden activity = something happened)
- New market creation (new events entering the platform)
- Spread widening (uncertainty increasing = narrative ambiguity)

Output: polymarket_feed.jsonl (append-only)
Each line: {timestamp, market_id, slug, question, price_before, 
           price_after, volume_delta, spread}
```

### 1B. Reddit Velocity Tracker

```
Source: https://www.reddit.com/r/{subreddit}/hot.json
Subreddits monitored:
  Political: politics, conservative, moderatepolitics, 
             PoliticalDiscussion, neutralpolitics
  Economic: economics, wallstreetbets, personalfinance, 
            investing, stocks
  Geopolitical: geopolitics, worldnews, foreignpolicy
  Crypto/Markets: polymarket, cryptocurrency
  MENA (your edge): arabs, iran, middleeast

Cost: $0 (public JSON, no auth, 60 req/min limit)

What it captures:
- Post velocity: how fast a post is gaining upvotes (not just score)
- Comment velocity: accelerating discussion = breaking awareness
- Cross-subreddit spread: same topic appearing in multiple subs
- Narrative framing: the title/framing that gets upvoted

Output: reddit_velocity.jsonl
Each line: {timestamp, subreddit, post_id, title, score, 
           score_velocity, comment_count, comment_velocity}
```

### 1C. FRED Economic Data Watcher

```
Source: https://api.stlouisfed.org/fred/
Cost: $0 (free API key)

Series monitored:
  UNRATE    — Unemployment rate
  CPIAUCSL  — CPI (inflation)
  UMCSENT   — Consumer sentiment
  FEDFUNDS  — Fed funds rate
  GDP       — Real GDP
  DCOILWTICO — WTI crude oil
  T10Y2Y   — Yield curve (recession signal)
  RSXFS    — Retail sales
  JTSJOL   — Job openings (JOLTS)
  
Polling: Every 15 min during market hours
Trigger: Any new data point that differs from consensus

Output: fred_releases.jsonl
```

### 1D. News RSS Aggregator

```
Sources (free RSS):
  AP News: https://rsshub.app/apnews/topics/apf-topnews
  Reuters: https://rsshub.app/reuters/world
  BBC: http://feeds.bbci.co.uk/news/world/rss.xml
  Al Jazeera: https://www.aljazeera.com/xml/rss/all.xml
  NPR: https://feeds.npr.org/1001/rss.xml
  
  MENA-specific (your structural edge):
  Al Jazeera Arabic: https://www.aljazeera.net/aljazeerarss/...
  Middle East Eye: RSS feed
  Iran International: RSS feed

Cost: $0
Polling: Every 5 minutes

Output: news_feed.jsonl
Each line: {timestamp, source, title, summary, url, language,
           detected_entities, sentiment_preliminary}
```

### 1E. Event Scorer (Python, runs continuously)

Watches all four feeds. When it detects a significant event, it scores it:

```python
EVENT_SCORE = (
    narrative_ambiguity    # 0-1: how many competing interpretations?
    × market_relevance     # 0-1: does it map to active Polymarket markets?
    × repricing_window     # 0-1: is the market currently thin/sleeping?
    × volume_potential     # 0-1: is the relevant market liquid enough?
)

TRIGGER if EVENT_SCORE > 0.4

Output: alert.json
{
  "event": "Fed Governor Waller signals support for rate pause",
  "source": "AP Wire",
  "timestamp": "2026-03-15T23:47:00Z",
  "event_score": 0.72,
  "narrative_ambiguity": 0.8,
  "competing_narratives": [
    "Economy too weak to raise → bearish → recession odds up",
    "Inflation under control → bullish → recession odds down"
  ],
  "affected_markets": [
    {"slug": "fed-decision-march", "current_price": 0.72, "volume": 1200000},
    {"slug": "us-recession-2026", "current_price": 0.31, "volume": 5400000}
  ],
  "repricing_window": "US traders sleeping, ~8 hours until full absorption",
  "seed_data_assembled": true,
  "seed_path": "seeds/fed-waller-rate-pause-2026-03-15.md"
}
```

When an alert fires, it also auto-assembles the seed document by pulling:
- The triggering news articles
- Current Reddit discussion on the topic
- Relevant FRED data
- Current Polymarket prices for affected markets
- Historical repricing data from calibration DB (if available)

This all happens automatically. No Claude involved yet.

---

## Layer 2: The Simulation Engine (Claude Code)

This is the ONLY part that requires your attention. Everything else is automated.

### How it works in practice:

Your phone buzzes with a Telegram/Discord alert:

```
🔴 ORACLE ALERT [Score: 0.72]
Event: "Fed Governor Waller signals rate pause"
Markets: Fed March (72¢), Recession 2026 (31¢)
Window: ~8 hours (US sleeping)
Seed ready: seeds/fed-waller-rate-pause-2026-03-15.md

Open Claude Code and run: oracle simulate
```

You open Claude Code (which is inside the oracle project directory):

```bash
$ cd ~/oracle
$ claude

> oracle simulate
```

Claude Code reads the alert.json, loads the seed document, and runs the dual simulation autonomously.

### The Master Simulation Prompt (loaded from prompts/oracle.md)

Claude Code executes this as a single comprehensive task:

**Phase A — Narrative Competition Simulation:**

Generate 20 public personas. For each one, determine which of the competing narratives they would adopt and amplify. Don't simulate "what happens" — simulate "which interpretation wins the first 48 hours of public discourse."

Track narrative adoption across 3 rounds:
- Round 1 (first 4 hours): Power users, journalists, political operatives
- Round 2 (4-12 hours): General public, Reddit communities, mainstream media
- Round 3 (12-48 hours): Casual consumers, poll respondents, low-info voters

Output: narrative dominance score (which interpretation wins and by how much)

**Phase B — Trader Repricing Simulation:**

Generate 10 Polymarket trader archetypes:
1. The Degen (trades on headlines, fast, emotional)
2. The Quant (waits for data, models probabilities)
3. The Political Junkie (deep context, medium speed)
4. The Whale (moves markets, waits for value)
5. The Arbitrageur (watches other markets for signals)
6. The Contrarian (fades the crowd)
7. The News Trader (buys the rumor, sells the news)
8. The Overnight Hunter (specifically trades thin markets)
9. The Momentum Follower (buys when price is moving)
10. The Fundamentalist (ignores noise, trades on data)

For each trader, determine: will they buy/sell/hold this market in the next 48 hours? At what price? When?

Output: predicted price trajectory with timing

**Phase C — The Prediction:**

```json
{
  "prediction_id": "ORACLE-2026-03-15-001",
  "timestamp": "2026-03-15T23:55:00Z",
  "event": "Fed Governor Waller signals rate pause",
  
  "markets": [
    {
      "slug": "fed-decision-march",
      "current_price": 0.72,
      "our_probability": 0.84,
      "direction": "BUY YES",
      "entry_price": 0.72,
      "target_price": 0.83,
      "target_timing": "10am ET tomorrow (10 hours)",
      "confidence": 0.74,
      "narrative_driver": "Narrative B wins (dovish = controlled inflation)",
      "narrative_dominance": 0.65
    }
  ],
  
  "repricing_model": {
    "absorption_25pct": "4 hours (Asian traders + overnight degens)",
    "absorption_50pct": "8 hours (early US morning)",
    "absorption_90pct": "18 hours (full US trading day)",
    "optimal_entry": "NOW",
    "optimal_exit": "tomorrow 2pm ET"
  },

  "risk_factors": [
    "Contradictory Fed speaker tomorrow morning could reverse narrative",
    "Low volume means a single whale could move the market against us"
  ],

  "tweet": "🔮 ORACLE #001\nFed March hold: market at 72¢, we see 84%.\nWaller's rate pause signal will be read as dovish by morning.\nBUY at 72¢ → target 83¢ by 2pm ET.\nNarrative B (inflation controlled) wins 65/35.\n#Polymarket"
}
```

Claude Code saves this to predictions/active/, and runs the publish script.

### Claude Code session budget per day:

| Task | Messages used | Time |
|---|---|---|
| Morning scan (review overnight alerts) | 5-10 | 5 min |
| Simulation #1 (if alert fired) | 15-25 | 10 min |
| Simulation #2 (if needed) | 15-25 | 10 min |
| Simulation #3 (if needed) | 15-25 | 10 min |
| Resolution check + scoreboard | 5-10 | 5 min |
| **Daily total** | **55-95 messages** | **~40 min** |

Max 5x gives you ~225 messages per 5-hour window. That's 2-4 full simulation sessions per window, with multiple windows per day. More than enough.

Max 20x at 900 messages would let you run 8-12 simulations per window — enough for every alert that fires.

---

## Layer 3: Publish & Track (Automated)

### Publishing pipeline:

```
prediction.json 
  → publish.py formats tweet text
  → Posts to Twitter/X via free API (1500 tweets/month)
  → Commits prediction to Git repo (public archive)
  → Sends to Telegram channel (optional, for subscribers)
  → Logs to Google Sheet via Claude's Google integration
```

### Resolution tracker:

```
Every 6 hours, track.py:
  → Queries Polymarket API for all active prediction markets
  → Checks if any resolved
  → If resolved:
    → Records actual outcome
    → Calculates Brier score
    → Calculates repricing accuracy (predicted vs actual timing)
    → Calculates narrative accuracy (predicted vs actual framing)
    → Updates calibration database
    → Moves to archive
    → Auto-posts resolution thread to Twitter/X
```

---

## Layer 4: The Memory (Calibration Engine)

This is the moat. After every resolved prediction, the system stores:

```json
{
  "prediction_id": "ORACLE-2026-03-15-001",
  "category": "economic_policy",
  "event_type": "fed_speaker_statement",
  "event_time_utc": "23:47",
  "day_of_week": "saturday",
  
  "narrative_prediction": "dovish_wins",
  "narrative_actual": "dovish_wins",
  "narrative_correct": true,
  "narrative_dominance_predicted": 0.65,
  "narrative_dominance_actual": 0.71,
  
  "price_at_prediction": 0.72,
  "price_at_target_time": 0.81,
  "price_at_resolution": 0.88,
  
  "repricing_25pct_predicted_hours": 4,
  "repricing_25pct_actual_hours": 3.2,
  "repricing_50pct_predicted_hours": 8,
  "repricing_50pct_actual_hours": 7.5,
  
  "brier_score": 0.042,
  "polymarket_brier_at_prediction": 0.078,
  "alpha": -0.036,
  
  "volume_at_prediction": 1200000,
  "volume_at_resolution": 2800000
}
```

After 30+ predictions, the calibration DB reveals patterns:

- "Economic markets reprice 30% slower than political markets"
- "Saturday night events have 40% larger repricing windows"
- "Narrative A wins 70% of the time when Fox News frames first"
- "Markets with <$500k volume are 2x more likely to be mispriced"
- "Our narrative predictions are 72% accurate on geopolitical markets but only 58% on economic markets"

These patterns get injected into future simulation prompts as priors, making each prediction more accurate than the last.

---

## Cowork Integration (Background Intelligence)

While you work on other things, Claude Desktop Cowork can run continuous background tasks:

**Task 1: Daily Market Brief (every morning)**
"Scan today's Polymarket landscape. Identify the 5 markets with the highest narrative ambiguity that resolve within 14 days. For each, draft a one-paragraph assessment of the current mispricing opportunity. Save to briefs/daily-2026-03-15.md"

**Task 2: Calibration Analysis (weekly)**
"Analyze the calibration database. Identify which market categories, event types, and time windows show the strongest prediction accuracy. Which show the weakest? Update the calibration summary. Suggest prompt modifications to improve weak categories."

**Task 3: Competitor Monitor (weekly)**  
"Search Twitter/X for other accounts making Polymarket predictions. Track their accuracy where visible. Identify what they're doing differently. Report findings."

---

## Infrastructure Cost Summary

| Component | Runs on | Cost |
|---|---|---|
| Claude Code simulations | Claude Max subscription | $100 or $200/month (already paying) |
| Cowork background tasks | Claude Max subscription | included |
| Data collection scripts | Local machine or $5/month VPS | $0-5/month |
| Polymarket API | Free | $0 |
| Reddit API | Free (public JSON) | $0 |
| FRED API | Free | $0 |
| RSS feeds | Free | $0 |
| Twitter/X posting | Free tier (1500/month) | $0 |
| Git repo (archive) | GitHub free | $0 |
| Google Sheets (scoreboard) | Free with Google account | $0 |
| Telegram channel | Free | $0 |
| Domain (optional) | oracle.signals or similar | $12/year |
| **Total additional cost** | | **$0-5/month** |

---

## File Structure

```
oracle/
├── ARCHITECTURE.md          ← this document
├── CLAUDE-CODE-PROMPT.md    ← the "oracle simulate" master prompt
├── .env                     ← API keys (FRED, Twitter, Telegram)
├── run.sh                   ← daily pipeline runner
│
├── nerve/                   ← Layer 1: automated data collection
│   ├── polymarket_ws.py     ← WebSocket price feed listener
│   ├── reddit_velocity.py   ← Reddit post/comment velocity tracker
│   ├── fred_watcher.py      ← FRED economic data release monitor
│   ├── news_rss.py          ← RSS aggregator (EN + AR sources)
│   ├── event_scorer.py      ← scores events, fires alerts
│   ├── seed_assembler.py    ← auto-builds seed docs from data
│   └── data/                ← raw data logs
│       ├── polymarket_feed.jsonl
│       ├── reddit_velocity.jsonl
│       ├── fred_releases.jsonl
│       ├── news_feed.jsonl
│       └── events.jsonl
│
├── prompts/                 ← Layer 2: simulation templates
│   ├── oracle.md            ← master simulation prompt
│   ├── narrative.md         ← narrative competition prompt
│   ├── trader.md            ← trader repricing prompt
│   └── personas/
│       ├── us-voters.md
│       ├── us-traders.md
│       ├── mena-analysts.md ← your structural edge
│       ├── economists.md
│       └── general-public.md
│
├── seeds/                   ← auto-generated per event
│   └── fed-waller-2026-03-15.md
│
├── predictions/
│   ├── active/              ← unresolved
│   ├── archive/             ← resolved with results
│   └── scoreboard.json
│
├── publish/                 ← Layer 3: distribution
│   ├── twitter.py
│   ├── telegram.py
│   └── templates/
│       ├── tweet.md
│       └── report.md
│
├── calibration/             ← Layer 4: the moat
│   ├── database.jsonl       ← every resolved prediction with metadata
│   ├── analysis.md          ← weekly calibration report
│   ├── repricing_model.json ← learned repricing speeds by category
│   ├── narrative_model.json ← learned narrative accuracy by type
│   └── prompt_versions/     ← versioned prompts with performance data
│       ├── v001.md
│       └── v002.md
│
└── docs/                    ← public-facing
    ├── methodology.md       ← how ORACLE works (for subscribers)
    ├── track-record.md      ← auto-updated from scoreboard
    └── faq.md
```

---

## Launch Sequence

### Week 1: Foundation
- Set up the oracle/ project directory
- Write and test the 4 data collectors (polymarket, reddit, fred, news)
- Write the event scorer
- Write the seed assembler
- Write the master simulation prompt
- Run first 3 manual predictions via Claude Code
- Post to Twitter/X with timestamps

### Week 2: Automation
- Set up cron jobs for data collection (every 5-15 min)
- Set up alert system (Telegram or Discord webhook)
- Write the resolution tracker
- Run 5-7 more predictions
- Start calibration database

### Week 3: Calibration
- Analyze first batch of resolved predictions
- Identify strongest/weakest market categories
- Tune simulation prompts based on calibration data
- Set up Cowork daily brief task
- Start tracking repricing speed data

### Week 4: Distribution
- Launch public Twitter/X presence with track record
- Create methodology page
- Set up Telegram channel for early followers
- Hit 20+ tracked predictions

### Month 2: Revenue
- If track record shows meaningful alpha (>5% better than market):
  - Launch paid Telegram/Discord tier ($49-99/month)
  - Paid subscribers get predictions 12-24 hours early
  - Free followers see predictions after market close
- Continue building calibration moat

### Month 3: Scale
- Build web dashboard (Next.js + your Three.js skills)
  - Live event feed on left
  - Polymarket prices in center  
  - ORACLE predictions on right
  - 3D globe visualization of events (your immersive-g.com skills)
- Raise price to $99-299/month for dashboard access
- API tier for trading bots ($299-999/month)

---

## The One Thing You Do Tomorrow

```bash
mkdir -p ~/oracle/{nerve,prompts,seeds,predictions/active,predictions/archive,publish,calibration,docs}
cd ~/oracle
claude

> Read this architecture document and build the complete Layer 1 
  data collection system. Start with the Polymarket feed, then 
  Reddit velocity tracker, then FRED watcher, then news RSS. 
  Test each one. Then build the event scorer and seed assembler. 
  Make sure everything runs and outputs to the right files.
  Go.
```

That's your Day 1. Claude Code builds all the infrastructure. Day 2, you run your first real simulation. Day 3, you publish your first prediction.

The calibration clock starts ticking the moment you publish prediction #1.
Every day you wait is a day of data you don't have.
