# ORACLE Notification System

## Topic
Default ntfy.sh topic (override via `NTFY_TOPIC` env var or Redis key `oracle:config:ntfy_topic`):

```
oracle-188d7e28a2af1544
```

Subscribe in the ntfy app (iOS/Android). No account needed.

## What triggers a push

| Event | Source | Severity | Bypasses quiet hours |
|---|---|---|---|
| Trade opened/closed | `strategy100-run` every 4h | `trade` or `alert` or `urgent` | Only if P&L ≥ $100 |
| New AI alert | daily 7:30 UTC `?action=ai-analyze` | `alert` | Yes |
| Top opportunity (edge ≥ 5%) | daily 7:30 UTC | `info` | No |
| Morning digest | 08:00 UTC `?action=daily-digest` | `info` | N/A (sent in morning) |
| Risk alert (drawdown/concentration) | `?action=risk-check` | `alert` or `urgent` | Yes |
| Pipeline health problem | `?action=health-ping` | `alert` | Yes |
| Manual close/pause/resume ack | ntfy action button | `high` | Yes |

## Quiet hours
Default: **00:00–07:00 UTC**. Non-urgent notifications are queued to Redis key `oracle:ntfy:queue` and flushed by the morning digest.

Override:
```bash
# Disable quiet hours
redis SET oracle:config:quiet_hours '{"enabled":false}'

# Custom window (22:00 UTC through 06:00 UTC)
redis SET oracle:config:quiet_hours '{"start":22,"end":6}'
```

## Kill switch
Redis key `oracle:config:trading_enabled` (default `true`).

- When `false`: `strategy100-run` skips opening new trades; exits still run.
- Toggle via ntfy "Pause trading" / "Resume trading" action buttons.

## Action buttons
Every actionable notification includes tap-to-act buttons. URLs are HMAC-signed (day-scoped, 24h TTL).

| Command | Effect |
|---|---|
| `close:<trade_id>` | Force-close an open position at current market price |
| `snooze:<alert_key>` | Suppress an alert for 24h |
| `pause` | Flip kill switch OFF |
| `resume` | Flip kill switch ON |
| `status` | Send portfolio summary as a new notification |

HMAC secret is read from `NTFY_ACTION_SECRET` env var, falling back to a hardcoded default. Rotate it if the topic leaks.

## QStash schedules to add

These endpoints exist but need QStash crons wired to call them on a schedule:

```
# Morning digest (08:00 UTC daily)
POST https://oracle-psi-orpin.vercel.app/api/strategy100-run?action=daily-digest
Cron: 0 8 * * *

# Silent heartbeat (every 12h)
GET  https://oracle-psi-orpin.vercel.app/api/strategy100-run?action=health-ping
Cron: 0 */12 * * *

# Risk check (every 6h)
GET  https://oracle-psi-orpin.vercel.app/api/strategy100-run?action=risk-check
Cron: 0 */6 * * *
```

Existing schedules remain unchanged.
