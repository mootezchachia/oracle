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
    const portfolioPath = join(process.cwd(), 'nerve', 'data', 'strategy_100_portfolio.json');

    if (!existsSync(portfolioPath)) {
      return res.status(200).json({
        account: { starting_balance: 100, total_cash: 100 },
        allocations: {
          bonds: { budget: 60, cash: 60, invested: 0 },
          expertise: { budget: 30, cash: 30, invested: 0 },
          flash_crash: { budget: 10, cash: 10, invested: 0 },
        },
        trades: [],
        stats: { total_trades: 0, wins: 0, losses: 0, total_pnl: 0 },
        initialized: false,
      });
    }

    const portfolio = JSON.parse(readFileSync(portfolioPath, 'utf8'));
    const openTrades = portfolio.trades.filter(t => t.status === "open");

    // Fetch live prices for open positions
    const results = await Promise.allSettled(
      openTrades.map(trade =>
        fetch(`https://gamma-api.polymarket.com/markets?slug=${trade.slug}`)
          .then(r => r.ok ? r.json() : [])
      )
    );

    let total_invested = 0;
    let positions_value = 0;

    const livePositions = openTrades.map((trade, i) => {
      const result = results[i];
      let current_price = null;
      let current_value = null;
      let pnl = null;
      let pnl_pct = null;

      if (result.status === "fulfilled" && result.value.length > 0) {
        try {
          const prices = JSON.parse(result.value[0].outcomePrices);
          const yes_price = parseFloat(prices[0]);
          const no_price = parseFloat(prices[1]);
          current_price = trade.side === "yes" ? yes_price : no_price;
          current_value = parseFloat((trade.shares * current_price).toFixed(2));
          pnl = parseFloat((current_value - trade.invested).toFixed(2));
          pnl_pct = parseFloat(((current_value / trade.invested - 1) * 100).toFixed(2));
          total_invested += trade.invested;
          positions_value += current_value;
        } catch (e) {}
      }

      return { ...trade, current_price, current_value, pnl, pnl_pct };
    });

    const closedTrades = portfolio.trades.filter(t => t.status === "closed");
    const total_value = portfolio.account.total_cash + positions_value;
    const total_return = total_value - portfolio.account.starting_balance;

    return res.status(200).json({
      account: {
        ...portfolio.account,
        positions_value: parseFloat(positions_value.toFixed(2)),
        total_value: parseFloat(total_value.toFixed(2)),
        total_return: parseFloat(total_return.toFixed(2)),
        total_return_pct: parseFloat(((total_return / portfolio.account.starting_balance) * 100).toFixed(2)),
      },
      allocations: portfolio.allocations,
      positions: livePositions,
      closed_trades: closedTrades,
      stats: portfolio.stats,
      updated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: "Failed to fetch strategy portfolio", detail: err.message });
  }
}
