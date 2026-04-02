/**
 * ORACLE $1K Strategy — Serverless Execution Endpoint
 *
 * GET  /api/strategy100-run         — run full scan + auto-execute
 * GET  /api/strategy100-run?reset=1 — reset portfolio to $1,000
 *
 * 4 Strategies:
 *   1. Bonds    ($500) — High-probability outcomes at 93c+, TP 7% / SL 5%
 *   2. Expertise ($250) — Sentiment-driven contrarian on political/macro markets
 *   3. Crypto15m ($150) — 15-minute crypto up/down markets with momentum signals
 *   4. Value    ($100) — Mean reversion on recent price drops, spread arbitrage
 *
 * Triggered by Vercel Cron (every 15 min) or manual GET.
 * Stores portfolio state in Upstash Redis.
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';

const GAMMA_API = "https://gamma-api.polymarket.com";
const PORTFOLIO_KEY = "oracle:strategy100:portfolio";
const LOG_KEY = "oracle:strategy100:log";
const LAST_FULL_SCAN_KEY = "oracle:strategy100:last_full_scan";

const RESEARCH_PARAMS_KEY = "oracle:research:params";

const SKIP_KW = [
  "nba", "nfl", "nhl", "mlb", "ufc", "mma", "premier league", "champions league",
  "tennis", "golf", "esports", "counter-strike", "dota",
  "oscars", "grammys", "weather", "temperature",
];

const SENTIMENT_KW = [
  "approval", "election", "ceasefire", "peace", "recession", "tariff",
  "fed", "iran", "ukraine", "trump", "midterm", "congress", "nato",
  "china", "taiwan", "oil", "inflation", "war", "sanction", "regime",
  "immigration", "border", "policy", "bitcoin", "crypto", "ethereum",
];

const CRYPTO_KEYWORDS = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "crypto"];
const CRYPTO_15M_KEYWORDS = ["15 minute", "15-minute", "15min", "up or down"];

// ─── Portfolio ────────────────────────────────────────────────

function newPortfolio() {
  return {
    account: { starting_balance: 1000, total_cash: 1000 },
    allocations: {
      bonds: { budget: 500, cash: 500, invested: 0 },
      expertise: { budget: 250, cash: 250, invested: 0 },
      crypto15m: { budget: 150, cash: 150, invested: 0 },
      value: { budget: 100, cash: 100, invested: 0 },
    },
    trades: [],
    stats: { total_trades: 0, wins: 0, losses: 0, total_pnl: 0, best_trade: 0, worst_trade: 0 },
    created: new Date().toISOString(),
    last_updated: new Date().toISOString(),
  };
}

async function loadPortfolio() {
  const saved = await redisGet(PORTFOLIO_KEY);
  if (!saved) return newPortfolio();

  // Migrate old $100 portfolio to $1K if needed
  if (saved.account.starting_balance < 1000) {
    const extra = 1000 - saved.account.starting_balance;
    saved.account.starting_balance = 1000;
    saved.account.total_cash += extra;

    // Set up new allocations preserving existing positions
    const oldBonds = saved.allocations.bonds || { budget: 60, cash: 60, invested: 0 };
    const oldExpertise = saved.allocations.expertise || { budget: 30, cash: 30, invested: 0 };
    const oldFlash = saved.allocations.flash_crash || { budget: 10, cash: 10, invested: 0 };

    saved.allocations = {
      bonds: { budget: 500, cash: oldBonds.cash + (500 - oldBonds.budget), invested: oldBonds.invested },
      expertise: { budget: 250, cash: oldExpertise.cash + (250 - oldExpertise.budget), invested: oldExpertise.invested },
      crypto15m: { budget: 150, cash: 150, invested: 0 },
      value: { budget: 100, cash: oldFlash.cash + (100 - oldFlash.budget), invested: oldFlash.invested },
    };

    // Migrate flash_crash trades to value strategy
    for (const t of saved.trades) {
      if (t.strategy === "flash_crash") t.strategy = "value";
    }

    delete saved.allocations.flash_crash;
  }

  return saved;
}

async function savePortfolio(portfolio) {
  portfolio.last_updated = new Date().toISOString();
  portfolio.account.total_cash = Object.values(portfolio.allocations)
    .reduce((sum, a) => sum + a.cash, 0);
  await redisSet(PORTFOLIO_KEY, portfolio);
}

async function appendLog(entry) {
  const log = (await redisGet(LOG_KEY)) || [];
  log.push({ ...entry, timestamp: new Date().toISOString() });
  await redisSet(LOG_KEY, log.slice(-200));
}

// ─── Market Fetching ──────────────────────────────────────────

async function fetchAllMarkets() {
  const all = [];
  for (let offset = 0; offset < 200; offset += 50) {
    try {
      const r = await fetch(
        `${GAMMA_API}/markets?closed=false&limit=50&offset=${offset}&order=volume&ascending=false`,
        { signal: AbortSignal.timeout(15000) }
      );
      if (!r.ok) break;
      const batch = await r.json();
      if (!batch || batch.length === 0) break;
      all.push(...batch);
    } catch { break; }
  }
  return all;
}

function daysUntilExpiry(endDate) {
  if (!endDate) return 999; // unknown = treat as far out
  const end = new Date(endDate);
  if (isNaN(end.getTime())) return 999;
  return Math.max(0, Math.round((end - Date.now()) / 86400000));
}

function parseMarket(m) {
  let prices = [];
  try { prices = JSON.parse(m.outcomePrices || "[]"); } catch {}
  prices = prices.map(p => parseFloat(p));

  const days = daysUntilExpiry(m.endDate);

  return {
    market_id: m.id || "",
    slug: m.slug || "",
    question: m.question || "",
    yes_price: prices[0] ?? null,
    no_price: prices[1] ?? null,
    volume: parseFloat(m.volume || 0) || 0,
    end_date: m.endDate || "",
    days_to_expiry: days,
    category: detectCategory(m.question || ""),
  };
}

function detectCategory(question) {
  const q = question.toLowerCase();
  if (CRYPTO_KEYWORDS.some(k => q.includes(k))) return "crypto";
  if (["oil", "gas", "gold", "silver", "commodity", "crude"].some(k => q.includes(k))) return "economic";
  if (["fed", "inflation", "gdp", "recession", "interest rate", "cpi", "employment"].some(k => q.includes(k))) return "economic";
  if (["trump", "biden", "election", "congress", "senate", "vote", "governor"].some(k => q.includes(k))) return "political";
  if (["war", "ceasefire", "peace", "nato", "iran", "ukraine", "china", "taiwan", "sanction"].some(k => q.includes(k))) return "geopolitical";
  return "other";
}

async function fetchPrice(slug) {
  try {
    const r = await fetch(`${GAMMA_API}/markets?slug=${slug}`, { signal: AbortSignal.timeout(10000) });
    if (!r.ok) return null;
    const markets = await r.json();
    if (markets && markets.length > 0) return parseMarket(markets[0]);
  } catch {}
  return null;
}

// ─── Strategy 1: Bonds ───────────────────────────────────────
// High-probability outcomes (93c+). TP 7%, SL 5%, maturity exit at 98c+
// Requires ≥14 days to expiry. Max 6 positions, $50 each.

function findBonds(markets, openPositions, skipReasons) {
  const bonds = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 50000) { continue; }

    // Time-to-expiry filter
    if (pm.days_to_expiry < 14) {
      skipReasons.push({ strategy: "bonds", slug: pm.slug, reason: `Expiry too soon: ${pm.days_to_expiry}d` });
      continue;
    }

    // Category diversification: max 2 bonds in same category
    const catCount = openPositions.filter(t => t.strategy === "bonds" && t.category === pm.category).length;
    if (catCount >= 2) {
      skipReasons.push({ strategy: "bonds", slug: pm.slug, reason: `Category limit: ${pm.category} (${catCount})` });
      continue;
    }

    if (pm.yes_price >= 0.93) {
      bonds.push({
        ...pm, bond_side: "yes", bond_price: pm.yes_price,
        expected_return_pct: Math.round(((1.0 / pm.yes_price) - 1) * 10000) / 100,
      });
    }
    if (pm.no_price >= 0.93) {
      bonds.push({
        ...pm, bond_side: "no", bond_price: pm.no_price,
        expected_return_pct: Math.round(((1.0 / pm.no_price) - 1) * 10000) / 100,
      });
    }
  }
  bonds.sort((a, b) => b.expected_return_pct - a.expected_return_pct);
  return bonds;
}

// ─── Strategy 2: Expertise (Mispricing) ─────────────────────
// Sentiment-driven contrarian. 1+ keyword, 5% edge, price 0.15-0.85.
// Requires ≥30 days to expiry. Max 3 positions, $50 each.

function findExpertise(markets, openPositions, skipReasons) {
  const opps = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 10000) continue;
    if (pm.yes_price < 0.15 || pm.yes_price > 0.85) continue;

    const q = pm.question.toLowerCase();
    if (!SENTIMENT_KW.some(k => q.includes(k))) continue;
    if (SKIP_KW.some(k => q.includes(k))) continue;

    // Time-to-expiry filter
    if (pm.days_to_expiry < 30) {
      skipReasons.push({ strategy: "expertise", slug: pm.slug, reason: `Expiry too soon: ${pm.days_to_expiry}d` });
      continue;
    }

    // Category diversification: max 2 per category
    const catCount = openPositions.filter(t => t.strategy === "expertise" && t.category === pm.category).length;
    if (catCount >= 2) {
      skipReasons.push({ strategy: "expertise", slug: pm.slug, reason: `Category limit: ${pm.category}` });
      continue;
    }

    const sentiment = SENTIMENT_KW.filter(k => q.includes(k)).length;
    if (sentiment < 1) continue; // lowered from 2 → 1

    const direction = pm.yes_price > 0.5 ? -1 : 1; // contrarian
    const side = direction > 0 ? "yes" : "no";
    const entry_price = side === "yes" ? pm.yes_price : pm.no_price;
    const edge_pct = Math.abs(pm.yes_price - 0.5) * 100;

    if (edge_pct >= 5) { // lowered from 8 → 5
      opps.push({
        ...pm, trade_side: side, entry_price, edge_pct: Math.round(edge_pct * 100) / 100,
        sentiment_hits: sentiment,
      });
    }
  }
  opps.sort((a, b) => b.edge_pct - a.edge_pct);
  return opps;
}

// ─── Strategy 3: Crypto 15m ─────────────────────────────────
// Discovers active 15-minute crypto prediction markets.
// Uses CoinGecko momentum signal + Polymarket price confirmation.
// Max 2 positions, $30 each. No expiry filter (always 15 min).

async function findCrypto15m(markets, openPositions, skipReasons) {
  const found = [];

  // Find 15m crypto markets from the already-fetched markets
  const crypto15mMarkets = [];
  for (const m of markets) {
    const q = (m.question || "").toLowerCase();
    const desc = (m.description || "").toLowerCase();
    const combined = q + " " + desc;

    const isCrypto = CRYPTO_KEYWORDS.some(k => combined.includes(k));
    const is15m = CRYPTO_15M_KEYWORDS.some(k => combined.includes(k));

    if (isCrypto && is15m) {
      const pm = parseMarket(m);
      if (pm.volume < 5000) continue;

      // Determine which crypto
      let crypto = "BTC";
      if (["ethereum", "eth"].some(k => combined.includes(k))) crypto = "ETH";
      else if (["solana", "sol"].some(k => combined.includes(k))) crypto = "SOL";
      else if (combined.includes("xrp")) crypto = "XRP";

      crypto15mMarkets.push({ ...pm, crypto, raw: m });
    }
  }

  if (crypto15mMarkets.length === 0) return found;

  // Limit open crypto positions to 2
  const openCrypto = openPositions.filter(t => t.strategy === "crypto15m").length;
  if (openCrypto >= 2) {
    skipReasons.push({ strategy: "crypto15m", slug: "all", reason: `Max 2 positions open (have ${openCrypto})` });
    return found;
  }

  // Fetch CoinGecko data for momentum signal
  let coinData = null;
  try {
    const coins = ["bitcoin", "ethereum", "solana", "ripple"];
    const url = `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=${coins.join(",")}&price_change_percentage=1h`;
    const r = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (r.ok) {
      const data = await r.json();
      coinData = {};
      for (const c of data) {
        const sym = c.symbol.toUpperCase();
        coinData[sym] = {
          price: c.current_price,
          change_1h: c.price_change_percentage_1h_in_currency || 0,
        };
      }
    }
  } catch {}

  if (!coinData) {
    skipReasons.push({ strategy: "crypto15m", slug: "all", reason: "CoinGecko fetch failed" });
    return found;
  }

  for (const market of crypto15mMarkets) {
    const coin = coinData[market.crypto];
    if (!coin) continue;

    // Momentum signal: 1h crypto price change
    let signal = "HOLD";
    if (coin.change_1h > 1) signal = "UP";
    else if (coin.change_1h < -1) signal = "DOWN";

    if (signal === "HOLD") {
      skipReasons.push({ strategy: "crypto15m", slug: market.slug, reason: `No momentum: ${market.crypto} 1h ${coin.change_1h?.toFixed(2)}%` });
      continue;
    }

    // For "up or down" markets, yes_price = UP probability
    // Buy the side that matches our momentum signal
    const side = signal === "UP" ? "yes" : "no";
    const entry_price = side === "yes" ? market.yes_price : market.no_price;

    // Only enter if price < 0.55 (paying less than 55c for our directional bet)
    if (entry_price > 0.55) {
      skipReasons.push({ strategy: "crypto15m", slug: market.slug, reason: `Price too high: ${(entry_price * 100).toFixed(0)}c for ${side}` });
      continue;
    }

    found.push({
      ...market,
      trade_side: side,
      entry_price,
      signal,
      momentum_1h: coin.change_1h,
      crypto_price: coin.price,
    });
  }

  // Sort by strongest momentum
  found.sort((a, b) => Math.abs(b.momentum_1h) - Math.abs(a.momentum_1h));
  return found;
}

// ─── Strategy 4: Value (replaces Flash Crash) ────────────────
// Mean reversion on recent drops + spread arbitrage.
// Requires ≥30 days to expiry. Max 2 positions, $25 each.

function findValue(markets, openPositions, skipReasons) {
  const opps = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 100000) continue;

    const q = pm.question.toLowerCase();
    if (SKIP_KW.some(k => q.includes(k))) continue;

    // Time-to-expiry filter
    if (pm.days_to_expiry < 30) {
      skipReasons.push({ strategy: "value", slug: pm.slug, reason: `Expiry too soon: ${pm.days_to_expiry}d` });
      continue;
    }

    // Category diversification: max 2 per category
    const catCount = openPositions.filter(t => t.strategy === "value" && t.category === pm.category).length;
    if (catCount >= 2) {
      skipReasons.push({ strategy: "value", slug: pm.slug, reason: `Category limit: ${pm.category}` });
      continue;
    }

    // Signal 1: YES+NO spread < 0.98 (arbitrage-adjacent)
    const spread = pm.yes_price + pm.no_price;
    const spreadGap = 1.0 - spread;

    // Signal 2: Price in value range (15c-70c) with high volume = potential mispricing
    const minPrice = 0.15;
    const maxPrice = 0.70;
    const spreadThreshold = 0.02; // 2% spread = opportunity

    if (pm.yes_price >= minPrice && pm.yes_price <= maxPrice) {
      const valueSignal = (pm.volume / 1000000) * spreadGap;
      if (valueSignal > 0.01 || spreadGap >= spreadThreshold) {
        opps.push({
          ...pm, trade_side: "yes", entry_price: pm.yes_price,
          value_signal: Math.round(valueSignal * 10000) / 10000,
          spread_gap: Math.round(spreadGap * 10000) / 10000,
          upside_pct: Math.round(((1.0 / pm.yes_price) - 1) * 100),
        });
      }
    }
    if (pm.no_price >= minPrice && pm.no_price <= maxPrice) {
      const valueSignal = (pm.volume / 1000000) * spreadGap;
      if (valueSignal > 0.01 || spreadGap >= spreadThreshold) {
        opps.push({
          ...pm, trade_side: "no", entry_price: pm.no_price,
          value_signal: Math.round(valueSignal * 10000) / 10000,
          spread_gap: Math.round(spreadGap * 10000) / 10000,
          upside_pct: Math.round(((1.0 / pm.no_price) - 1) * 100),
        });
      }
    }
  }
  // Prefer higher spread gaps (more mispriced)
  opps.sort((a, b) => b.spread_gap - a.spread_gap || b.value_signal - a.value_signal);
  return opps;
}

// ─── Trade Execution ─────────────────────────────────────────

function executeTrade(portfolio, strategy, slug, question, side, price, maxSize, extra = {}) {
  const alloc = portfolio.allocations[strategy];
  if (!alloc) return null;

  // Use research-optimized position size if available
  const researchParams = portfolio._researchParams?.[strategy];
  const size = Math.min(researchParams?.position_size || maxSize, alloc.cash);
  if (size < 3) return null;

  // Check for duplicate
  if (portfolio.trades.some(t => t.slug === slug && t.status === "open")) return null;

  const shares = Math.round((size / price) * 10000) / 10000;

  // Strategy-specific TP/SL — use research-optimized values when available
  let take_profit_pct, stop_loss_pct;
  if (researchParams) {
    take_profit_pct = researchParams.take_profit_pct;
    stop_loss_pct = researchParams.stop_loss_pct;
  } else {
    switch (strategy) {
      case "bonds":    take_profit_pct = 7;  stop_loss_pct = 5;  break;
      case "expertise": take_profit_pct = 15; stop_loss_pct = 10; break;
      case "crypto15m": take_profit_pct = 20; stop_loss_pct = 12; break;
      case "value":    take_profit_pct = 25; stop_loss_pct = 15; break;
      default:         take_profit_pct = 15; stop_loss_pct = 10; break;
    }
  }

  const trade = {
    id: portfolio.trades.length + 1,
    strategy, slug, question, side,
    entry_price: Math.round(price * 10000) / 10000,
    shares, invested: Math.round(size * 100) / 100,
    date: new Date().toISOString().slice(0, 10),
    status: "open",
    take_profit_pct,
    stop_loss_pct,
    category: extra.category || "other",
    days_to_expiry: extra.days_to_expiry || null,
  };

  alloc.cash = Math.round((alloc.cash - size) * 100) / 100;
  alloc.invested = Math.round((alloc.invested + size) * 100) / 100;
  portfolio.trades.push(trade);
  portfolio.stats.total_trades++;
  return trade;
}

// ─── Exit Checking ───────────────────────────────────────────

async function checkExits(portfolio) {
  const closed = [];
  for (const trade of portfolio.trades) {
    if (trade.status !== "open") continue;

    const market = await fetchPrice(trade.slug);
    if (!market) continue;

    const currentPrice = trade.side === "yes" ? market.yes_price : market.no_price;
    if (currentPrice === null) continue;

    const currentValue = trade.shares * currentPrice;
    const pnl = currentValue - trade.invested;
    const pnlPct = trade.invested > 0 ? ((currentValue / trade.invested) - 1) * 100 : 0;

    let closeReason = null;

    // Standard TP/SL
    if (pnlPct >= trade.take_profit_pct) closeReason = `TP: ${pnlPct.toFixed(1)}%`;
    else if (pnlPct <= -trade.stop_loss_pct) closeReason = `SL: ${pnlPct.toFixed(1)}%`;

    // Bond maturity exit at 98c+
    if (trade.strategy === "bonds" && currentPrice >= 0.98) closeReason = `Matured: ${(currentPrice * 100).toFixed(0)}c`;

    if (closeReason) {
      trade.status = "closed";
      trade.exit_price = Math.round(currentPrice * 10000) / 10000;
      trade.current_value = Math.round(currentValue * 100) / 100;
      trade.pnl = Math.round(pnl * 100) / 100;
      trade.pnl_pct = Math.round(pnlPct * 100) / 100;
      trade.close_reason = closeReason;
      trade.closed_at = new Date().toISOString();

      const alloc = portfolio.allocations[trade.strategy];
      if (alloc) {
        alloc.cash = Math.round((alloc.cash + currentValue) * 100) / 100;
        alloc.invested = Math.round((alloc.invested - trade.invested) * 100) / 100;
      }
      portfolio.stats.total_pnl = Math.round((portfolio.stats.total_pnl + pnl) * 100) / 100;
      if (pnl > 0) { portfolio.stats.wins++; portfolio.stats.best_trade = Math.max(portfolio.stats.best_trade, pnl); }
      else { portfolio.stats.losses++; portfolio.stats.worst_trade = Math.min(portfolio.stats.worst_trade, pnl); }

      closed.push(trade);
    }
  }
  return closed;
}

// ─── Daily Opportunity Scanner (for main portfolio) ──────────

const MAIN_PORTFOLIO_KEY = "oracle:portfolio:main";
const SCAN_LOG_KEY = "oracle:scanner:log";

const ORACLE_TOPICS = [
  { keywords: ["iran", "ceasefire", "military action", "regime", "khamenei", "irgc"], category: "geopolitical", label: "Iran" },
  { keywords: ["ukraine", "russia", "lyman", "donetsk", "crimea", "zelensky", "putin"], category: "geopolitical", label: "Russia-Ukraine" },
  { keywords: ["china", "taiwan", "blockade", "xi jinping"], category: "geopolitical", label: "China-Taiwan" },
  { keywords: ["tariff", "trade war", "sanction"], category: "economic", label: "Trade" },
  { keywords: ["fed", "interest rate", "rate cut", "inflation", "cpi", "recession", "gdp"], category: "economic", label: "Fed/Macro" },
  { keywords: ["bitcoin", "btc", "ethereum", "eth", "crypto", "microstrategy", "solana"], category: "crypto", label: "Crypto" },
  { keywords: ["trump", "congress", "election", "executive order", "vance"], category: "political", label: "US Politics" },
  { keywords: ["ai ", "openai", "anthropic", "google ai", "deepmind", "artificial intelligence"], category: "tech", label: "AI/Tech" },
  { keywords: ["oil", "crude", "opec", "natural gas", "gold", "commodity"], category: "economic", label: "Commodities" },
  { keywords: ["nato", "war", "peace", "ceasefire", "missile", "nuclear"], category: "geopolitical", label: "Security" },
];

function classifyMarket(question) {
  const q = question.toLowerCase();
  for (const topic of ORACLE_TOPICS) {
    if (topic.keywords.some(k => q.includes(k))) {
      return { category: topic.category, label: topic.label };
    }
  }
  return null;
}

function scoreOpportunity(pm, classification) {
  // Score 0-100: how good is this opportunity for ORACLE's main portfolio?
  let score = 0;

  // Price range: best opportunities are 0.15-0.85 (not near-certain)
  const yes = pm.yes_price;
  if (yes >= 0.20 && yes <= 0.80) score += 30;
  else if (yes >= 0.10 && yes <= 0.90) score += 15;
  else if (yes >= 0.90 || yes <= 0.10) score += 5; // bonds

  // Volume: higher = more liquid
  if (pm.volume > 500000) score += 25;
  else if (pm.volume > 100000) score += 20;
  else if (pm.volume > 50000) score += 15;
  else if (pm.volume > 10000) score += 10;

  // Time: prefer 14+ days (avoid expiring today)
  if (pm.days_to_expiry >= 30) score += 20;
  else if (pm.days_to_expiry >= 14) score += 15;
  else if (pm.days_to_expiry >= 7) score += 10;
  else if (pm.days_to_expiry >= 1) score += 5;

  // Edge from 50/50: bigger deviation = clearer signal
  const edge = Math.abs(yes - 0.5) * 100;
  if (edge >= 30) score += 15;
  else if (edge >= 15) score += 10;
  else if (edge >= 5) score += 5;

  // Boost ORACLE-domain markets (geopolitical/macro)
  if (classification) {
    if (["geopolitical", "economic", "political"].includes(classification.category)) score += 10;
    if (classification.category === "crypto") score += 5;
  }

  return Math.min(100, score);
}

async function runDailyScanner(markets) {
  const opportunities = [];

  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;

    const q = pm.question.toLowerCase();
    if (SKIP_KW.some(k => q.includes(k))) continue;

    const classification = classifyMarket(pm.question);
    if (!classification) continue; // only ORACLE-relevant markets

    // Skip near-certain (bonds handled by strategy100)
    if (pm.yes_price > 0.97 || pm.no_price > 0.97) continue;
    if (pm.volume < 5000) continue;

    const score = scoreOpportunity(pm, classification);
    if (score < 30) continue;

    // Determine suggested side
    let side, entry_price, thesis;
    if (pm.yes_price > 0.5) {
      // Market leans YES — contrarian says NO, unless bond
      if (pm.yes_price >= 0.88) {
        side = "yes"; entry_price = pm.yes_price;
        thesis = `Bond: ${classification.label} market at ${(pm.yes_price*100).toFixed(0)}c YES. High-prob outcome.`;
      } else {
        side = "no"; entry_price = pm.no_price;
        thesis = `Contrarian: ${classification.label} at ${(pm.yes_price*100).toFixed(0)}c YES — market may overestimate.`;
      }
    } else {
      if (pm.no_price >= 0.88) {
        side = "no"; entry_price = pm.no_price;
        thesis = `Bond: ${classification.label} market at ${(pm.no_price*100).toFixed(0)}c NO. High-prob outcome.`;
      } else {
        side = "yes"; entry_price = pm.yes_price;
        thesis = `Contrarian: ${classification.label} at ${(pm.yes_price*100).toFixed(0)}c YES — market may underestimate.`;
      }
    }

    opportunities.push({
      slug: pm.slug,
      question: pm.question,
      category: classification.category,
      label: classification.label,
      side,
      entry_price,
      yes_price: pm.yes_price,
      no_price: pm.no_price,
      volume: pm.volume,
      days_to_expiry: pm.days_to_expiry,
      score,
      thesis,
    });
  }

  // Sort by score descending
  opportunities.sort((a, b) => b.score - a.score);
  return opportunities;
}

async function handleDailyScan(req, res) {
  const markets = await fetchAllMarkets();
  const opportunities = await runDailyScanner(markets);

  // Load main portfolio to check for duplicates
  const mainPortfolio = await redisGet(MAIN_PORTFOLIO_KEY);
  const existingSlugs = new Set(
    (mainPortfolio?.trades || []).filter(t => t.status !== "closed").map(t => t.slug)
  );

  // Filter out already-held positions
  const fresh = opportunities.filter(o => !existingSlugs.has(o.slug));

  // Auto-execute top opportunities if ?execute=1
  const autoExecute = req.query?.execute === "1";
  const executed = [];

  if (autoExecute && mainPortfolio) {
    const cash = mainPortfolio.account?.cash || 0;
    const nextId = Math.max(...mainPortfolio.trades.map(t => t.id), 0) + 1;
    const maxNewTrades = 3; // max 3 new positions per daily scan
    const positionSize = 500;

    for (let i = 0; i < Math.min(fresh.length, maxNewTrades); i++) {
      const opp = fresh[i];
      if (opp.score < 50) break; // minimum quality threshold
      if (mainPortfolio.account.cash < positionSize) break;

      const trade = {
        id: nextId + i,
        slug: opp.slug,
        question: opp.question,
        side: opp.side,
        entry_price: opp.entry_price,
        shares: Math.round((positionSize / opp.entry_price) * 100) / 100,
        invested: positionSize,
        date: new Date().toISOString().slice(0, 10),
        status: "open",
        source: "scanner",
        thesis: opp.thesis,
        score: opp.score,
        category: opp.category,
      };

      mainPortfolio.trades.push(trade);
      mainPortfolio.account.cash = Math.round((mainPortfolio.account.cash - positionSize) * 100) / 100;
      executed.push(trade);
    }

    if (executed.length > 0) {
      await redisSet(MAIN_PORTFOLIO_KEY, mainPortfolio);
    }
  }

  // Log the scan
  const scanLog = {
    timestamp: new Date().toISOString(),
    markets_scanned: markets.length,
    opportunities_found: opportunities.length,
    fresh_opportunities: fresh.length,
    executed: executed.length,
    top_10: fresh.slice(0, 10).map(o => ({
      slug: o.slug,
      question: o.question.slice(0, 60),
      side: o.side,
      price: o.entry_price,
      score: o.score,
      label: o.label,
      thesis: o.thesis,
    })),
  };

  const logs = (await redisGet(SCAN_LOG_KEY)) || [];
  logs.push(scanLog);
  await redisSet(SCAN_LOG_KEY, logs.slice(-30));

  return res.status(200).json({
    status: "ok",
    scanned: markets.length,
    opportunities: fresh.length,
    executed: executed.length,
    executed_trades: executed,
    top_opportunities: fresh.slice(0, 15),
    scan_log: scanLog,
  });
}

// ─── Main Handler ────────────────────────────────────────────

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-cache");

  if (req.method !== "GET" && req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  if (!isRedisConfigured()) {
    return res.status(500).json({
      error: "Upstash Redis not configured",
      help: "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in Vercel env vars",
    });
  }

  try {
    // Daily opportunity scanner for main portfolio
    if (req.query?.action === "scan") {
      return handleDailyScan(req, res);
    }

    // Reset
    if (req.query?.reset === "1") {
      const p = newPortfolio();
      await savePortfolio(p);
      await redisSet(LAST_FULL_SCAN_KEY, null);
      return res.status(200).json({ status: "reset", portfolio: p });
    }

    const portfolio = await loadPortfolio();

    // Load research-optimized parameters (from auto-research experiments)
    const researchParams = await redisGet(RESEARCH_PARAMS_KEY);
    if (researchParams) {
      portfolio._researchParams = researchParams;
    }

    const executed = [];
    const skipReasons = [];
    const log = { cycle: new Date().toISOString(), executed: [], closed: [], scanned: {}, skipped: [], research_params: !!researchParams };

    // Track open positions for diversification checks
    const openPositions = portfolio.trades.filter(t => t.status === "open");

    // Determine if we need a full scan (bonds/expertise/value)
    // These only run once per day; crypto15m runs every 15 min
    const lastFullScan = await redisGet(LAST_FULL_SCAN_KEY);
    const hoursSinceFullScan = lastFullScan
      ? (Date.now() - new Date(lastFullScan).getTime()) / 3600000
      : 999;
    const runFullScan = hoursSinceFullScan >= 23;

    // Fetch markets
    const markets = await fetchAllMarkets();
    log.scanned.total_markets = markets.length;

    // ── Strategy 1: Bonds (max 6 positions, $50 each) ──
    if (runFullScan) {
      const bonds = findBonds(markets, openPositions, skipReasons);
      log.scanned.bonds = bonds.length;
      for (const b of bonds.slice(0, 6)) {
        const trade = executeTrade(portfolio, "bonds", b.slug, b.question, b.bond_side, b.bond_price, 50,
          { category: b.category, days_to_expiry: b.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "bonds", slug: b.slug }); }
      }
    }

    // ── Strategy 2: Expertise (max 3 positions, $50 each) ──
    if (runFullScan) {
      const expertise = findExpertise(markets, openPositions, skipReasons);
      log.scanned.expertise = expertise.length;
      for (const e of expertise.slice(0, 3)) {
        const trade = executeTrade(portfolio, "expertise", e.slug, e.question, e.trade_side, e.entry_price, 50,
          { category: e.category, days_to_expiry: e.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "expertise", slug: e.slug }); }
      }
    }

    // ── Strategy 3: Crypto 15m (max 2 positions, $30 each) ── always runs
    const crypto15m = await findCrypto15m(markets, openPositions, skipReasons);
    log.scanned.crypto15m = crypto15m.length;
    for (const c of crypto15m.slice(0, 2)) {
      const trade = executeTrade(portfolio, "crypto15m", c.slug, c.question, c.trade_side, c.entry_price, 30,
        { category: "crypto", days_to_expiry: 0 });
      if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "crypto15m", slug: c.slug, signal: c.signal, momentum: c.momentum_1h }); }
    }

    // ── Strategy 4: Value (max 2 positions, $25 each) ──
    if (runFullScan) {
      const value = findValue(markets, openPositions, skipReasons);
      log.scanned.value = value.length;
      for (const v of value.slice(0, 2)) {
        const trade = executeTrade(portfolio, "value", v.slug, v.question, v.trade_side, v.entry_price, 25,
          { category: v.category, days_to_expiry: v.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "value", slug: v.slug }); }
      }
    }

    // Mark full scan time
    if (runFullScan) {
      await redisSet(LAST_FULL_SCAN_KEY, new Date().toISOString());
    }

    // Check TP/SL exits (always runs)
    const closed = await checkExits(portfolio);
    log.closed = closed.map(t => ({ id: t.id, pnl: t.pnl, reason: t.close_reason }));
    log.skipped = skipReasons.slice(0, 50); // cap logged skips
    log.scanned.full_scan = runFullScan;

    // Save (strip internal research params before persisting)
    delete portfolio._researchParams;
    await savePortfolio(portfolio);
    await appendLog(log);

    return res.status(200).json({
      status: "ok",
      executed: executed.length,
      closed: closed.length,
      full_scan: runFullScan,
      portfolio_value: portfolio.account.total_cash +
        Object.values(portfolio.allocations).reduce((s, a) => s + a.invested, 0),
      total_pnl: portfolio.stats.total_pnl,
      scanned: log.scanned,
      trades: executed,
      exits: closed,
      skipped: skipReasons.length,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
