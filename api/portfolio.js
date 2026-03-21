import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=120");

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  try {
    // Read portfolio from shared data file (written by executor + manual)
    const portfolioPath = join(process.cwd(), 'nerve', 'data', 'virtual_portfolio.json');
    let portfolio;

    if (existsSync(portfolioPath)) {
      portfolio = JSON.parse(readFileSync(portfolioPath, 'utf8'));
    } else {
      return res.status(200).json({
        account: { starting_balance: 10000, cash: 10000, positions_value: 0, total_value: 10000, pnl: 0, pnl_pct: 0 },
        positions: [],
        updated_at: new Date().toISOString(),
      });
    }

    const TRADES = portfolio.trades.filter(t => t.status === "open" || !t.status);
    const CASH = portfolio.account.cash;
    const STARTING_BALANCE = portfolio.account.starting_balance;

    // Fetch all market prices in parallel
    const results = await Promise.allSettled(
      TRADES.map((trade) =>
        fetch(`https://gamma-api.polymarket.com/markets?slug=${trade.slug}`)
          .then((r) => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          })
      )
    );

    const positions = TRADES.map((trade, i) => {
      const result = results[i];
      let market_yes = null;
      let market_no = null;
      let current_price = null;
      let current_value = null;
      let pnl = null;
      let pnl_pct = null;
      let status = "unknown";
      let live = false;

      if (result.status === "fulfilled" && Array.isArray(result.value) && result.value.length > 0) {
        const market = result.value[0];
        try {
          const prices = JSON.parse(market.outcomePrices);
          market_yes = parseFloat(prices[0]);
          market_no = parseFloat(prices[1]);
          current_price = trade.side === "yes" ? market_yes : market_no;
          current_value = parseFloat((trade.shares * current_price).toFixed(2));
          pnl = parseFloat((current_value - trade.invested).toFixed(2));
          pnl_pct = parseFloat(((current_value / trade.invested - 1) * 100).toFixed(2));
          status = pnl > 0.5 ? "winning" : pnl < -0.5 ? "losing" : "flat";
          live = true;
        } catch (e) {
          // outcomePrices parse failed
        }
      }

      return {
        ...trade,
        current_price,
        current_value,
        pnl,
        pnl_pct,
        status,
        market_yes,
        market_no,
        live,
      };
    });

    // Also include closed trades for history
    const closedTrades = (portfolio.trades || []).filter(t => t.status === "closed").map(t => ({
      ...t,
      live: false,
    }));

    const livePositions = positions.filter((p) => p.live);
    const total_invested = livePositions.reduce((s, p) => s + p.invested, 0);
    const positions_value = parseFloat(livePositions.reduce((s, p) => s + p.current_value, 0).toFixed(2));
    const total_pnl = parseFloat((positions_value - total_invested).toFixed(2));
    const total_pnl_pct = total_invested > 0 ? parseFloat(((positions_value / total_invested - 1) * 100).toFixed(2)) : 0;
    const account_value = parseFloat((CASH + positions_value).toFixed(2));

    return res.status(200).json({
      account: {
        starting_balance: STARTING_BALANCE,
        cash: CASH,
        positions_value,
        total_value: account_value,
        pnl: total_pnl,
        pnl_pct: total_pnl_pct,
      },
      positions: [...positions, ...closedTrades],
      trade_count: portfolio.trades.length,
      updated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: "Failed to fetch portfolio", detail: err.message });
  }
}
