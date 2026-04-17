/**
 * /api/crypto — Live cryptocurrency data
 *
 * Fetches real-time prices from CoinGecko (free, no key needed).
 * Returns current prices, 24h change, and sparkline data.
 */

const COINS = ["bitcoin", "ethereum", "solana", "dogecoin", "cardano", "ripple"];

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");

  try {
    // Fetch current prices + 24h change + 7d sparkline
    const url = `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=${COINS.join(",")}&order=market_cap_desc&sparkline=true&price_change_percentage=1h,24h,7d`;

    const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!r.ok) throw new Error(`CoinGecko API ${r.status}`);

    const coins = await r.json();

    const prices = coins.map((c) => ({
      id: c.id,
      symbol: c.symbol.toUpperCase(),
      name: c.name,
      price: c.current_price,
      change_1h: c.price_change_percentage_1h_in_currency,
      change_24h: c.price_change_percentage_24h_in_currency,
      change_7d: c.price_change_percentage_7d_in_currency,
      high_24h: c.high_24h,
      low_24h: c.low_24h,
      market_cap: c.market_cap,
      volume_24h: c.total_volume,
      sparkline: c.sparkline_in_7d?.price?.slice(-48) || [], // last 2 days of 7d hourly
    }));

    res.status(200).json({
      prices,
      updated: new Date().toISOString(),
    });
  } catch (err) {
    // Return empty but valid response on error
    res.status(200).json({
      prices: [],
      updated: new Date().toISOString(),
      error: err.message,
    });
  }
}
