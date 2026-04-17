# ORACLE — Vercel Deployment Setup

## 1. Create Upstash Redis (free, 30 seconds)

1. Go to [console.upstash.com](https://console.upstash.com)
2. Create a new Redis database (any region)
3. Copy **REST URL** and **REST Token**

## 2. Add Environment Variables in Vercel

Go to your Vercel project → Settings → Environment Variables → add:

| Variable | Value |
|----------|-------|
| `UPSTASH_REDIS_REST_URL` | `https://your-db.upstash.io` |
| `UPSTASH_REDIS_REST_TOKEN` | `AXxx...your-token` |

## 3. Deploy

Push to your branch. Vercel auto-deploys.

## 4. Initialize Portfolio

Hit this URL once to start trading:
```
https://your-app.vercel.app/api/strategy100-run
```

To reset portfolio:
```
https://your-app.vercel.app/api/strategy100-run?reset=1
```

## How It Works

- **Vercel Cron** hits `/api/strategy100-run` every 6 hours (Hobby plan limit)
- Each run: scans Polymarket → finds bonds/expertise/crash opportunities → executes → checks TP/SL
- Portfolio state persists in Upstash Redis
- Dashboard reads from `/api/strategy100` (Redis → live Polymarket prices)

## Want More Frequent Runs?

Vercel Hobby = 2 cron jobs, 1x/day minimum. For every-5-minute trading:

**Option A: Upstash QStash (free, 500 msgs/day)**
1. Go to [console.upstash.com/qstash](https://console.upstash.com/qstash)
2. Create a schedule: `*/5 * * * *` → `https://your-app.vercel.app/api/strategy100-run`

**Option B: GitHub Actions (free)**
Add `.github/workflows/oracle-cron.yml`:
```yaml
name: ORACLE Strategy Runner
on:
  schedule:
    - cron: '*/10 * * * *'
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: curl -s https://your-app.vercel.app/api/strategy100-run
```

**Option C: Vercel Pro ($20/mo)**
Unlimited cron jobs, any schedule.
