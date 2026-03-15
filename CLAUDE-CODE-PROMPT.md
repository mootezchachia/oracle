# ORACLE — Day 1 Build Prompt
# Paste this into Claude Code from inside the ~/oracle directory

Read the ARCHITECTURE.md file in this directory for full context.

You are building ORACLE — a narrative arbitrage engine for Polymarket prediction markets.

## YOUR TASK: Build the entire Layer 1 (Nerve Center) + the simulation system

Build everything below. Test each component. Do not ask me anything — just build and verify.

### 1. Project Setup
- Create the full directory structure from ARCHITECTURE.md
- Create requirements.txt with all needed Python packages
- Install dependencies
- Create .env.example with placeholder keys

### 2. Polymarket Feed (nerve/polymarket_ws.py)
- Connect to Polymarket Gamma API: GET https://gamma-api.polymarket.com/markets
- Poll every 60 seconds (WebSocket is ideal but polling works for v1)
- Filter for active, non-closed markets
- Track price changes between polls
- Log to data/polymarket_feed.jsonl
- Test it: run it for 2 minutes, show me what it captures

### 3. Reddit Velocity Tracker (nerve/reddit_velocity.py)
- Monitor these subreddits: politics, conservative, worldnews, economics, geopolitics, wallstreetbets
- Use public .json endpoints (no auth needed): https://www.reddit.com/r/{sub}/hot.json
- For each post, calculate velocity: track score over time between polls
- Detect "acceleration" — posts gaining upvotes faster than subreddit average
- Log to data/reddit_velocity.jsonl
- Test it: run one cycle, show top 5 accelerating posts

### 4. FRED Economic Watcher (nerve/fred_watcher.py)
- Use FRED API with DEMO_KEY (or env var FRED_API_KEY)
- Monitor: UNRATE, CPIAUCSL, UMCSENT, FEDFUNDS, GDP, DCOILWTICO, T10Y2Y
- Check for new data releases every 15 minutes
- Log to data/fred_releases.jsonl
- Test it: fetch current values for all series

### 5. News RSS Aggregator (nerve/news_rss.py)
- Parse RSS feeds: AP, Reuters, BBC, Al Jazeera (English + Arabic)
- Use feedparser library
- Extract: title, summary, publish time, source, detected entities
- Dedup by title similarity (don't log the same story twice)
- Log to data/news_feed.jsonl
- Test it: fetch current headlines from all feeds

### 6. Event Scorer (nerve/event_scorer.py)
- Watch all 4 data logs for significant events
- Score each event on:
  - narrative_ambiguity (0-1): can this event be interpreted multiple ways?
  - market_relevance (0-1): does it map to active Polymarket markets?
  - repricing_window (0-1): is the market currently thin? are traders sleeping?
  - volume_potential (0-1): is the relevant market liquid enough to matter?
- EVENT_SCORE = narrative_ambiguity × market_relevance × repricing_window × volume_potential
- If EVENT_SCORE > 0.4, write alert to alerts/
- Include affected Polymarket markets with current prices
- Include competing narrative interpretations

### 7. Seed Assembler (nerve/seed_assembler.py)
- When an alert fires, auto-assemble a seed document:
  - The triggering event details
  - Current Polymarket prices for affected markets
  - Top Reddit posts and comments on the topic
  - Relevant FRED economic data
  - Recent news headlines on the topic
- Save to seeds/ as markdown file
- Make it comprehensive enough to paste directly into a simulation

### 8. Master Simulation Prompt (prompts/oracle.md)
- Create the full dual-simulation prompt:
  - Simulation A: 20 public personas, narrative competition, 3 rounds
  - Simulation B: 10 trader archetypes, repricing prediction
  - Output format: structured JSON prediction with narrative dominance, price target, timing, confidence, tweet text
- Include persona generation instructions
- Include the realism rules (most people don't shift, intensity changes before direction, economic concerns move swing voters more than cultural ones)

### 9. Resolution Tracker (scripts/track.py)
- Check Polymarket API for resolved markets
- Calculate Brier scores
- Track narrative accuracy and repricing timing accuracy
- Maintain scoreboard.json
- Support registering new predictions and checking resolutions

### 10. Daily Runner (run.sh)
- Single command that:
  - Checks for new alerts
  - Shows today's best targets
  - Checks for resolved predictions
  - Prints scoreboard
  - Tells me what to do next

### 11. Quick Test
After building everything, run the full pipeline once:
- Fetch current Polymarket markets
- Fetch current Reddit posts  
- Fetch FRED data
- Identify the single best market to simulate right now
- Assemble a seed document for it
- Print a summary of what you built and what's ready

DO NOT ask me questions. Make all decisions yourself. 
If an API call fails, work around it.
If you're unsure about something, pick the simpler option.
Build everything, test everything, report results.

Go.
