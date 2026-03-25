/**
 * ORACLE $100 Strategy — Dashboard Read Endpoint + Auto-Research
 *
 * Reads portfolio from Upstash Redis (written by strategy100-run cron).
 * Falls back to local file if Redis not configured.
 * Enriches open positions with live Polymarket prices.
 *
 * Routes:
 *   GET  ?view=history        — event timeline
 *   GET  ?view=research       — experiment log + current best params
 *   GET  ?view=research&reset=1 — reset research to defaults
 *   POST ?action=research-run — run one experiment cycle (called by QStash)
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

const PORTFOLIO_KEY = "oracle:strategy100:portfolio";
const LOG_KEY = "oracle:strategy100:log";
const MAIN_PORTFOLIO_KEY = "oracle:portfolio:main";
const PRICE_HISTORY_KEY = "oracle:price_history";

// ─── Auto-Research Constants ─────────────────────────────────────
const RESEARCH_PARAMS_KEY = "oracle:research:params";
const RESEARCH_BASELINE_KEY = "oracle:research:baseline";
const RESEARCH_LOG_KEY = "oracle:research:log";

const DEFAULT_RESEARCH_PARAMS = {
  bonds: {
    min_price: 0.93, take_profit_pct: 7, stop_loss_pct: 5,
    max_positions: 6, position_size: 50, min_volume: 50000,
    min_days_to_expiry: 14, maturity_exit: 0.98,
  },
  expertise: {
    min_edge_pct: 5, take_profit_pct: 15, stop_loss_pct: 10,
    max_positions: 3, position_size: 50, min_volume: 10000,
    min_days_to_expiry: 30, price_range: [0.15, 0.85], min_sentiment_hits: 1,
  },
  crypto15m: {
    momentum_threshold: 1.0, max_entry_price: 0.55,
    take_profit_pct: 20, stop_loss_pct: 12, max_positions: 2, position_size: 30,
  },
  value: {
    min_volume: 100000, take_profit_pct: 25, stop_loss_pct: 15,
    max_positions: 2, position_size: 25, price_range: [0.15, 0.70],
    spread_threshold: 0.02, min_days_to_expiry: 30,
  },
  fusion_weights: {
    narrative_dominance: 0.25, rsi: 0.08, macd: 0.08,
    reddit_velocity: 0.08, price_momentum: 0.06, ensemble_model: 0.07,
  },
};

const RESEARCH_PROGRAM = {
  objectives: [
    "Maximize risk-adjusted returns (Sharpe-like ratio on closed trades)",
    "Reduce drawdown by tightening stop-losses on underperforming strategies",
    "Find optimal TP/SL ratios per strategy based on historical win rates",
    "Tune entry thresholds to filter low-quality signals",
    "Optimize position sizing relative to strategy edge",
  ],
  constraints: [
    "Never set stop_loss below 3% (avoid noise exits)",
    "Never set take_profit above 40% (unrealistic for prediction markets)",
    "Position sizes must stay between $10-$100",
    "Maintain diversification: max_positions bounds [1, 8]",
    "Entry prices must be realistic for Polymarket (0.05-0.97)",
  ],
  mutation_budget: 1,
};

const PARAM_RANGES = {
  "bonds.min_price":           { min: 0.88, max: 0.97, step: 0.01 },
  "bonds.take_profit_pct":     { min: 3,    max: 15,   step: 1 },
  "bonds.stop_loss_pct":       { min: 3,    max: 10,   step: 1 },
  "bonds.position_size":       { min: 20,   max: 100,  step: 10 },
  "bonds.min_volume":          { min: 20000, max: 200000, step: 10000 },
  "bonds.min_days_to_expiry":  { min: 7,    max: 30,   step: 1 },
  "bonds.maturity_exit":       { min: 0.95, max: 0.99, step: 0.01 },
  "expertise.min_edge_pct":    { min: 3,    max: 15,   step: 1 },
  "expertise.take_profit_pct": { min: 8,    max: 30,   step: 1 },
  "expertise.stop_loss_pct":   { min: 5,    max: 20,   step: 1 },
  "expertise.position_size":   { min: 20,   max: 100,  step: 10 },
  "crypto15m.momentum_threshold": { min: 0.3, max: 3.0, step: 0.1 },
  "crypto15m.max_entry_price":   { min: 0.40, max: 0.65, step: 0.05 },
  "crypto15m.take_profit_pct":   { min: 10,   max: 35,   step: 1 },
  "crypto15m.stop_loss_pct":     { min: 5,    max: 20,   step: 1 },
  "value.take_profit_pct":     { min: 15,   max: 40,   step: 1 },
  "value.stop_loss_pct":       { min: 8,    max: 25,   step: 1 },
  "value.spread_threshold":    { min: 0.01, max: 0.05, step: 0.005 },
  "fusion_weights.narrative_dominance": { min: 0.10, max: 0.40, step: 0.05 },
  "fusion_weights.rsi":                 { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.macd":                { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.reddit_velocity":     { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.price_momentum":      { min: 0.02, max: 0.12, step: 0.01 },
  "fusion_weights.ensemble_model":      { min: 0.02, max: 0.15, step: 0.01 },
};

const EMPTY_PORTFOLIO = {
  account: { starting_balance: 1000, total_cash: 1000 },
  allocations: {
    bonds: { budget: 500, cash: 500, invested: 0 },
    expertise: { budget: 250, cash: 250, invested: 0 },
    crypto15m: { budget: 150, cash: 150, invested: 0 },
    value: { budget: 100, cash: 100, invested: 0 },
  },
  trades: [],
  stats: { total_trades: 0, wins: 0, losses: 0, total_pnl: 0 },
  initialized: false,
};

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");

  if (req.method === "OPTIONS") return res.status(204).end();

  // History view: ?view=history
  if (req.query?.view === "history") {
    return handleHistory(req, res);
  }

  // Research views: ?view=research, ?action=research-run
  if (req.query?.view === "research") {
    return handleResearchRead(req, res);
  }
  if (req.query?.action === "research-run" || (req.method === "POST" && req.query?.view === "research")) {
    return handleResearchRun(req, res);
  }

  try {
    // Try Redis first, then local file fallback
    let portfolio = null;

    if (isRedisConfigured()) {
      portfolio = await redisGet(PORTFOLIO_KEY);
    }

    if (!portfolio) {
      // Fallback: local file (works in dev or if Redis empty)
      const filePath = join(process.cwd(), 'nerve', 'data', 'strategy_100_portfolio.json');
      if (existsSync(filePath)) {
        portfolio = JSON.parse(readFileSync(filePath, 'utf8'));
      }
    }

    if (!portfolio) {
      return res.status(200).json(EMPTY_PORTFOLIO);
    }

    const openTrades = portfolio.trades.filter(t => t.status === "open");

    // Fetch live prices for open positions
    const results = await Promise.allSettled(
      openTrades.map(trade =>
        fetch(`https://gamma-api.polymarket.com/markets?slug=${trade.slug}`)
          .then(r => r.ok ? r.json() : [])
      )
    );

    let total_invested = 0;
    let positions_value = 0;

    const livePositions = openTrades.map((trade, i) => {
      const result = results[i];
      let current_price = null;
      let current_value = null;
      let pnl = null;
      let pnl_pct = null;

      if (result.status === "fulfilled" && result.value.length > 0) {
        try {
          const prices = JSON.parse(result.value[0].outcomePrices);
          const yes_price = parseFloat(prices[0]);
          const no_price = parseFloat(prices[1]);
          current_price = trade.side === "yes" ? yes_price : no_price;
          current_value = parseFloat((trade.shares * current_price).toFixed(2));
          pnl = parseFloat((current_value - trade.invested).toFixed(2));
          pnl_pct = parseFloat(((current_value / trade.invested - 1) * 100).toFixed(2));
          total_invested += trade.invested;
          positions_value += current_value;
        } catch (e) {}
      }

      return { ...trade, current_price, current_value, pnl, pnl_pct };
    });

    const closedTrades = portfolio.trades.filter(t => t.status === "closed");
    const total_value = portfolio.account.total_cash + positions_value;
    const total_return = total_value - portfolio.account.starting_balance;

    return res.status(200).json({
      account: {
        ...portfolio.account,
        positions_value: parseFloat(positions_value.toFixed(2)),
        total_value: parseFloat(total_value.toFixed(2)),
        total_return: parseFloat(total_return.toFixed(2)),
        total_return_pct: parseFloat(((total_return / portfolio.account.starting_balance) * 100).toFixed(2)),
      },
      allocations: portfolio.allocations,
      positions: livePositions,
      closed_trades: closedTrades,
      stats: portfolio.stats,
      source: isRedisConfigured() ? "redis" : "file",
      updated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: "Failed to fetch strategy portfolio", detail: err.message });
  }
}

function portfolioTradeEvents(portfolio, label, idFn, strategyFn) {
  if (!portfolio || !Array.isArray(portfolio.trades)) return [];
  return portfolio.trades.map(t => ({
    type: t.status === "closed" ? "trade_closed" : "trade_opened",
    timestamp: t.closed_at || t.date || portfolio.created,
    detail: {
      id: idFn(t), strategy: strategyFn(t), slug: t.slug,
      question: t.question || t.market, side: t.side,
      entry_price: t.entry_price, exit_price: t.exit_price || null,
      invested: t.invested, shares: t.shares,
      pnl: t.pnl || null, pnl_pct: t.pnl_pct || null,
      close_reason: t.close_reason || null, status: t.status, portfolio: label,
    },
  }));
}

// ─── Auto-Research Helpers ────────────────────────────────────────

function deepClone(obj) { return JSON.parse(JSON.stringify(obj)); }

function getNestedValue(obj, path) {
  const keys = path.split(".");
  let v = obj;
  for (const k of keys) { v = v?.[k]; }
  return v;
}

function setNestedValue(obj, path, value) {
  const keys = path.split(".");
  let v = obj;
  for (let i = 0; i < keys.length - 1; i++) { v = v[keys[i]]; }
  v[keys[keys.length - 1]] = value;
}

function mutateParams(currentParams) {
  const candidate = deepClone(currentParams);
  const paramKeys = Object.keys(PARAM_RANGES);
  const key = paramKeys[Math.floor(Math.random() * paramKeys.length)];
  const range = PARAM_RANGES[key];
  const oldValue = getNestedValue(candidate, key);
  const direction = Math.random() < 0.5 ? -1 : 1;
  let newValue = oldValue + direction * range.step;
  newValue = Math.max(range.min, Math.min(range.max, newValue));
  newValue = Math.round(newValue * 10000) / 10000;
  setNestedValue(candidate, key, newValue);
  return { candidate, mutation: { parameter: key, old_value: oldValue, new_value: newValue, direction: direction > 0 ? "increase" : "decrease" } };
}

function backtestParams(trades, params) {
  let totalPnl = 0, wins = 0, losses = 0, maxDrawdown = 0, peak = 0, equity = 0;
  for (const trade of trades) {
    if (trade.status !== "closed" || !trade.entry_price || !trade.exit_price) continue;
    const stratParams = params[trade.strategy];
    if (!stratParams) continue;
    const tp = stratParams.take_profit_pct / 100;
    const sl = stratParams.stop_loss_pct / 100;
    const posSize = stratParams.position_size || trade.invested;
    const priceChange = (trade.exit_price - trade.entry_price) / trade.entry_price;
    const pctMove = priceChange * (trade.side === "no" ? -1 : 1);
    const simulatedPct = pctMove >= tp ? tp : pctMove <= -sl ? -sl : pctMove;
    const simulatedPnl = posSize * simulatedPct;
    totalPnl += simulatedPnl;
    equity += simulatedPnl;
    if (simulatedPnl > 0) wins++; else losses++;
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }
  const totalTrades = wins + losses;
  const winRate = totalTrades > 0 ? wins / totalTrades : 0;
  const sharpe = totalPnl / Math.max(maxDrawdown, 1);
  return {
    total_pnl: Math.round(totalPnl * 100) / 100, wins, losses,
    win_rate: Math.round(winRate * 1000) / 10,
    max_drawdown: Math.round(maxDrawdown * 100) / 100,
    sharpe: Math.round(sharpe * 1000) / 1000, trades_evaluated: totalTrades,
  };
}

async function handleResearchRead(req, res) {
  if (!isRedisConfigured()) return res.status(500).json({ error: "Redis not configured" });
  try {
    if (req.query?.reset === "1") {
      await redisSet(RESEARCH_PARAMS_KEY, DEFAULT_RESEARCH_PARAMS);
      await redisSet(RESEARCH_BASELINE_KEY, null);
      await redisSet(RESEARCH_LOG_KEY, []);
      return res.status(200).json({ status: "reset", params: DEFAULT_RESEARCH_PARAMS });
    }
    const params = (await redisGet(RESEARCH_PARAMS_KEY)) || DEFAULT_RESEARCH_PARAMS;
    const baseline = (await redisGet(RESEARCH_BASELINE_KEY)) || null;
    const log = (await redisGet(RESEARCH_LOG_KEY)) || [];
    const accepted = log.filter(e => e.accepted);
    let cumulativePnlDelta = 0;
    for (const entry of accepted) {
      cumulativePnlDelta += (entry.candidate_result?.total_pnl || 0) - (entry.baseline_result?.total_pnl || 0);
    }
    return res.status(200).json({
      status: "ok", current_params: params, baseline,
      experiments: {
        total: log.length, accepted: accepted.length, rejected: log.length - accepted.length,
        acceptance_rate: log.length > 0 ? Math.round((accepted.length / log.length) * 1000) / 10 : 0,
        cumulative_pnl_delta: Math.round(cumulativePnlDelta * 100) / 100,
      },
      last_experiment: log[log.length - 1] || null,
      log: log.slice(-50),
      research_program: RESEARCH_PROGRAM,
    });
  } catch (err) { return res.status(500).json({ error: err.message }); }
}

async function handleResearchRun(req, res) {
  if (!isRedisConfigured()) return res.status(500).json({ error: "Redis not configured" });
  try {
    const portfolio = await redisGet(PORTFOLIO_KEY);
    if (!portfolio?.trades) return res.status(200).json({ status: "skipped", reason: "No trade history" });
    const closedTrades = portfolio.trades.filter(t => t.status === "closed");
    if (closedTrades.length < 3) return res.status(200).json({ status: "skipped", reason: `Need 3+ closed trades (have ${closedTrades.length})` });

    const currentParams = (await redisGet(RESEARCH_PARAMS_KEY)) || deepClone(DEFAULT_RESEARCH_PARAMS);
    const baselineResult = backtestParams(closedTrades, currentParams);
    const { candidate, mutation } = mutateParams(currentParams);
    const candidateResult = backtestParams(closedTrades, candidate);

    const pnlImproved = candidateResult.total_pnl > baselineResult.total_pnl;
    const sharpeImproved = candidateResult.sharpe > baselineResult.sharpe;
    const drawdownWorse = candidateResult.max_drawdown > baselineResult.max_drawdown * 1.2;
    const accepted = (pnlImproved || sharpeImproved) && !drawdownWorse;

    const experiment = {
      id: Date.now(), timestamp: new Date().toISOString(), mutation,
      baseline_result: baselineResult, candidate_result: candidateResult, accepted,
      reason: accepted
        ? `+$${(candidateResult.total_pnl - baselineResult.total_pnl).toFixed(2)} PnL, ${candidateResult.sharpe.toFixed(3)} Sharpe`
        : drawdownWorse
          ? `Drawdown increased: $${candidateResult.max_drawdown.toFixed(2)} vs $${baselineResult.max_drawdown.toFixed(2)}`
          : `No improvement: $${candidateResult.total_pnl.toFixed(2)} vs $${baselineResult.total_pnl.toFixed(2)}`,
    };
    const log = (await redisGet(RESEARCH_LOG_KEY)) || [];
    log.push(experiment);
    await redisSet(RESEARCH_LOG_KEY, log.slice(-200));
    if (accepted) {
      await redisSet(RESEARCH_PARAMS_KEY, candidate);
      await redisSet(RESEARCH_BASELINE_KEY, candidateResult);
    }
    return res.status(200).json({ status: "ok", experiment, current_params: accepted ? candidate : currentParams, trades_evaluated: closedTrades.length });
  } catch (err) { return res.status(500).json({ error: err.message }); }
}

// ─── History Handler ─────────────────────────────────────────────

async function handleHistory(req, res) {
  try {
    const events = [];

    if (isRedisConfigured()) {
      const [execLog, s100, main, priceHistory] = await Promise.all([
        redisGet(LOG_KEY),
        redisGet(PORTFOLIO_KEY),
        redisGet(MAIN_PORTFOLIO_KEY),
        redisGet(PRICE_HISTORY_KEY),
      ]);

      // 1. Strategy execution logs (cron runs)
      if (Array.isArray(execLog)) {
        for (const entry of execLog) {
          const ts = entry.timestamp || entry.cycle;
          events.push({
            type: "scan", timestamp: ts,
            detail: {
              markets_scanned: entry.scanned?.total_markets || 0,
              bonds_found: entry.scanned?.bonds || 0,
              expertise_found: entry.scanned?.expertise || 0,
              crypto15m_found: entry.scanned?.crypto15m || 0,
              value_found: entry.scanned?.value || 0,
              crashes_found: entry.scanned?.crashes || 0,
              full_scan: entry.scanned?.full_scan ?? true,
              executed: entry.executed?.length || 0,
              closed: entry.closed?.length || 0,
              trades: entry.executed || [],
              exits: entry.closed || [],
              skipped: entry.skipped || [],
            },
          });

          if (Array.isArray(entry.executed)) {
            for (const t of entry.executed) {
              events.push({
                type: "trade_open", timestamp: ts,
                detail: { strategy: t.strategy, slug: t.slug, id: t.id },
              });
            }
          }
          if (Array.isArray(entry.closed)) {
            for (const t of entry.closed) {
              events.push({
                type: "trade_close", timestamp: ts,
                detail: { id: t.id, pnl: t.pnl, reason: t.reason },
              });
            }
          }
        }
      }

      // 2. Portfolio trades ($100 + $10K)
      events.push(...portfolioTradeEvents(s100, "$100", t => t.id, t => t.strategy));
      events.push(...portfolioTradeEvents(main, "$10K", t => `main-${t.id || t.slug}`, () => "manual"));

      // 3. Price history summary
      if (priceHistory && typeof priceHistory === "object") {
        for (const [slug, points] of Object.entries(priceHistory)) {
          if (Array.isArray(points) && points.length > 0) {
            events.push({
              type: "price_tracking",
              timestamp: points[points.length - 1]?.ts || new Date().toISOString(),
              detail: {
                slug, data_points: points.length,
                latest_price: points[points.length - 1]?.price,
                first_price: points[0]?.price,
                first_seen: points[0]?.ts,
              },
            });
          }
        }
      }
    }

    events.sort((a, b) => {
      const ta = new Date(a.timestamp || 0).getTime();
      const tb = new Date(b.timestamp || 0).getTime();
      return tb - ta;
    });

    return res.status(200).json({
      events,
      total: events.length,
      generated_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(500).json({ error: "Failed to fetch history", detail: err.message });
  }
}
