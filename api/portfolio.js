const TRADES = [
  { id: 1, number: 1, slug: "us-x-iran-ceasefire-by-march-31", question: "US x Iran ceasefire by March 31?", side: "no", entry_price: 0.86, shares: 581.40, invested: 500, date: "2026-03-15" },
  { id: 2, number: 2, slug: "will-crude-oil-cl-hit-high-120-by-end-of-march-766-813-597", question: "Oil $120 by end of March?", side: "yes", entry_price: 0.411, shares: 1217.11, invested: 500, date: "2026-03-15" },
  { id: 3, number: 3, slug: "us-x-iran-ceasefire-by-may-31-313", question: "Iran ceasefire by May 31?", side: "no", entry_price: 0.51, shares: 980.39, invested: 500, date: "2026-03-15" },
  { id: 4, number: 4, slug: "will-crude-oil-cl-hit-high-140-by-end-of-march-934-621", question: "Oil $140 by end of March?", side: "no", entry_price: 0.80, shares: 625.00, invested: 500, date: "2026-03-15" },
  { id: 5, number: 5, slug: "will-the-iranian-regime-fall-by-the-end-of-2026", question: "Iranian regime fall before 2027?", side: "no", entry_price: 0.63, shares: 793.65, invested: 500, date: "2026-03-15" },
  { id: 6, number: 6, slug: "us-x-iran-ceasefire-by-june-30-752", question: "Iran ceasefire by June 30?", side: "no", entry_price: 0.416, shares: 1201.70, invested: 500, date: "2026-03-15" },
  { id: 7, number: 7, slug: "will-russia-enter-druzkhivka-by-june-30-933-897", question: "Russia enter Druzhkivka by June 30?", side: "no", entry_price: 0.76, shares: 658.29, invested: 500, date: "2026-03-15" },
];

const CASH = 6500;
const STARTING_BALANCE = 10000;

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=120");

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  try {
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
          // outcomePrices parse failed, leave as null
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
      positions,
      updated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: "Failed to fetch portfolio", detail: err.message });
  }
}
