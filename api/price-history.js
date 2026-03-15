const TRACKED_MARKETS = {
  "us-x-iran-ceasefire-by-march-31": { oracle: 8, label: "#001 Iran Ceasefire", direction: "NO" },
  "will-crude-oil-cl-hit-high-120-by-end-of-march-766-813-597": { oracle: 52, label: "#002 Oil $120", direction: "YES" },
};

async function fetchMarketPrice(slug) {
  try {
    const url = `https://gamma-api.polymarket.com/markets?slug=${encodeURIComponent(slug)}`;
    const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!r.ok) return null;

    const markets = await r.json();
    const m = Array.isArray(markets) ? markets[0] : markets;
    if (!m) return null;

    let prices = [];
    try { prices = JSON.parse(m.outcomePrices || "[]"); } catch {}

    let outcomes = m.outcomes || "";
    if (typeof outcomes === "string") {
      try { outcomes = JSON.parse(outcomes); } catch { outcomes = []; }
    }

    let yesPrice = null;
    if (Array.isArray(outcomes)) {
      for (let i = 0; i < outcomes.length; i++) {
        const name = (typeof outcomes[i] === "string" ? outcomes[i] : "").trim().toLowerCase();
        if (name === "yes" && i < prices.length) {
          yesPrice = Math.round(parseFloat(prices[i]) * 1000) / 10;
          break;
        }
      }
    }
    if (yesPrice === null && prices.length > 0) {
      yesPrice = Math.round(parseFloat(prices[0]) * 1000) / 10;
    }

    return yesPrice;
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  try {
    const result = {};
    const now = Date.now() / 1000;

    const entries = Object.entries(TRACKED_MARKETS);
    const fetches = entries.map(async ([slug, info]) => {
      const price = await fetchMarketPrice(slug);
      const history = [];
      if (price !== null) {
        history.push({ ts: now, price });
      }
      return [slug, {
        label: info.label,
        oracle: info.oracle,
        direction: info.direction,
        history,
      }];
    });

    const pairs = await Promise.all(fetches);
    for (const [slug, data] of pairs) {
      result[slug] = data;
    }

    res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
    res.status(200).json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
