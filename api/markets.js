const SENTIMENT_KW = [
  "approval", "election", "ceasefire", "peace", "recession", "tariff",
  "fed", "iran", "ukraine", "trump", "midterm", "congress",
  "senate", "nato", "china", "taiwan", "oil", "inflation",
  "war", "nuclear", "sanction", "regime", "immigration", "border", "policy",
];

const SKIP_KW = [
  "nba", "nfl", "nhl", "mlb", "ufc", "mma", "premier league", "champions league",
  "temperature", "weather", "netflix", "spotify", "tiktok",
  "youtube", "esports", "cricket", "airdrop", "token launch",
];

export default async function handler(req, res) {
  try {
    const allMarkets = [];

    for (let offset = 0; offset < 200; offset += 50) {
      const url = `https://gamma-api.polymarket.com/markets?closed=false&limit=50&offset=${offset}&order=volume&ascending=false`;
      const r = await fetch(url, { signal: AbortSignal.timeout(15000) });
      if (!r.ok) break;
      const batch = await r.json();
      if (!batch || batch.length === 0) break;
      allMarkets.push(...batch);
    }

    const results = [];

    for (const m of allMarkets) {
      const q = (m.question || "").toLowerCase();
      if (SKIP_KW.some(k => q.includes(k))) continue;
      if (!SENTIMENT_KW.some(k => q.includes(k))) continue;

      let prices = [];
      try { prices = JSON.parse(m.outcomePrices || "[]"); } catch {}

      let outcomes = m.outcomes || "";
      if (typeof outcomes === "string") {
        try { outcomes = JSON.parse(outcomes); } catch { outcomes = outcomes.split(","); }
      }

      const outcomeData = [];
      for (let i = 0; i < outcomes.length; i++) {
        const name = (typeof outcomes[i] === "string" ? outcomes[i] : String(outcomes[i])).trim();
        const price = i < prices.length ? parseFloat(prices[i]) * 100 : 0;
        outcomeData.push({ name, price: Math.round(price * 10) / 10 });
      }

      const vol = parseFloat(m.volume || 0) || 0;
      results.push({
        question: m.question,
        outcomes: outcomeData,
        volume: vol,
        volume_fmt: vol >= 1e6 ? `$${(vol / 1e6).toFixed(1)}M` : `$${Math.round(vol / 1e3)}K`,
        slug: m.slug || "",
        url: `https://polymarket.com/market/${m.slug || ""}`,
      });
    }

    results.sort((a, b) => b.volume - a.volume);
    const top = results.slice(0, 30);

    res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
    res.status(200).json(top);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
