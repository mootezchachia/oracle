/**
 * /api/signals — Live signal computation
 *
 * Computes market signals from live Polymarket data:
 * - Signal fusion from multiple market-implied indicators
 * - Ensemble-style model votes from different analysis angles
 * - Technical-style signals derived from market prices
 * - Crypto market analysis from CoinGecko
 *
 * Fully autonomous — no local files needed.
 */

const GAMMA_API = "https://gamma-api.polymarket.com";

const TRACKED_SLUGS = [
  "us-x-iran-ceasefire-by-march-31",
  "us-x-iran-ceasefire-by-may-31-313",
  "us-x-iran-ceasefire-by-june-30-752",
  "will-the-iranian-regime-fall-by-the-end-of-2026",
  "will-russia-enter-druzkhivka-by-june-30-933-897",
];

const SENTIMENT_KW = [
  "ceasefire", "peace", "recession", "tariff", "fed", "iran", "ukraine",
  "trump", "congress", "nato", "china", "taiwan", "oil", "inflation", "war",
];

async function fetchMarkets() {
  const url = `${GAMMA_API}/markets?active=true&closed=false&limit=30&order=volume24hr&ascending=false`;
  const r = await fetch(url, { signal: AbortSignal.timeout(10000) });
  if (!r.ok) return [];
  const markets = await r.json();
  return markets.filter((m) => {
    const q = (m.question || "").toLowerCase();
    return SENTIMENT_KW.some((kw) => q.includes(kw));
  });
}

async function fetchTrackedMarkets() {
  const results = await Promise.allSettled(
    TRACKED_SLUGS.map((slug) =>
      fetch(`${GAMMA_API}/markets?slug=${slug}`, { signal: AbortSignal.timeout(10000) })
        .then((r) => (r.ok ? r.json() : []))
    )
  );
  return results
    .filter((r) => r.status === "fulfilled" && r.value.length > 0)
    .map((r) => r.value[0]);
}

function parsePrice(market) {
  try {
    const prices = JSON.parse(market.outcomePrices || "[]");
    return { yes: parseFloat(prices[0]), no: parseFloat(prices[1]) };
  } catch {
    return { yes: 0.5, no: 0.5 };
  }
}

function computeFusion(markets) {
  if (markets.length === 0) return null;

  // Pick the highest-volume tracked market for fusion
  const market = markets[0];
  const { yes } = parsePrice(market);

  // Market-implied direction (deviation from 50/50)
  const marketImplied = { source: "market_implied", direction: (yes - 0.5) * 2, strength: Math.abs(yes - 0.5), weight: 0.3, decay: 1.0 };
  marketImplied.effective = marketImplied.direction * marketImplied.weight;
  marketImplied.contribution = marketImplied.effective;

  // Volume signal — high volume = confidence in current direction
  const vol = parseFloat(market.volume24hr || 0);
  const volNorm = Math.min(vol / 100000, 1);
  const volumeSignal = { source: "volume_momentum", direction: (yes - 0.5) * volNorm * 2, strength: volNorm, weight: 0.25, decay: 1.0 };
  volumeSignal.effective = volumeSignal.direction * volumeSignal.weight;
  volumeSignal.contribution = volumeSignal.effective;

  // Spread signal — tight spread = strong consensus
  const spreadSignal = { source: "spread_consensus", direction: (yes > 0.5 ? 1 : -1) * Math.max(0, 1 - Math.abs(yes - 0.5) * 4), strength: 0.3, weight: 0.2, decay: 1.0 };
  spreadSignal.effective = spreadSignal.direction * spreadSignal.weight;
  spreadSignal.contribution = spreadSignal.effective;

  // Multi-market agreement
  const allPrices = markets.map((m) => parsePrice(m).yes);
  const avgDir = allPrices.reduce((s, p) => s + (p - 0.5), 0) / allPrices.length;
  const agreement = allPrices.filter((p) => (p - 0.5) * avgDir > 0).length / allPrices.length;
  const multiMarket = { source: "multi_market", direction: avgDir * 2, strength: agreement, weight: 0.25, decay: 1.0 };
  multiMarket.effective = multiMarket.direction * multiMarket.weight;
  multiMarket.contribution = multiMarket.effective;

  const breakdown = [marketImplied, volumeSignal, spreadSignal, multiMarket];
  const totalDir = breakdown.reduce((s, b) => s + b.contribution, 0);
  const totalConf = breakdown.reduce((s, b) => s + b.strength * b.weight, 0);

  const rec = Math.abs(totalDir) < 0.05 ? "HOLD" : totalDir > 0.15 ? "LEAN YES" : totalDir < -0.15 ? "LEAN NO" : totalDir > 0 ? "SLIGHT YES" : "SLIGHT NO";

  return {
    market_id: market.id || market.conditionId,
    question: market.question,
    direction: parseFloat(totalDir.toFixed(4)),
    confidence: parseFloat(Math.min(totalConf + 0.2, 1).toFixed(4)),
    signal_count: breakdown.length,
    agreement: parseFloat(agreement.toFixed(4)),
    edge_pct: parseFloat((Math.abs(totalDir) * 100).toFixed(1)),
    recommendation: rec,
    breakdown,
    timestamp: new Date().toISOString(),
  };
}

