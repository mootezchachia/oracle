# ORACLE Quick Check

Fast portfolio health check and opportunity scan. Use this for quick daily monitoring.

## Do this:

1. Fetch in parallel using curl (from https://oracle-psi-orpin.vercel.app):
   - `/api/strategy100` (strategy portfolio)
   - `/api/portfolio` (main portfolio)
   - `/api/strategy100?view=forecast` (agent forecasts)

2. Show a compact summary:
   - Both portfolio values and daily P&L
   - Any positions with P&L worse than -10% (flag as warnings)
   - Top 3 forecast opportunities with edge > 3%
   - Any positions expiring within 7 days

3. If any urgent issues found, recommend specific actions.

Keep output under 30 lines. Be direct.
