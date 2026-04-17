/**
 * ORACLE $1K Strategy — Serverless Execution Endpoint
 *
 * GET  /api/strategy100-run         — run full scan + auto-execute
 * GET  /api/strategy100-run?reset=1 — reset portfolio to $1,000
 *
 * 3 Active Strategies + Multi-Agent Superforecasting:
 *   1. Bonds     ($550) — High-probability outcomes at 93c+, TP 7% / SL 5%
 *   2. Expertise ($300) — Sentiment-driven contrarian, forecast-validated
 *   3. Value     ($150) — Mean reversion + spread arbitrage, forecast-validated
 *   4. Crypto15m ($0)   — DISABLED (lost $101, binary SL failure)
 *
 * Superforecasting Engine: 4 agents (Base Rate, Causal, Adversarial, Crowd)
 * aggregate via geometric mean + logit extremizing. Validates trades before entry.
 *
 * Triggered by Vercel Cron (daily) + QStash or manual GET.
 * Stores portfolio state in Upstash Redis.
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';
import {
  notify, notifyRaw, verifyAction, snoozeAlert,
  isTradingEnabled, setTradingEnabled, flushQueue,
} from './lib/notify.js';

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
      bonds: { budget: 550, cash: 550, invested: 0 },
      expertise: { budget: 300, cash: 300, invested: 0 },
      crypto15m: { budget: 0, cash: 0, invested: 0 },
      value: { budget: 150, cash: 150, invested: 0 },
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

    const oldBonds = saved.allocations.bonds || { budget: 60, cash: 60, invested: 0 };
    const oldExpertise = saved.allocations.expertise || { budget: 30, cash: 30, invested: 0 };
    const oldFlash = saved.allocations.flash_crash || { budget: 10, cash: 10, invested: 0 };

    saved.allocations = {
      bonds: { budget: 550, cash: oldBonds.cash + (550 - oldBonds.budget), invested: oldBonds.invested },
      expertise: { budget: 300, cash: oldExpertise.cash + (300 - oldExpertise.budget), invested: oldExpertise.invested },
      crypto15m: { budget: 0, cash: 0, invested: 0 },
      value: { budget: 150, cash: oldFlash.cash + (150 - oldFlash.budget), invested: oldFlash.invested },
    };

    for (const t of saved.trades) {
      if (t.strategy === "flash_crash") t.strategy = "value";
    }
    delete saved.allocations.flash_crash;
  }

  // Migrate crypto15m budget to bonds/expertise/value (crypto15m disabled due to losses)
  if (saved.allocations.crypto15m && saved.allocations.crypto15m.budget > 0) {
    const cryptoCash = saved.allocations.crypto15m.cash || 0;
    saved.allocations.crypto15m.budget = 0;
    saved.allocations.crypto15m.cash = 0;
    // Distribute remaining crypto cash: 50% bonds, 30% expertise, 20% value
    if (cryptoCash > 0) {
      saved.allocations.bonds.cash = Math.round((saved.allocations.bonds.cash + cryptoCash * 0.5) * 100) / 100;
      saved.allocations.bonds.budget += Math.round(cryptoCash * 0.5 * 100) / 100;
      saved.allocations.expertise.cash = Math.round((saved.allocations.expertise.cash + cryptoCash * 0.3) * 100) / 100;
      saved.allocations.expertise.budget += Math.round(cryptoCash * 0.3 * 100) / 100;
      saved.allocations.value.cash = Math.round((saved.allocations.value.cash + cryptoCash * 0.2) * 100) / 100;
      saved.allocations.value.budget += Math.round(cryptoCash * 0.2 * 100) / 100;
    }
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

// ─── Multi-Agent Superforecasting Engine ─────────────────────
// 4 independent agents produce probability estimates, then aggregated
// via geometric mean + logit extremizing (calibrated ensemble).

function agentBaseRate(pm) {
  // Base rate estimation using historical resolution patterns
  const yes = pm.yes_price;
  const days = pm.days_to_expiry;
  let prob = yes; // start with market price as prior

  // Short-dated markets: extreme prices are more reliable
  if (days <= 14) {
    if (yes > 0.85) prob = 0.80 + (yes - 0.85) * 1.3; // slightly deflate certainty
    else if (yes < 0.15) prob = yes * 0.7 + 0.03;
    else prob = yes * 0.9 + 0.05; // pull toward 50%
  }
  // Long-dated markets: more uncertainty, regress toward 50%
  else if (days > 90) {
    prob = yes * 0.75 + 0.125; // strong regression to mean
  } else {
    // Mid-range: moderate regression
    const regressionFactor = 0.85 + (days / 365) * 0.1;
    prob = yes * regressionFactor + (1 - regressionFactor) * 0.5;
  }

  // Category-specific base rates
  if (pm.category === "geopolitical") {
    // Geopolitical events: "will X happen" tends to NOT happen
    if (yes > 0.4 && yes < 0.7) prob *= 0.92; // slight NO bias
  } else if (pm.category === "crypto") {
    // Crypto: short-term price predictions are nearly random
    if (days <= 7) prob = prob * 0.6 + 0.2; // heavy regression
  }

  prob = Math.max(0.02, Math.min(0.98, prob));
  const confidence = days <= 7 ? 0.7 : days <= 30 ? 0.8 : 0.6;

  return {
    agent: "base_rate",
    probability: Math.round(prob * 10000) / 10000,
    confidence,
    reasoning: `Base rate: ${days}d to expiry, ${pm.category}. Prior ${(yes*100).toFixed(0)}c → adjusted ${(prob*100).toFixed(0)}c`,
  };
}

function agentCausal(pm) {
  // Causal analysis: volume, spread, momentum signals
  const yes = pm.yes_price;
  const no = pm.no_price;
  let prob = yes;

  // Spread analysis: YES+NO should sum to ~1.0
  // If < 0.98, there's a spread — true value likely between
  const spread = yes + no;
  if (spread < 0.97) {
    // Market-maker spread suggests true price is slightly higher for both sides
    const midpoint = yes / spread;
    prob = prob * 0.7 + midpoint * 0.3;
  }

  // Volume signal: high volume = more price discovery = price more accurate
  if (pm.volume > 1000000) {
    // High-volume markets: trust market price more
    prob = prob * 0.85 + yes * 0.15;
  } else if (pm.volume < 50000) {
    // Low volume: price may be stale, regress toward 50%
    prob = prob * 0.7 + 0.15;
  }

  // Days to expiry impact on certainty
  if (pm.days_to_expiry <= 3 && yes > 0.7) {
    prob = Math.min(prob * 1.05, 0.97); // near-expiry high-prob events: boost slightly
  }

  prob = Math.max(0.02, Math.min(0.98, prob));
  const confidence = pm.volume > 500000 ? 0.8 : pm.volume > 100000 ? 0.7 : 0.5;

  return {
    agent: "causal",
    probability: Math.round(prob * 10000) / 10000,
    confidence,
    reasoning: `Causal: vol=$${(pm.volume/1000).toFixed(0)}K, spread=${spread.toFixed(3)}, ${pm.days_to_expiry}d expiry`,
  };
}

function agentAdversarial(pm) {
  // Devil's advocate: challenges consensus, detects overconfidence
  const yes = pm.yes_price;
  let prob = yes;

  // Overconfidence detection: extreme prices often overestimate certainty
  if (yes > 0.90) {
    // Markets above 90c: historically ~15% fail (black swan discount)
    const overconfidence = (yes - 0.90) * 2.0; // 0-0.2 scale
    prob -= overconfidence * 0.15; // discount by up to 3%
  } else if (yes < 0.10) {
    // Markets below 10c: tail events happen more than price suggests
    const underconfidence = (0.10 - yes) * 2.0;
    prob += underconfidence * 0.15; // boost by up to 3%
  }

  // Mean reversion bias: markets that moved far from 50% often overshoot
  if (yes > 0.60 && yes < 0.85) {
    prob = prob * 0.95 + 0.025; // slight pull toward 50%
  } else if (yes > 0.15 && yes < 0.40) {
    prob = prob * 0.95 + 0.025;
  }

  // Contrarian on low-volume extreme prices (potential manipulation)
  if (pm.volume < 100000 && (yes > 0.80 || yes < 0.20)) {
    prob = prob * 0.8 + 0.1; // strong regression — thin market, unreliable
  }

  // Near-expiry contrarian: if still uncertain with <7 days, expect NO resolution
  if (pm.days_to_expiry <= 7 && yes > 0.3 && yes < 0.7) {
    // "Will X happen in 7 days?" — if market is 50/50, lean NO (most things don't happen)
    prob *= 0.90;
  }

  prob = Math.max(0.02, Math.min(0.98, prob));

  return {
    agent: "adversarial",
    probability: Math.round(prob * 10000) / 10000,
    confidence: 0.65,
    reasoning: `Adversarial: market ${(yes*100).toFixed(0)}c → challenged to ${(prob*100).toFixed(0)}c. ${pm.volume < 100000 ? "Thin market. " : ""}${pm.days_to_expiry <= 7 ? "Near expiry." : ""}`,
  };
}

function agentCrowd(pm) {
  // Crowd wisdom: trusts market price but volume-weights credibility
  const yes = pm.yes_price;
  let prob = yes; // crowd says what the price says

  // Volume-weighted credibility
  const volumeCredibility = Math.min(1, pm.volume / 500000);
  // Regress low-credibility markets toward 50%
  prob = prob * volumeCredibility + 0.5 * (1 - volumeCredibility);

  // Cross-signal: if price is near 50%, crowd is genuinely uncertain
  if (yes > 0.45 && yes < 0.55) {
    prob = 0.5; // coin flip — no edge
  }

  prob = Math.max(0.02, Math.min(0.98, prob));
  const confidence = volumeCredibility > 0.8 ? 0.85 : volumeCredibility > 0.4 ? 0.7 : 0.5;

  return {
    agent: "crowd",
    probability: Math.round(prob * 10000) / 10000,
    confidence,
    reasoning: `Crowd: price ${(yes*100).toFixed(0)}c, vol credibility ${(volumeCredibility*100).toFixed(0)}%. ${yes > 0.45 && yes < 0.55 ? "Coin flip — no edge." : ""}`,
  };
}

function aggregateForecasts(agents, extremizingFactor = 1.5) {
  // Step 1: Confidence-weighted geometric mean
  const totalWeight = agents.reduce((s, a) => s + a.confidence, 0);
  let logGeoMean = 0;
  for (const a of agents) {
    const w = a.confidence / totalWeight;
    logGeoMean += w * Math.log(Math.max(0.001, a.probability));
  }
  const geoMean = Math.exp(logGeoMean);

  // Step 2: Logit extremizing (pushes away from 50% for calibration)
  // logit(p) = ln(p / (1-p))
  const clamp = (p) => Math.max(0.01, Math.min(0.99, p));
  const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
  const invLogit = (l) => 1 / (1 + Math.exp(-l));

  const extremized = invLogit(logit(geoMean) * extremizingFactor);

  // Step 3: Confidence from agent agreement (lower spread = higher confidence)
  const probs = agents.map(a => a.probability);
  const maxSpread = Math.max(...probs) - Math.min(...probs);
  const agreement = 1 - Math.min(1, maxSpread * 2); // 0=total disagreement, 1=perfect agreement
  const ensembleConfidence = Math.round(agreement * 100);

  return {
    raw_geo_mean: Math.round(geoMean * 10000) / 10000,
    extremized: Math.round(extremized * 10000) / 10000,
    final_probability: Math.round(extremized * 10000) / 10000,
    ensemble_confidence: ensembleConfidence,
    agent_spread: Math.round(maxSpread * 10000) / 10000,
  };
}

function forecastMarket(pm) {
  const agents = [
    agentBaseRate(pm),
    agentCausal(pm),
    agentAdversarial(pm),
    agentCrowd(pm),
  ];

  const aggregation = aggregateForecasts(agents);

  // Edge = our forecast vs market price
  const edge = aggregation.final_probability - pm.yes_price;
  const edgePct = Math.round(edge * 10000) / 100;

  // Trading signal based on edge
  let signal = "HOLD";
  let tradeSide = null;
  let tradePrice = null;

  if (edge > 0.05 && aggregation.ensemble_confidence >= 40) {
    signal = "BUY YES";
    tradeSide = "yes";
    tradePrice = pm.yes_price;
  } else if (edge < -0.05 && aggregation.ensemble_confidence >= 40) {
    signal = "BUY NO";
    tradeSide = "no";
    tradePrice = pm.no_price;
  }

  // Repricing velocity: expected annual price movement toward our forecast
  const daysLeft = Math.max(1, pm.days_to_expiry);
  const repricingVelocity = Math.abs(edge) / (daysLeft / 365);

  return {
    market: {
      slug: pm.slug,
      question: pm.question,
      yes_price: pm.yes_price,
      no_price: pm.no_price,
      volume: pm.volume,
      days_to_expiry: pm.days_to_expiry,
      category: pm.category,
    },
    agents,
    aggregation,
    edge: Math.round(edge * 10000) / 10000,
    edge_pct: edgePct,
    repricing_velocity: Math.round(repricingVelocity * 100) / 100,
    signal,
    trade_side: tradeSide,
    trade_price: tradePrice,
  };
}

// Decompose complex questions into sub-components
function decomposeQuestion(pm) {
  const q = pm.question.toLowerCase();
  const parts = [];

  // Time component
  if (pm.days_to_expiry < 999) {
    parts.push({
      sub_question: `Will this resolve within ${pm.days_to_expiry} days?`,
      type: "temporal",
      weight: pm.days_to_expiry <= 14 ? 0.4 : 0.2,
    });
  }

  // Category-specific decomposition
  if (pm.category === "geopolitical") {
    if (q.includes("ceasefire") || q.includes("peace")) {
      parts.push({ sub_question: "Are negotiations actively underway?", type: "causal", weight: 0.3 });
      parts.push({ sub_question: "Is there international pressure for resolution?", type: "contextual", weight: 0.2 });
    }
    if (q.includes("war") || q.includes("attack") || q.includes("invasion")) {
      parts.push({ sub_question: "Are military forces positioned for action?", type: "causal", weight: 0.3 });
      parts.push({ sub_question: "Is there a precedent for this type of escalation?", type: "base_rate", weight: 0.2 });
    }
  } else if (pm.category === "economic") {
    parts.push({ sub_question: "What do leading economic indicators suggest?", type: "causal", weight: 0.3 });
    parts.push({ sub_question: "What is the historical frequency of this outcome?", type: "base_rate", weight: 0.3 });
  } else if (pm.category === "crypto") {
    parts.push({ sub_question: "What is current market momentum?", type: "momentum", weight: 0.3 });
    parts.push({ sub_question: "Are there upcoming catalysts (halvings, ETF decisions)?", type: "causal", weight: 0.2 });
  } else if (pm.category === "political") {
    parts.push({ sub_question: "What do polls/approval ratings suggest?", type: "crowd", weight: 0.3 });
    parts.push({ sub_question: "Is there a historical precedent?", type: "base_rate", weight: 0.2 });
  }

  // Always add a market efficiency component
  parts.push({
    sub_question: `Is the market price of ${(pm.yes_price*100).toFixed(0)}c well-calibrated?`,
    type: "meta",
    weight: 0.15,
  });

  return parts;
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
    if (!classification) continue;

    if (pm.yes_price > 0.97 || pm.no_price > 0.97) continue;
    if (pm.volume < 5000) continue;

    // Run multi-agent forecast instead of simple scoring
    const forecast = forecastMarket(pm);
    const score = scoreOpportunity(pm, classification);

    // Only surface markets where agents found edge
    if (forecast.signal === "HOLD" && score < 50) continue;

    // Use agent consensus for trade direction
    let side, entry_price, thesis;
    if (forecast.signal !== "HOLD") {
      side = forecast.trade_side;
      entry_price = forecast.trade_price;
      const agents = forecast.agents.map(a => `${a.agent}: ${(a.probability*100).toFixed(0)}c`).join(", ");
      thesis = `Agents [${agents}] → ${(forecast.aggregation.final_probability*100).toFixed(0)}c (edge ${forecast.edge_pct > 0 ? "+" : ""}${forecast.edge_pct}%). Signal: ${forecast.signal}`;
    } else {
      // Fallback to simple contrarian for high-score markets with no agent edge
      if (pm.yes_price > 0.5) {
        if (pm.yes_price >= 0.88) { side = "yes"; entry_price = pm.yes_price; }
        else { side = "no"; entry_price = pm.no_price; }
      } else {
        if (pm.no_price >= 0.88) { side = "no"; entry_price = pm.no_price; }
        else { side = "yes"; entry_price = pm.yes_price; }
      }
      thesis = `Score-based: ${classification.label} at ${(pm.yes_price*100).toFixed(0)}c. No agent edge.`;
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
      forecast: {
        final_probability: forecast.aggregation.final_probability,
        edge_pct: forecast.edge_pct,
        confidence: forecast.aggregation.ensemble_confidence,
        signal: forecast.signal,
        repricing_velocity: forecast.repricing_velocity,
        agents: forecast.agents.map(a => ({ agent: a.agent, prob: a.probability, conf: a.confidence })),
      },
    });
  }

  // Sort by absolute edge (strongest signals first), then by score
  opportunities.sort((a, b) => {
    const edgeA = Math.abs(a.forecast?.edge_pct || 0);
    const edgeB = Math.abs(b.forecast?.edge_pct || 0);
    if (edgeA !== edgeB) return edgeB - edgeA;
    return b.score - a.score;
  });
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

// ─── Forecast Handler ────────────────────────────────────────

const FORECAST_KEY = "oracle:forecasts:latest";

async function handleForecast(req, res) {
  const markets = await fetchAllMarkets();
  const forecasts = [];

  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;

    const q = pm.question.toLowerCase();
    if (SKIP_KW.some(k => q.includes(k))) continue;
    if (pm.volume < 10000) continue;

    const classification = classifyMarket(pm.question);
    if (!classification) continue;

    const forecast = forecastMarket(pm);
    const decomposition = decomposeQuestion(pm);

    forecasts.push({
      ...forecast,
      classification,
      decomposition,
      score: scoreOpportunity(pm, classification),
    });
  }

  // Sort by absolute edge
  forecasts.sort((a, b) => Math.abs(b.edge_pct) - Math.abs(a.edge_pct));

  const result = {
    timestamp: new Date().toISOString(),
    markets_analyzed: markets.length,
    forecasts_generated: forecasts.length,
    with_edge: forecasts.filter(f => f.signal !== "HOLD").length,
    top_forecasts: forecasts.slice(0, 30),
    agent_summary: {
      base_rate: { description: "Historical resolution patterns, time-decay, category biases" },
      causal: { description: "Volume momentum, spread analysis, liquidity signals" },
      adversarial: { description: "Overconfidence detection, contrarian challenges, mean reversion" },
      crowd: { description: "Volume-weighted market price as crowd wisdom signal" },
    },
    methodology: {
      aggregation: "Confidence-weighted geometric mean",
      calibration: "Logit extremizing (factor=1.5)",
      edge_threshold: "±5% vs market price",
      confidence_threshold: "40% ensemble agreement",
    },
  };

  // Cache the latest forecast in Redis
  await redisSet(FORECAST_KEY, result);

  return res.status(200).json(result);
}

// ─── AI Analysis (Qwen 3.6 Plus via OpenRouter — FREE) ──────

const BRIEF_KEY = "oracle:ai:brief";
const ANALYSIS_KEY = "oracle:ai:analysis";
const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const AI_MODEL = "qwen/qwen3.6-plus:free";

const BASE_URL = process.env.VERCEL_URL
  ? `https://${process.env.VERCEL_URL}`
  : "https://oracle-psi-orpin.vercel.app";

async function fetchJSON(url, timeout = 12000) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeout) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

async function gatherAllData() {
  const [markets, portfolio, strategy, forecast, news, reddit, signals, fred] =
    await Promise.all([
      fetchJSON(`${BASE_URL}/api/markets`),
      fetchJSON(`${BASE_URL}/api/portfolio`),
      fetchJSON(`${BASE_URL}/api/strategy100`),
      fetchJSON(`${BASE_URL}/api/strategy100?view=forecast`),
      fetchJSON(`${BASE_URL}/api/news`),
      fetchJSON(`${BASE_URL}/api/reddit`),
      fetchJSON(`${BASE_URL}/api/signals`),
      fetchJSON(`${BASE_URL}/api/fred`),
    ]);
  return { markets, portfolio, strategy, forecast, news, reddit, signals, fred };
}

function summarizeData(data) {
  const parts = [];
  if (data.portfolio?.account) {
    const a = data.portfolio.account;
    parts.push(`MAIN $10K PORTFOLIO: Value $${a.total_value?.toFixed(2)} | PnL $${a.pnl?.toFixed(2)} (${a.pnl_pct?.toFixed(1)}%)`);
    for (const p of (data.portfolio.positions || []).slice(0, 25))
      parts.push(`  #${p.id} [${p.status === 'closed' ? 'CLOSED' : 'LIVE'}] ${p.question?.slice(0, 60)} | ${p.side} @ ${p.entry_price} | PnL: $${p.pnl?.toFixed(2) || 'n/a'}`);
  }
  if (data.strategy?.account) {
    const a = data.strategy.account;
    parts.push(`\nSTRATEGY $1K: Value $${a.total_value?.toFixed(2)} | Return $${a.total_return?.toFixed(2)} (${a.total_return_pct?.toFixed(1)}%)`);
    for (const p of (data.strategy.positions || []).slice(0, 15))
      parts.push(`  #${p.id} [${p.strategy}] ${p.question?.slice(0, 50)} | ${p.side} @ ${p.entry_price} | PnL: $${p.pnl?.toFixed(2) || 'n/a'}`);
  }
  if (data.forecast?.top_forecasts) {
    parts.push(`\nFORECASTS (${data.forecast.forecasts_generated} generated, ${data.forecast.with_edge} with edge):`);
    for (const f of data.forecast.top_forecasts.slice(0, 10)) {
      const m = f.market || {};
      parts.push(`  [${f.signal}] ${m.question?.slice(0, 55)} | Mkt:${Math.round((m.yes_price || 0) * 100)}c → Fcst:${Math.round((f.aggregation?.final_probability || 0) * 100)}c | Edge:${f.edge_pct?.toFixed(1)}% | ${m.days_to_expiry}d`);
    }
  }
  if (data.news && Array.isArray(data.news)) {
    parts.push(`\nNEWS (${data.news.length} articles):`);
    for (const n of data.news.slice(0, 12))
      parts.push(`  [${n.source}] ${n.title?.slice(0, 80)}`);
  }
  if (data.reddit && Array.isArray(data.reddit) && data.reddit.length > 0 && !data.reddit[0]?.subreddit?.includes('status')) {
    parts.push(`\nREDDIT (${data.reddit.length} posts):`);
    for (const p of data.reddit.slice(0, 8))
      parts.push(`  [${p.score}pts] r/${p.subreddit} — ${p.title?.slice(0, 70)}`);
  }
  if (data.fred && Array.isArray(data.fred)) {
    parts.push(`\nECONOMIC DATA (FRED):`);
    for (const f of data.fred)
      parts.push(`  ${f.name}: ${f.value} (${f.date})`);
  }
  return parts.join('\n');
}

async function callOpenRouter(systemPrompt, userPrompt) {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY not set");

  const r = await fetch(OPENROUTER_URL, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": BASE_URL,
      "X-Title": "ORACLE Superforecaster",
    },
    body: JSON.stringify({
      model: AI_MODEL,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
      temperature: 0.3,
      max_tokens: 4000,
    }),
    signal: AbortSignal.timeout(90000),
  });

  if (!r.ok) throw new Error(`OpenRouter ${r.status}: ${await r.text()}`);
  const resp = await r.json();
  return resp.choices?.[0]?.message?.content || "";
}

const AI_SYSTEM = `You are ORACLE, an elite superforecasting AI for prediction markets.
Analyze through 4 perspectives: Base Rate, Causal, Adversarial, Crowd.
Be calibrated: 70% means 70%, not 90%. Think in EDGE (your probability vs market).
Cite specific news headlines and price levels.`;

function buildAIPrompt(dataSummary) {
  return `Analyze these prediction market portfolios. Today: ${new Date().toISOString().split('T')[0]}.

${dataSummary}

Respond in EXACTLY this JSON (no markdown fences, no extra text, just JSON):
{
  "opportunities": [{"slug":"...","question":"...","market_price":0.23,"ai_probability":0.08,"edge_pct":-15.0,"signal":"BUY NO","thesis":"2-sentence thesis","agents":[{"name":"base_rate","probability":0.05,"confidence":"high","reasoning":"..."},{"name":"causal","probability":0.06,"confidence":"high","reasoning":"..."},{"name":"adversarial","probability":0.15,"confidence":"medium","reasoning":"..."},{"name":"crowd","probability":0.10,"confidence":"medium","reasoning":"..."}]}],
  "health_checks": [{"question":"...","status":"hold|alert|exit","reason":"1-sentence"}],
  "key_insights": ["insight 1","insight 2","insight 3"],
  "market_regime": "1-sentence macro environment"
}

RULES:
- Include ALL opportunities with edge > 3%
- Check EVERY open position against the news
- Flag positions where thesis weakened or date passed
- Signal: BUY YES / BUY NO / HOLD`;
}

async function handleAIAnalyze(req, res) {
  if (!process.env.OPENROUTER_API_KEY) {
    return res.status(500).json({ error: "OPENROUTER_API_KEY not configured" });
  }

  const data = await gatherAllData();
  if (!data.portfolio && !data.strategy) {
    return res.status(500).json({ error: "Failed to fetch portfolio data" });
  }

  const dataSummary = summarizeData(data);
  const aiResponse = await callOpenRouter(AI_SYSTEM, buildAIPrompt(dataSummary));

  // Parse JSON from AI response
  let analysis;
  try {
    let jsonStr = aiResponse;
    const fenceMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (fenceMatch) jsonStr = fenceMatch[1];
    jsonStr = jsonStr.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
    analysis = JSON.parse(jsonStr);
  } catch (e) {
    await redisSet(BRIEF_KEY, aiResponse);
    return res.status(200).json({ status: "partial", message: "AI responded but JSON parse failed", raw: aiResponse.slice(0, 2000), error: e.message });
  }

  // Build result
  const result = {
    timestamp: new Date().toISOString(),
    ai_powered: true,
    model: AI_MODEL,
    markets_analyzed: data.forecast?.forecasts_generated || 0,
    opportunities: analysis.opportunities || [],
    health_checks: analysis.health_checks || [],
    key_insights: analysis.key_insights || [],
    market_regime: analysis.market_regime || "",
  };

  // Build brief text
  const mainVal = data.portfolio?.account?.total_value?.toFixed(0) || "?";
  const mainPnl = data.portfolio?.account?.pnl?.toFixed(0) || "?";
  const mainPct = data.portfolio?.account?.pnl_pct?.toFixed(1) || "?";
  const stratVal = data.strategy?.account?.total_value?.toFixed(0) || "?";
  const stratRet = data.strategy?.account?.total_return?.toFixed(0) || "?";
  const stratPct = data.strategy?.account?.total_return_pct?.toFixed(1) || "?";

  let brief = `=== ORACLE INTELLIGENCE BRIEF ===\n`;
  brief += `Date: ${new Date().toISOString().split('T')[0]} (AI: ${AI_MODEL})\n\n`;
  brief += `--- PORTFOLIO ---\n`;
  brief += `$10K: $${mainVal} ($${mainPnl}, ${mainPct}%) — ${data.portfolio?.positions?.filter(p => p.status !== 'closed').length || 0} positions\n`;
  brief += `$1K:  $${stratVal} ($${stratRet}, ${stratPct}%) — ${data.strategy?.positions?.length || 0} positions\n\n`;

  if (result.opportunities.length > 0) {
    brief += `--- TOP OPPORTUNITIES ---\n`;
    for (const opp of result.opportunities.slice(0, 5)) {
      brief += `[${opp.signal}] ${opp.question}\n`;
      brief += `   Mkt: ${Math.round((opp.market_price || 0) * 100)}c | AI: ${Math.round((opp.ai_probability || 0) * 100)}c | Edge: ${opp.edge_pct > 0 ? '+' : ''}${opp.edge_pct?.toFixed(1)}%\n`;
      brief += `   ${opp.thesis}\n\n`;
    }
  }

  const alerts = (result.health_checks || []).filter(h => h.status === 'alert' || h.status === 'exit');
  if (alerts.length > 0) {
    brief += `--- ALERTS (${alerts.length}) ---\n`;
    for (const a of alerts) brief += `!! ${a.question?.slice(0, 50)} — ${a.reason}\n`;
    brief += `\n`;
  }

  if (result.key_insights?.length > 0) {
    brief += `--- KEY INSIGHTS ---\n`;
    for (const ins of result.key_insights) brief += `- ${ins}\n`;
  }

  if (result.market_regime) brief += `\nRegime: ${result.market_regime}\n`;

  result.brief = brief;

  // Diff alerts vs. last run — push notification only for NEW alerts
  try {
    const prev = await redisGet(ANALYSIS_KEY);
    const prevAlertKeys = new Set(
      (prev?.health_checks || [])
        .filter(h => h.status === 'alert' || h.status === 'exit')
        .map(h => h.question)
    );
    const newAlerts = alerts.filter(a => !prevAlertKeys.has(a.question));
    const topOpps = result.opportunities.filter(o => Math.abs(o.edge_pct || 0) >= 5).slice(0, 3);

    if (newAlerts.length > 0 || topOpps.length > 0) {
      const lines = [];
      for (const a of newAlerts.slice(0, 5)) {
        lines.push(`!! ${a.question?.slice(0, 50)}\n   ${a.reason}`);
      }
      for (const o of topOpps) {
        const edgeSign = o.edge_pct >= 0 ? '+' : '';
        lines.push(`[${o.signal}] ${o.question?.slice(0, 50)}\n   Mkt:${Math.round(o.market_price * 100)}c AI:${Math.round(o.ai_probability * 100)}c Edge:${edgeSign}${o.edge_pct?.toFixed(1)}%`);
      }
      const alertActions = [];
      if (newAlerts[0]?.question) {
        alertActions.push({ label: "Snooze 24h", cmd: "snooze", arg: newAlerts[0].question.slice(0, 40) });
      }
      alertActions.push({ label: "Open dashboard", url: "https://oracle-psi-orpin.vercel.app", method: "GET" });
      notify({
        title: `ORACLE AI: ${newAlerts.length} new alert${newAlerts.length === 1 ? '' : 's'}, ${topOpps.length} opportunit${topOpps.length === 1 ? 'y' : 'ies'}`,
        body: lines.join('\n\n'),
        severity: newAlerts.length > 0 ? "alert" : "info",
        tags: newAlerts.length > 0 ? ["rotating_light", "warning"] : ["brain", "mag"],
        click: "https://oracle-psi-orpin.vercel.app",
        actions: alertActions,
        dedupeKey: newAlerts[0]?.question ? `alert:${newAlerts[0].question.slice(0, 40)}` : undefined,
      }).catch(() => {});
    }
  } catch {}

  await Promise.all([redisSet(ANALYSIS_KEY, result), redisSet(BRIEF_KEY, brief)]);

  return res.status(200).json({
    status: "ok",
    model: AI_MODEL,
    opportunities: result.opportunities.length,
    alerts: alerts.length,
    health_checks: result.health_checks.length,
    brief,
  });
}

// ─── Notification Action Endpoints ───────────────────────────

async function handleNtfyCmd(req, res) {
  const { cmd, arg = "", t } = req.query || {};
  if (!cmd || !t) return res.status(400).json({ error: "missing cmd or token" });
  if (!verifyAction(cmd, arg, t)) return res.status(401).json({ error: "invalid or expired token" });

  if (cmd === "pause") {
    await setTradingEnabled(false);
    await notifyRaw({ title: "ORACLE: trading paused", body: "New trade opens are blocked. Exits still run. Tap Resume to re-enable.", priority: "high", tags: ["pause_button"], actions: [{ label: "Resume trading", cmd: "resume" }] });
    return res.status(200).json({ ok: true, trading_enabled: false });
  }
  if (cmd === "resume") {
    await setTradingEnabled(true);
    await notifyRaw({ title: "ORACLE: trading resumed", body: "Strategies will execute on the next scan.", priority: "default", tags: ["play_button"] });
    return res.status(200).json({ ok: true, trading_enabled: true });
  }
  if (cmd === "snooze") {
    await snoozeAlert(`alert:${arg}`, 24);
    await notifyRaw({ title: "ORACLE: alert snoozed", body: `"${arg.slice(0, 60)}" suppressed for 24h.`, priority: "min", tags: ["zzz"] });
    return res.status(200).json({ ok: true, snoozed: arg });
  }
  if (cmd === "close") {
    const id = Number(arg);
    const portfolio = await loadPortfolio();
    const trade = portfolio.trades.find(tr => tr.id === id && tr.status === "open");
    if (!trade) {
      await notifyRaw({ title: `ORACLE: can't close #${arg}`, body: "Trade not found or already closed.", priority: "default", tags: ["x"] });
      return res.status(404).json({ error: "trade not found or not open" });
    }
    const pm = await fetchPrice(trade.slug).catch(() => null);
    const price = pm ? (trade.side === "yes" ? pm.yes_price : pm.no_price) : trade.entry_price;
    const pnl = price != null ? (price - trade.entry_price) * trade.shares : 0;
    trade.status = "closed";
    trade.close_price = price || trade.entry_price;
    trade.close_reason = "manual_close";
    trade.pnl = Math.round(pnl * 100) / 100;
    trade.pnl_pct = Math.round((pnl / trade.invested) * 10000) / 100;
    trade.closed_at = new Date().toISOString();
    const alloc = portfolio.allocations[trade.strategy];
    if (alloc) { alloc.cash += (trade.invested + trade.pnl); alloc.invested -= trade.invested; }
    portfolio.account.total_cash += (trade.invested + trade.pnl);
    portfolio.stats.total_pnl += trade.pnl;
    if (trade.pnl > 0) portfolio.stats.wins++; else portfolio.stats.losses++;
    await savePortfolio(portfolio);
    await notifyRaw({
      title: `ORACLE: closed #${trade.id} manually`,
      body: `${trade.question?.slice(0, 60)}\n${trade.pnl >= 0 ? '+' : ''}$${trade.pnl} (${trade.pnl_pct}%)`,
      priority: "high",
      tags: trade.pnl >= 0 ? ["money_with_wings"] : ["chart_with_downwards_trend"],
    });
    return res.status(200).json({ ok: true, closed: trade });
  }
  if (cmd === "status") {
    const portfolio = await loadPortfolio();
    const open = portfolio.trades.filter(tr => tr.status === "open");
    const body = `Cash: $${portfolio.account.total_cash.toFixed(0)}\nTotal P&L: ${portfolio.stats.total_pnl >= 0 ? '+' : ''}$${portfolio.stats.total_pnl.toFixed(0)}\nOpen: ${open.length} positions\nTrading: ${await isTradingEnabled() ? 'ENABLED' : 'PAUSED'}`;
    await notifyRaw({ title: "ORACLE status", body, priority: "default", tags: ["bar_chart"] });
    return res.status(200).json({ ok: true });
  }
  return res.status(400).json({ error: "unknown cmd" });
}

async function handleDailyDigest(req, res) {
  const portfolio = await loadPortfolio();
  const analysis = await redisGet(ANALYSIS_KEY);
  const log = (await redisGet(LOG_KEY)) || [];
  const since = Date.now() - 24 * 3600 * 1000;

  const open = portfolio.trades.filter(t => t.status === "open");
  const closedToday = portfolio.trades.filter(t => t.status === "closed" && t.closed_at && new Date(t.closed_at).getTime() > since);
  const openedToday = portfolio.trades.filter(t => t.opened_at && new Date(t.opened_at).getTime() > since);
  const dayPnl = closedToday.reduce((s, t) => s + (t.pnl || 0), 0);
  const totalInvested = Object.values(portfolio.allocations).reduce((s, a) => s + a.invested, 0);
  const totalValue = portfolio.account.total_cash + totalInvested;

  const queue = await flushQueue();
  const alerts = (analysis?.health_checks || []).filter(h => h.status === 'alert' || h.status === 'exit').slice(0, 3);
  const opps = (analysis?.opportunities || []).filter(o => Math.abs(o.edge_pct || 0) >= 5).slice(0, 3);

  const lines = [];
  lines.push(`Portfolio: $${totalValue.toFixed(0)} (cash $${portfolio.account.total_cash.toFixed(0)} + invested $${totalInvested.toFixed(0)})`);
  lines.push(`24h P&L: ${dayPnl >= 0 ? '+' : ''}$${dayPnl.toFixed(2)} across ${closedToday.length} closes, ${openedToday.length} opens`);
  lines.push(`Open positions: ${open.length}`);
  lines.push(`Trading: ${await isTradingEnabled() ? 'ENABLED' : 'PAUSED'}`);
  if (opps.length) {
    lines.push("");
    lines.push("TOP OPPORTUNITIES:");
    for (const o of opps) {
      const edge = o.edge_pct >= 0 ? '+' : '';
      lines.push(`[${o.signal}] ${o.question?.slice(0, 50)} — Edge ${edge}${o.edge_pct?.toFixed(1)}%`);
    }
  }
  if (alerts.length) {
    lines.push("");
    lines.push("ALERTS:");
    for (const a of alerts) lines.push(`!! ${a.question?.slice(0, 50)} — ${a.reason?.slice(0, 80)}`);
  }
  if (queue.length) {
    lines.push("");
    lines.push(`OVERNIGHT QUEUE (${queue.length} deferred):`);
    for (const q of queue.slice(-5)) lines.push(`- ${q.title}`);
  }

  await notifyRaw({
    title: `ORACLE morning digest — ${dayPnl >= 0 ? '+' : ''}$${dayPnl.toFixed(0)} overnight`,
    body: lines.join('\n'),
    priority: "default",
    tags: ["newspaper", dayPnl >= 0 ? "chart_with_upwards_trend" : "chart_with_downwards_trend"],
    click: "https://oracle-psi-orpin.vercel.app",
    actions: [
      { label: "Pause trading", cmd: "pause" },
      { label: "Status", cmd: "status" },
      { label: "Dashboard", url: "https://oracle-psi-orpin.vercel.app", method: "GET" },
    ],
  });

  return res.status(200).json({ ok: true, day_pnl: dayPnl, open: open.length, queue_flushed: queue.length });
}

async function handleHealthPing(req, res) {
  const now = Date.now();
  await redisSet("oracle:health:last_ping", now);

  const analysis = await redisGet(ANALYSIS_KEY);
  const portfolio = await loadPortfolio();
  const briefAgeH = analysis?.timestamp ? (now - new Date(analysis.timestamp).getTime()) / 3600000 : 999;
  const portfolioAgeH = portfolio?.last_updated ? (now - new Date(portfolio.last_updated).getTime()) / 3600000 : 999;

  const problems = [];
  if (briefAgeH > 26) problems.push(`AI brief stale (${briefAgeH.toFixed(0)}h old)`);
  if (portfolioAgeH > 8) problems.push(`Portfolio not updated in ${portfolioAgeH.toFixed(0)}h`);

  if (problems.length) {
    await notifyRaw({
      title: "ORACLE: pipeline warning",
      body: problems.join('\n'),
      priority: "high",
      tags: ["warning", "construction"],
    });
  } else if (req.query?.verbose === "1") {
    await notifyRaw({
      title: "ORACLE: all systems healthy",
      body: `AI brief ${briefAgeH.toFixed(0)}h old, portfolio ${portfolioAgeH.toFixed(0)}h old.`,
      priority: "min",
      tags: ["green_heart"],
    });
  }
  return res.status(200).json({ ok: true, problems, briefAgeH, portfolioAgeH });
}

async function handleRiskCheck(req, res) {
  const portfolio = await loadPortfolio();
  const start = portfolio.account.starting_balance || 1000;
  const totalInvested = Object.values(portfolio.allocations).reduce((s, a) => s + a.invested, 0);
  const totalValue = portfolio.account.total_cash + totalInvested;
  const drawdown = ((start - totalValue) / start) * 100;
  const open = portfolio.trades.filter(t => t.status === "open");

  // Concentration: largest single position as % of portfolio
  const largest = open.reduce((m, t) => Math.max(m, t.invested || 0), 0);
  const concentrationPct = totalValue > 0 ? (largest / totalValue) * 100 : 0;

  const alerts = [];
  if (drawdown >= 10) alerts.push(`Drawdown ${drawdown.toFixed(1)}% from starting balance`);
  if (concentrationPct >= 25) alerts.push(`Single position is ${concentrationPct.toFixed(0)}% of portfolio`);
  if (open.length >= 20) alerts.push(`${open.length} open positions — capacity risk`);

  if (alerts.length) {
    await notifyRaw({
      title: "ORACLE: risk alert",
      body: alerts.join('\n'),
      priority: drawdown >= 15 ? "urgent" : "high",
      tags: ["rotating_light", "warning"],
      actions: [{ label: "Pause trading", cmd: "pause" }, { label: "Status", cmd: "status" }],
    });
  }
  return res.status(200).json({ ok: true, drawdown, concentrationPct, open_count: open.length, alerts });
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
    // AI-powered analysis via Qwen 3.6 Plus (free)
    if (req.query?.action === "ai-analyze") {
      return handleAIAnalyze(req, res);
    }

    // Read cached AI brief
    if (req.query?.action === "ai-brief") {
      const analysis = await redisGet(ANALYSIS_KEY);
      const brief = await redisGet(BRIEF_KEY);
      return res.status(200).json({ analysis, brief, cached: true });
    }

    // Multi-agent forecast endpoint
    if (req.query?.action === "forecast") {
      return handleForecast(req, res);
    }

    // Daily opportunity scanner for main portfolio
    if (req.query?.action === "scan") {
      return handleDailyScan(req, res);
    }

    // Morning digest (drains quiet-hours queue + writes summary to ntfy)
    if (req.query?.action === "daily-digest") {
      return handleDailyDigest(req, res);
    }

    // Silent pipeline heartbeat
    if (req.query?.action === "health-ping") {
      return handleHealthPing(req, res);
    }

    // Portfolio risk check (drawdown / concentration)
    if (req.query?.action === "risk-check") {
      return handleRiskCheck(req, res);
    }

    // ntfy action button callback — HMAC-signed
    if (req.query?.action === "ntfy-cmd") {
      return handleNtfyCmd(req, res);
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

    // Kill switch — opens blocked when trading_enabled=false; closes still run
    const tradingEnabled = await isTradingEnabled();
    if (!tradingEnabled) {
      log.trading_paused = true;
    }

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
    if (runFullScan && tradingEnabled) {
      const bonds = findBonds(markets, openPositions, skipReasons);
      log.scanned.bonds = bonds.length;
      for (const b of bonds.slice(0, 6)) {
        const trade = executeTrade(portfolio, "bonds", b.slug, b.question, b.bond_side, b.bond_price, 50,
          { category: b.category, days_to_expiry: b.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "bonds", slug: b.slug }); }
      }
    }

    // ── Strategy 2: Expertise (max 4 positions, $60 each) ──
    if (runFullScan && tradingEnabled) {
      const expertise = findExpertise(markets, openPositions, skipReasons);
      log.scanned.expertise = expertise.length;

      // Use multi-agent forecast to validate expertise trades
      for (const e of expertise.slice(0, 4)) {
        const pm = { ...e }; // already parsed
        const forecast = forecastMarket(pm);
        // Only enter if forecast doesn't strongly disagree
        if (forecast.signal !== "HOLD" && forecast.aggregation.ensemble_confidence >= 50) {
          const forecastAgrees = (e.trade_side === "yes" && forecast.edge > 0) || (e.trade_side === "no" && forecast.edge < 0);
          if (!forecastAgrees) {
            skipReasons.push({ strategy: "expertise", slug: e.slug, reason: `Forecast disagrees: agents say ${forecast.signal} (conf ${forecast.aggregation.ensemble_confidence}%)` });
            continue;
          }
        }
        const trade = executeTrade(portfolio, "expertise", e.slug, e.question, e.trade_side, e.entry_price, 60,
          { category: e.category, days_to_expiry: e.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "expertise", slug: e.slug, forecast_edge: forecast.edge_pct }); }
      }
    }

    // ── Strategy 3: Crypto 15m — DISABLED (lost $101 on 9 trades, SL doesn't work on binary 15m markets)
    log.scanned.crypto15m = 0;
    skipReasons.push({ strategy: "crypto15m", slug: "all", reason: "Strategy disabled: -$101 lifetime loss, binary market SL failure" });

    // ── Strategy 4: Value (max 3 positions, $40 each) ──
    if (runFullScan && tradingEnabled) {
      const value = findValue(markets, openPositions, skipReasons);
      log.scanned.value = value.length;

      // Use multi-agent forecast to validate value trades
      for (const v of value.slice(0, 3)) {
        const pm = { ...v }; // already parsed
        const forecast = forecastMarket(pm);
        // Only enter if forecast agrees with trade direction (or is neutral)
        if (forecast.signal !== "HOLD") {
          const forecastAgrees = (v.trade_side === "yes" && forecast.edge > 0) || (v.trade_side === "no" && forecast.edge < 0);
          if (!forecastAgrees) {
            skipReasons.push({ strategy: "value", slug: v.slug, reason: `Forecast disagrees: agents say ${forecast.signal}` });
            continue;
          }
        }
        const trade = executeTrade(portfolio, "value", v.slug, v.question, v.trade_side, v.entry_price, 40,
          { category: v.category, days_to_expiry: v.days_to_expiry });
        if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "value", slug: v.slug, forecast_edge: forecast.edge_pct }); }
      }
    }

    // Mark full scan time + run forecast engine
    if (runFullScan) {
      await redisSet(LAST_FULL_SCAN_KEY, new Date().toISOString());

      // Auto-generate forecasts during full scans (cached for dashboard)
      try {
        const forecastResults = [];
        for (const m of markets) {
          const pm = parseMarket(m);
          if (pm.yes_price === null || pm.no_price === null) continue;
          const q = pm.question.toLowerCase();
          if (SKIP_KW.some(k => q.includes(k))) continue;
          if (pm.volume < 10000) continue;
          const classification = classifyMarket(pm.question);
          if (!classification) continue;
          forecastResults.push({
            ...forecastMarket(pm),
            classification,
            decomposition: decomposeQuestion(pm),
            score: scoreOpportunity(pm, classification),
          });
        }
        forecastResults.sort((a, b) => Math.abs(b.edge_pct) - Math.abs(a.edge_pct));
        await redisSet(FORECAST_KEY, {
          timestamp: new Date().toISOString(),
          markets_analyzed: markets.length,
          forecasts_generated: forecastResults.length,
          with_edge: forecastResults.filter(f => f.signal !== "HOLD").length,
          top_forecasts: forecastResults.slice(0, 30),
          agent_summary: {
            base_rate: { description: "Historical resolution patterns, time-decay, category biases" },
            causal: { description: "Volume momentum, spread analysis, liquidity signals" },
            adversarial: { description: "Overconfidence detection, contrarian challenges, mean reversion" },
            crowd: { description: "Volume-weighted market price as crowd wisdom signal" },
          },
          methodology: {
            aggregation: "Confidence-weighted geometric mean",
            calibration: "Logit extremizing (factor=1.5)",
            edge_threshold: "5% vs market price",
            confidence_threshold: "40% ensemble agreement",
          },
        });
        log.scanned.forecasts_generated = forecastResults.length;
      } catch (e) {
        log.scanned.forecast_error = e.message;
      }
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

    // Push notifications for trade activity (non-blocking, best-effort)
    if (executed.length > 0 || closed.length > 0) {
      const lines = [];
      for (const t of executed.slice(0, 5)) {
        lines.push(`OPENED #${t.id} [${t.strategy}] ${t.question?.slice(0, 50)} | ${t.side.toUpperCase()} @ ${(t.entry_price * 100).toFixed(0)}c ($${t.invested})`);
      }
      for (const t of closed.slice(0, 5)) {
        const sign = t.pnl >= 0 ? '+' : '';
        lines.push(`CLOSED #${t.id} [${t.strategy}] ${sign}$${t.pnl} (${sign}${t.pnl_pct}%) — ${t.close_reason}`);
      }
      const extra = (executed.length + closed.length > 10) ? `\n(+${executed.length + closed.length - 10} more)` : '';
      const profit = closed.reduce((s, t) => s + (t.pnl || 0), 0);
      const tradeActions = [];
      if (executed[0]?.id != null) {
        tradeActions.push({ label: `Close #${executed[0].id}`, cmd: "close", arg: String(executed[0].id) });
      }
      tradeActions.push({ label: "Pause trading", cmd: "pause" });
      tradeActions.push({ label: "Dashboard", url: "https://oracle-psi-orpin.vercel.app", method: "GET" });
      const bigMove = Math.abs(profit) >= 100;
      notify({
        title: `ORACLE: ${executed.length} opened, ${closed.length} closed`,
        body: lines.join('\n') + extra + (closed.length > 0 ? `\n\nSession P&L: ${profit >= 0 ? '+' : ''}$${profit.toFixed(2)}` : ''),
        severity: bigMove ? "urgent" : (Math.abs(profit) >= 50 ? "alert" : "trade"),
        tags: profit >= 0 ? ["money_with_wings", "chart_with_upwards_trend"] : ["chart_with_downwards_trend", "warning"],
        click: "https://oracle-psi-orpin.vercel.app",
        actions: tradeActions,
      }).catch(() => {});
    }

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