function computeEnsemble(markets) {
  if (markets.length === 0) return null;

  const market = markets[0];
  const { yes } = parsePrice(market);

  const models = [
    { name: "market_price", direction: (yes - 0.5) * 2, confidence: 0.7 },
    { name: "volume_weighted", direction: (yes - 0.5) * Math.min(parseFloat(market.volume24hr || 0) / 50000, 2), confidence: 0.5 },
    { name: "mean_reversion", direction: -(yes - 0.5), confidence: 0.3 },
    { name: "momentum", direction: (yes - 0.5) * 1.5, confidence: 0.4 },
  ];

  const avgDir = models.reduce((s, m) => s + m.direction * m.confidence, 0) / models.reduce((s, m) => s + m.confidence, 0);
  const rec = Math.abs(avgDir) < 0.05 ? "HOLD" : avgDir > 0.15 ? "LEAN YES" : avgDir < -0.15 ? "LEAN NO" : "HOLD";

  return {
    question: market.question,
    direction: parseFloat(avgDir.toFixed(4)),
    confidence: parseFloat((models.reduce((s, m) => s + m.confidence, 0) / models.length).toFixed(4)),
    model_count: models.length,
    edge_pct: parseFloat((Math.abs(avgDir) * 100).toFixed(1)),
    recommendation: rec,
    votes: models,
    timestamp: new Date().toISOString(),
  };
}

function computeTA(markets) {
  return markets.slice(0, 8).map((m) => {
    const { yes } = parsePrice(m);
    const deviation = yes - 0.5;
    const vol = parseFloat(m.volume24hr || 0);
    const volStrength = Math.min(vol / 100000, 1);

    return {
      source: (m.question || "market").slice(0, 40),
      direction: parseFloat((deviation * 2).toFixed(4)),
      strength: parseFloat(volStrength.toFixed(4)),
      timeframe: "live",
      timestamp: new Date().toISOString(),
      metadata: { yes_price: yes, volume_24h: vol },
    };
  });
}

async function fetchCryptoSignals() {
  try {
    const url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin,ethereum,solana&order=market_cap_desc&price_change_percentage=1h,24h";
    const r = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) return [];
    const coins = await r.json();

    return coins.map((c) => {
      const change = c.price_change_percentage_24h_in_currency || 0;
      const direction = change > 2 ? 0.5 : change < -2 ? -0.5 : change / 4;
      const confidence = Math.min(Math.abs(change) / 10 + 0.3, 0.9);
      const rec = Math.abs(change) < 1 ? "HOLD" : change > 3 ? "LEAN YES" : change < -3 ? "LEAN NO" : "HOLD";

      return {
        crypto: c.symbol.toUpperCase(),
        crypto_price: c.current_price,
        change_24h: change,
        direction: parseFloat(direction.toFixed(4)),
        confidence: parseFloat(confidence.toFixed(4)),
        recommendation: rec,
        timestamp: new Date().toISOString(),
      };
    });
  } catch {
    return [];
  }
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");

  try {
    const [sentimentMarkets, trackedMarkets, cryptoSignals] = await Promise.all([
      fetchMarkets(),
      fetchTrackedMarkets(),
      fetchCryptoSignals(),
    ]);

    const allMarkets = [...trackedMarkets, ...sentimentMarkets];

    const fusion = computeFusion(allMarkets);
    const ensemble = computeEnsemble(allMarkets);
    const ta = computeTA(allMarkets);

    res.status(200).json({
      fusion: fusion ? [fusion] : [],
      ensemble: ensemble ? [ensemble] : [],
      ta_signals: ta,
      executor: null,
      crypto_15m: cryptoSignals,
      updated: new Date().toISOString(),
    });
  } catch (err) {
    res.status(200).json({
      fusion: [],
      ensemble: [],
      ta_signals: [],
      executor: null,
      crypto_15m: [],
      updated: new Date().toISOString(),
      error: err.message,
    });
  }
}
