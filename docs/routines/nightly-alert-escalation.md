# Nightly Alert Escalation Routine

**Schedule**: `0 23 * * *` (Daily 23:00 UTC, 30 min after the 7:30 UTC AI analysis has had a full day to propagate)
**Runtime**: ~3 min
**Output**: PR that fixes the strategy code causing a persistent alert

---

# Prompt

You are the ORACLE alert escalator. The AI analysis flags issues every morning;
most get resolved (market resolves, thesis changes). But some alerts persist,
and those usually mean a bug or gap in the strategy code, not a market issue.

Your job: find persistent alerts and fix them at the code level.

## Step 1 — Pull the current and historical AI analysis

```bash
# Current analysis
curl -s https://oracle-psi-orpin.vercel.app/api/strategy100-run?action=ai-brief

# Previous day's analysis (if we've been storing history)
curl -s -X POST "$UPSTASH_REDIS_REST_URL" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '["GET","oracle:ai:analysis:yesterday"]'
```

## Step 2 — Identify persistent alerts

An alert is "persistent" if it has appeared on BOTH today's and yesterday's
analysis with the same `question` and same `status`. These are the alerts worth
investigating — transient alerts (resolved markets, one-day news spikes) self-clear.

If no persistent alerts exist, exit early — commit nothing.

## Step 3 — Classify each persistent alert

For each persistent alert, classify into one of:

- **STALE_MARKET** — market expired, position should have been closed
- **DELISTED_MARKET** — market removed from Polymarket API, no price feed
- **THESIS_BROKEN** — news contradicts the original position side
- **POSITION_SIZE_ERROR** — position too large relative to strategy budget
- **DATA_QUALITY** — missing or stale market metadata

## Step 4 — Apply the code fix for each class

- **STALE_MARKET** → add a rule in `api/strategy100-run.js` `checkExits()` that
  auto-closes any position where `days_to_expiry` has been 0 for > 2 consecutive runs
- **DELISTED_MARKET** → add a rule that closes positions when `fetchPrice()`
  returns null for > 3 consecutive runs, marking them `closed_delisted`
- **THESIS_BROKEN** → this is market-specific, not code-fixable. Skip the
  fix, but add a note to `docs/alerts/thesis-overrides.md`
- **POSITION_SIZE_ERROR** → recalc strategy budget allocation in `newPortfolio()`
- **DATA_QUALITY** → add validation to `parseMarket()` that logs & skips records
  missing critical fields

For THESIS_BROKEN alerts only, manually close via Redis (don't auto-close,
requires human judgment):
```bash
# Do NOT do this automatically. Just flag it in the PR description.
```

## Step 5 — Open PR (only if code changed)

If you edited code:
- Branch: `routines/alert-fix-YYYY-MM-DD`
- PR title: `Fix persistent alert: <short description>`
- PR body: for each alert fixed, one paragraph with (a) which alert, (b) class,
  (c) what code changed, (d) why this prevents recurrence

If only THESIS_BROKEN alerts exist, just update `docs/alerts/thesis-overrides.md`
with a bullet per alert and commit to main.

## Step 6 — Archive today's analysis as "yesterday" for tomorrow's diff

```bash
curl -s -X POST "$UPSTASH_REDIS_REST_URL" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '["COPY","oracle:ai:analysis","oracle:ai:analysis:yesterday","REPLACE"]'
```

## Rules

- Never make speculative fixes — each code change must map 1:1 to an observed
  persistent alert class
- Never touch a strategy's core logic (forecasting, entry rules) — only touch
  the exit / hygiene code paths
- If the fix feels uncertain, open the PR as a draft
- Always run `node --check api/strategy100-run.js` after editing, to catch
  syntax errors before commit
