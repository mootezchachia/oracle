import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

export default async function handler(req, res) {
  try {
    const dataDir = join(process.cwd(), 'nerve', 'data');

    // Read cached candles
    const candlePath = join(dataDir, 'candle_cache.json');
    let candles = {};
    if (existsSync(candlePath)) {
      candles = JSON.parse(readFileSync(candlePath, 'utf8'));
    }

    // Read price feed
    const pricePath = join(dataDir, 'price_feed.jsonl');
    let prices = [];
    if (existsSync(pricePath)) {
      const lines = readFileSync(pricePath, 'utf8').trim().split('\n').filter(Boolean);
      prices = lines.slice(-20).map(l => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    }

    // Read 15m market cache
    const marketsPath = join(dataDir, 'crypto_15m_markets.json');
    let markets = null;
    if (existsSync(marketsPath)) {
      markets = JSON.parse(readFileSync(marketsPath, 'utf8'));
    }

    res.setHeader('Cache-Control', 's-maxage=15, stale-while-revalidate=30');
    res.status(200).json({
      candles,
      prices,
      markets,
      updated: new Date().toISOString(),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
