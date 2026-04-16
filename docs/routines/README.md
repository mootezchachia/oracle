## ORACLE Claude Code Routines

Two routines for use with Claude Code's `/routine` feature (launched April 2026).
Routines run on Anthropic's managed cloud infrastructure, persist across sessions,
and can commit / open PRs autonomously.

- `weekly-postmortem.md` — Mondays 08:00 UTC. Analyzes last week's trades, opens PR with strategy tweaks.
- `nightly-alert-escalation.md` — Daily 23:00 UTC. Finds stale AI alerts and opens PRs that fix the underlying strategy.

### How to install

1. Go to claude.ai/code/routines
2. Click "New routine"
3. Set schedule (Cron expression)
4. Paste the prompt from the corresponding `.md` file (everything below the `---` line)
5. Select this repo (`mootezchachia/oracle`) as the working directory
6. Grant tools: Bash, Read, Edit, Write, Grep, Glob, Git

### Why these (vs QStash cron)

QStash can only call static serverless endpoints. Routines run full Claude Code
with repo access, so they can:
- Read the actual code that wrote a trade
- Reason about *why* a trade failed
- Edit the code and open a PR
- Cross-reference news, Redis state, and git history together

Use QStash for fast polling + mechanical actions. Use routines for slow,
reasoning-heavy work that needs code changes.
