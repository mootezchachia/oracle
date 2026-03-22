/**
 * ORACLE $100 Strategy — Serverless Execution Endpoint
 *
 * GET  /api/strategy100-run         — run full scan + auto-execute
 * GET  /api/strategy100-run?reset=1 — reset portfolio to $100
 *
 * Triggered by Vercel Cron or external scheduler (Upstash QStash, GitHub Actions, etc.)
 * Stores portfolio state in Upstash Redis.
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';

const GAMMA_API = "https://gamma-api.polymarket.com";
const PORTFOLIO_KEY = "oracle:strategy100:portfolio";
const LOG_KEY = "oracle:strategy100:log";

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

// ─── Portfolio ────────────────────────────────────────────────

function newPortfolio() {
  return {
    account: { starting_balance: 100, total_cash: 100 },
    allocations: {
      bonds: { budget: 60, cash: 60, invested: 0 },
      expertise: { budget: 30, cash: 30, invested: 0 },
      flash_crash: { budget: 10, cash: 10, invested: 0 },
    },
    trades: [],
    stats: { total_trades: 0, wins: 0, losses: 0, total_pnl: 0, best_trade: 0, worst_trade: 0 },
    created: new Date().toISOString(),
    last_updated: new Date().toISOString(),
  };
}

async function loadPortfolio() {
  const saved = await redisGet(PORTFOLIO_KEY);
  return saved || newPortfolio();
}

async function savePortfolio(portfolio) {
  portfolio.last_updated = new Date().toISOString();
  portfolio.account.total_cash = Object.values(portfolio.allocations)
    .reduce((sum, a) => sum + a.cash, 0);
  await redisSet(PORTFOLIO_KEY, portfolio);
}

async function appendLog(entry) {
  // Store last 200 log entries
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

function parseMarket(m) {
  let prices = [];
  try { prices = JSON.parse(m.outcomePrices || "[]"); } catch {}
  prices = prices.map(p => parseFloat(p));

  return {
    market_id: m.id || "",
    slug: m.slug || "",
    question: m.question || "",
    yes_price: prices[0] ?? null,
    no_price: prices[1] ?? null,
    volume: parseFloat(m.volume || 0) || 0,
    end_date: m.endDate || "",
  };
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

function findBonds(markets) {
  const bonds = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 50000) continue;

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

// ─── Strategy 2: Expertise ───────────────────────────────────

function findExpertise(markets) {
  const opps = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 10000) continue;
    if (pm.yes_price < 0.20 || pm.yes_price > 0.80) continue;

    const q = pm.question.toLowerCase();
    if (!SENTIMENT_KW.some(k => q.includes(k))) continue;
    if (SKIP_KW.some(k => q.includes(k))) continue;

    // Simple contrarian signal: high-sentiment markets with mid-range prices
    const sentiment = SENTIMENT_KW.filter(k => q.includes(k)).length;
    if (sentiment < 2) continue;

    const direction = pm.yes_price > 0.5 ? -1 : 1; // contrarian
    const side = direction > 0 ? "yes" : "no";
    const entry_price = side === "yes" ? pm.yes_price : pm.no_price;
    const edge_pct = Math.abs(pm.yes_price - 0.5) * 100;

    if (edge_pct >= 8) {
      opps.push({
        ...pm, trade_side: side, entry_price, edge_pct: Math.round(edge_pct * 100) / 100,
        sentiment_hits: sentiment,
      });
    }
  }
  opps.sort((a, b) => b.edge_pct - a.edge_pct);
  return opps;
}

// ─── Strategy 3: Flash Crash ─────────────────────────────────

function findCrashes(markets) {
  const crashes = [];
  for (const m of markets) {
    const pm = parseMarket(m);
    if (pm.yes_price === null || pm.no_price === null) continue;
    if (pm.volume < 20000) continue;

    const q = pm.question.toLowerCase();
    if (SKIP_KW.some(k => q.includes(k))) continue;

    const minPrice = 0.15;
    const maxPrice = 0.70;

    if (pm.yes_price >= minPrice && pm.yes_price <= maxPrice && pm.volume >= 100000) {
      const signal = (pm.volume / 1000000) * (1 - pm.yes_price);
      if (signal > 0.1) {
        crashes.push({
          ...pm, crash_side: "yes", crash_price: pm.yes_price,
          crash_signal: Math.round(signal * 10000) / 10000,
          upside_pct: Math.round(((1.0 / pm.yes_price) - 1) * 100),
        });
      }
    }
    if (pm.no_price >= minPrice && pm.no_price <= maxPrice && pm.volume >= 100000) {
      const signal = (pm.volume / 1000000) * (1 - pm.no_price);
      if (signal > 0.1) {
        crashes.push({
          ...pm, crash_side: "no", crash_price: pm.no_price,
          crash_signal: Math.round(signal * 10000) / 10000,
          upside_pct: Math.round(((1.0 / pm.no_price) - 1) * 100),
        });
      }
    }
  }
  crashes.sort((a, b) => b.crash_signal - a.crash_signal);
  return crashes;
}

// ─── Trade Execution ─────────────────────────────────────────

function executeTrade(portfolio, strategy, slug, question, side, price, maxSize) {
  const alloc = portfolio.allocations[strategy];
  const size = Math.min(maxSize, alloc.cash);
  if (size < 3) return null;

  // Check for duplicate
  if (portfolio.trades.some(t => t.slug === slug && t.status === "open")) return null;

  const shares = Math.round((size / price) * 10000) / 10000;
  const trade = {
    id: portfolio.trades.length + 1,
    strategy, slug, question, side,
    entry_price: Math.round(price * 10000) / 10000,
    shares, invested: Math.round(size * 100) / 100,
    date: new Date().toISOString().slice(0, 10),
    status: "open",
    take_profit_pct: strategy === "flash_crash" ? 25 : 15,
    stop_loss_pct: strategy === "flash_crash" ? 15 : 10,
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
    if (pnlPct >= trade.take_profit_pct) closeReason = `Take profit: ${pnlPct.toFixed(1)}%`;
    else if (pnlPct <= -trade.stop_loss_pct) closeReason = `Stop loss: ${pnlPct.toFixed(1)}%`;
    if (trade.strategy === "bonds" && currentPrice >= 0.99) closeReason = `Bond matured: ${pnlPct.toFixed(1)}%`;

    if (closeReason) {
      trade.status = "closed";
      trade.exit_price = Math.round(currentPrice * 10000) / 10000;
      trade.current_value = Math.round(currentValue * 100) / 100;
      trade.pnl = Math.round(pnl * 100) / 100;
      trade.pnl_pct = Math.round(pnlPct * 100) / 100;
      trade.close_reason = closeReason;
      trade.closed_at = new Date().toISOString();

      const alloc = portfolio.allocations[trade.strategy];
      alloc.cash = Math.round((alloc.cash + currentValue) * 100) / 100;
      alloc.invested = Math.round((alloc.invested - trade.invested) * 100) / 100;
      portfolio.stats.total_pnl = Math.round((portfolio.stats.total_pnl + pnl) * 100) / 100;
      if (pnl > 0) { portfolio.stats.wins++; portfolio.stats.best_trade = Math.max(portfolio.stats.best_trade, pnl); }
      else { portfolio.stats.losses++; portfolio.stats.worst_trade = Math.min(portfolio.stats.worst_trade, pnl); }

      closed.push(trade);
    }
  }
  return closed;
}

// ─── Main Handler ────────────────────────────────────────────

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-cache");

  if (!isRedisConfigured()) {
    return res.status(500).json({
      error: "Upstash Redis not configured",
      help: "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in Vercel env vars",
    });
  }

  try {
    // Reset
    if (req.query?.reset === "1") {
      const p = newPortfolio();
      await savePortfolio(p);
      return res.status(200).json({ status: "reset", portfolio: p });
    }

    const portfolio = await loadPortfolio();
    const executed = [];
    const log = { cycle: new Date().toISOString(), executed: [], closed: [], scanned: {} };

    // Fetch markets
    const markets = await fetchAllMarkets();
    log.scanned.total_markets = markets.length;

    // Strategy 1: Bonds (max 4 positions, $15 each)
    const bonds = findBonds(markets);
    log.scanned.bonds = bonds.length;
    for (const b of bonds.slice(0, 4)) {
      const trade = executeTrade(portfolio, "bonds", b.slug, b.question, b.bond_side, b.bond_price, 15);
      if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "bonds", slug: b.slug }); }
    }

    // Strategy 2: Expertise (max 2 positions, $15 each)
    const expertise = findExpertise(markets);
    log.scanned.expertise = expertise.length;
    for (const e of expertise.slice(0, 2)) {
      const trade = executeTrade(portfolio, "expertise", e.slug, e.question, e.trade_side, e.entry_price, 15);
      if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "expertise", slug: e.slug }); }
    }

    // Strategy 3: Flash crash (max 1 position, $10)
    const crashes = findCrashes(markets);
    log.scanned.crashes = crashes.length;
    for (const c of crashes.slice(0, 1)) {
      const trade = executeTrade(portfolio, "flash_crash", c.slug, c.question, c.crash_side, c.crash_price, 10);
      if (trade) { executed.push(trade); log.executed.push({ id: trade.id, strategy: "flash_crash", slug: c.slug }); }
    }

    // Check TP/SL exits
    const closed = await checkExits(portfolio);
    log.closed = closed.map(t => ({ id: t.id, pnl: t.pnl, reason: t.close_reason }));

    // Save
    await savePortfolio(portfolio);
    await appendLog(log);

    return res.status(200).json({
      status: "ok",
      executed: executed.length,
      closed: closed.length,
      portfolio_value: portfolio.account.total_cash +
        Object.values(portfolio.allocations).reduce((s, a) => s + a.invested, 0),
      total_pnl: portfolio.stats.total_pnl,
      scanned: log.scanned,
      trades: executed,
      exits: closed,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
