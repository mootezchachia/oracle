/**
 * /api/price-history — Accumulating price history (Redis-backed)
 *
 * Fetches live Polymarket prices for tracked markets,
 * appends each snapshot to Redis, and returns the full history.
 * History grows autonomously every time the endpoint is called (~60s polling).
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';

const HISTORY_KEY = "oracle:price_history";
const MAX_POINTS = 500; // per market, ~8 hours at 60s polling

const TRACKED_MARKETS = {
  "us-x-iran-ceasefire-by-march-31": { oracle: 8, label: "#001 Iran Ceasefire Mar", direction: "NO" },
  "us-x-iran-ceasefire-by-may-31-313": { oracle: 15, label: "#003 Iran Ceasefire May", direction: "NO" },
  "us-x-iran-ceasefire-by-june-30-752": { oracle: 20, label: "#006 Iran Ceasefire Jun", direction: "NO" },
  "will-the-iranian-regime-fall-by-the-end-of-2026": { oracle: 12, label: "#005 Iranian Regime", direction: "NO" },
  "will-russia-enter-druzkhivka-by-june-30-933-897": { oracle: 18, label: "#007 Russia Druzhkivka", direction: "NO" },
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
  res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=120");

  try {
    const now = Math.floor(Date.now() / 1000);

    // Load existing history from Redis
    let history = {};
    if (isRedisConfigured()) {
      history = (await redisGet(HISTORY_KEY)) || {};
    }

    // Fetch all current prices
    const entries = Object.entries(TRACKED_MARKETS);
    const fetches = await Promise.all(
      entries.map(async ([slug, info]) => {
        const price = await fetchMarketPrice(slug);
        return [slug, info, price];
      })
    );

    // Build result and accumulate history
    const result = {};
    for (const [slug, info, price] of fetches) {
      // Init history array for this market if needed
      if (!history[slug]) history[slug] = [];

      // Append new price point (avoid duplicates within 30s)
      if (price !== null) {
        const last = history[slug][history[slug].length - 1];
        if (!last || now - last.ts > 30) {
          history[slug].push({ ts: now, price });
          // Trim to max points
          if (history[slug].length > MAX_POINTS) {
            history[slug] = history[slug].slice(-MAX_POINTS);
          }
        }
      }

      result[slug] = {
        label: info.label,
        oracle: info.oracle,
        direction: info.direction,
        history: history[slug],
      };
    }

    // Save updated history to Redis
    if (isRedisConfigured()) {
      await redisSet(HISTORY_KEY, history);
    }

    res.status(200).json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
