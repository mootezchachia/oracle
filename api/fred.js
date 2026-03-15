const FRED_SERIES = {
  UNRATE: "Unemployment Rate",
  CPIAUCSL: "Consumer Price Index",
  UMCSENT: "Consumer Sentiment",
  FEDFUNDS: "Federal Funds Rate",
  DCOILWTICO: "WTI Crude Oil",
  T10Y2Y: "Yield Curve (10Y-2Y)",
};

export default async function handler(req, res) {
  const apiKey = process.env.FRED_API_KEY || "DEMO_KEY";

  try {
    const entries = Object.entries(FRED_SERIES);
    const fetches = entries.map(async ([seriesId, name]) => {
      try {
        const url = `https://api.stlouisfed.org/fred/series/observations?series_id=${seriesId}&api_key=${apiKey}&file_type=json&sort_order=desc&limit=5`;
        const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
        if (!r.ok) return null;
        const data = await r.json();
        const obs = data.observations || [];
        if (!obs.length) return null;

        const latest = obs[0];
        const prev = obs.length > 1 ? obs[1] : null;
        const val = latest.value;
        const prevVal = prev ? prev.value : ".";

        let v, pv, direction, change;
        try {
          v = parseFloat(val);
          pv = prevVal !== "." ? parseFloat(prevVal) : v;
          direction = v > pv ? "up" : v < pv ? "down" : "flat";
          change = Math.round((v - pv) * 1000) / 1000;
        } catch {
          v = val;
          pv = prevVal;
          direction = "flat";
          change = 0;
        }

        return {
          id: seriesId,
          name,
          value: v,
          prev_value: pv,
          date: latest.date || "",
          direction,
          change,
        };
      } catch {
        return null;
      }
    });

    const results = (await Promise.all(fetches)).filter(Boolean);

    res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
    res.status(200).json(results);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
