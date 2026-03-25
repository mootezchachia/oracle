/**
 * ORACLE Auto-Research — Autonomous Strategy Experimentation
 *
 * Inspired by Karpathy's AutoResearch: an autonomous loop that
 * mutates strategy parameters, backtests against real trade history,
 * and accepts/rejects based on simulated P&L improvement.
 *
 * GET  /api/auto-research              — view experiment log + current best params
 * POST /api/auto-research              — run one experiment cycle (called by QStash)
 * GET  /api/auto-research?reset=1      — reset to default params
 *
 * Like AutoResearch's train.py, the MUTABLE surface is strategy parameters only.
 * Like program.md, the research directive guides what to explore.
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';

const PARAMS_KEY    = "oracle:research:params";
const BASELINE_KEY  = "oracle:research:baseline";
const LOG_KEY       = "oracle:research:log";
const PORTFOLIO_KEY = "oracle:strategy100:portfolio";

// ─── Default Strategy Parameters (the "train.py") ────────────────

const DEFAULT_PARAMS = {
  bonds: {
    min_price: 0.93,       // entry threshold
    take_profit_pct: 7,
    stop_loss_pct: 5,
    max_positions: 6,
    position_size: 50,
    min_volume: 50000,
    min_days_to_expiry: 14,
    maturity_exit: 0.98,
  },
  expertise: {
    min_edge_pct: 5,
    take_profit_pct: 15,
    stop_loss_pct: 10,
    max_positions: 3,
    position_size: 50,
    min_volume: 10000,
    min_days_to_expiry: 30,
    price_range: [0.15, 0.85],
    min_sentiment_hits: 1,
  },
  crypto15m: {
    momentum_threshold: 1.0,   // % 1h change to trigger
    max_entry_price: 0.55,
    take_profit_pct: 20,
    stop_loss_pct: 12,
    max_positions: 2,
    position_size: 30,
  },
  value: {
    min_volume: 100000,
    take_profit_pct: 25,
    stop_loss_pct: 15,
    max_positions: 2,
    position_size: 25,
    price_range: [0.15, 0.70],
    spread_threshold: 0.02,
    min_days_to_expiry: 30,
  },
  // Global fusion weights (maps to signal_fusion.py DEFAULT_WEIGHTS)
  fusion_weights: {
    narrative_dominance: 0.25,
    rsi: 0.08,
    macd: 0.08,
    reddit_velocity: 0.08,
    price_momentum: 0.06,
    ensemble_model: 0.07,
  },
};

// ─── Research Program (the "program.md") ─────────────────────────
// Guides what the autonomous agent should explore each cycle.

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
  mutation_budget: 1, // mutate 1 parameter per experiment (like AutoResearch's single-file constraint)
};

// ─── Mutation Engine ─────────────────────────────────────────────

const PARAM_RANGES = {
  // Bonds
  "bonds.min_price":           { min: 0.88, max: 0.97, step: 0.01 },
  "bonds.take_profit_pct":     { min: 3,    max: 15,   step: 1 },
  "bonds.stop_loss_pct":       { min: 3,    max: 10,   step: 1 },
  "bonds.position_size":       { min: 20,   max: 100,  step: 10 },
  "bonds.min_volume":          { min: 20000, max: 200000, step: 10000 },
  "bonds.min_days_to_expiry":  { min: 7,    max: 30,   step: 1 },
  "bonds.maturity_exit":       { min: 0.95, max: 0.99, step: 0.01 },
  // Expertise
  "expertise.min_edge_pct":    { min: 3,    max: 15,   step: 1 },
  "expertise.take_profit_pct": { min: 8,    max: 30,   step: 1 },
  "expertise.stop_loss_pct":   { min: 5,    max: 20,   step: 1 },
  "expertise.position_size":   { min: 20,   max: 100,  step: 10 },
  // Crypto15m
  "crypto15m.momentum_threshold": { min: 0.3, max: 3.0, step: 0.1 },
  "crypto15m.max_entry_price":   { min: 0.40, max: 0.65, step: 0.05 },
  "crypto15m.take_profit_pct":   { min: 10,   max: 35,   step: 1 },
  "crypto15m.stop_loss_pct":     { min: 5,    max: 20,   step: 1 },
  // Value
  "value.take_profit_pct":     { min: 15,   max: 40,   step: 1 },
  "value.stop_loss_pct":       { min: 8,    max: 25,   step: 1 },
  "value.spread_threshold":    { min: 0.01, max: 0.05, step: 0.005 },
  // Fusion weights
  "fusion_weights.narrative_dominance": { min: 0.10, max: 0.40, step: 0.05 },
  "fusion_weights.rsi":                 { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.macd":                { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.reddit_velocity":     { min: 0.02, max: 0.15, step: 0.01 },
  "fusion_weights.price_momentum":      { min: 0.02, max: 0.12, step: 0.01 },
  "fusion_weights.ensemble_model":      { min: 0.02, max: 0.15, step: 0.01 },
};

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

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

  // Mutate: random step up or down within bounds
  const direction = Math.random() < 0.5 ? -1 : 1;
  let newValue = oldValue + direction * range.step;
  newValue = Math.max(range.min, Math.min(range.max, newValue));
  // Round to avoid floating point noise
  newValue = Math.round(newValue * 10000) / 10000;

  setNestedValue(candidate, key, newValue);

  return {
    candidate,
    mutation: {
      parameter: key,
      old_value: oldValue,
      new_value: newValue,
      direction: direction > 0 ? "increase" : "decrease",
    },
  };
}

// ─── Backtester (the "5-minute experiment") ──────────────────────
// Replays closed trades with candidate parameters to estimate P&L delta.

function backtestParams(trades, params) {
  let totalPnl = 0;
  let wins = 0;
  let losses = 0;
  let maxDrawdown = 0;
  let peak = 0;
  let equity = 0;

  for (const trade of trades) {
    if (trade.status !== "closed") continue;
    if (!trade.entry_price || !trade.exit_price) continue;

    const strategy = trade.strategy;
    const stratParams = params[strategy];
    if (!stratParams) continue;

    // Simulate with candidate TP/SL
    const tp = stratParams.take_profit_pct / 100;
    const sl = stratParams.stop_loss_pct / 100;
    const posSize = stratParams.position_size || trade.invested;

    // Actual price movement
    const priceChange = (trade.exit_price - trade.entry_price) / trade.entry_price;
    const sideMultiplier = trade.side === "no" ? -1 : 1;
    const pctMove = priceChange * sideMultiplier;

    // Apply candidate TP/SL
    let simulatedPct;
    if (pctMove >= tp) {
      simulatedPct = tp; // would have hit TP
    } else if (pctMove <= -sl) {
      simulatedPct = -sl; // would have hit SL
    } else {
      simulatedPct = pctMove; // natural exit
    }

    const simulatedPnl = posSize * simulatedPct;
    totalPnl += simulatedPnl;
    equity += simulatedPnl;

    if (simulatedPnl > 0) wins++;
    else losses++;

    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  const totalTrades = wins + losses;
  const winRate = totalTrades > 0 ? wins / totalTrades : 0;
  // Simplified Sharpe-like metric: return / max(drawdown, 1)
  const sharpe = totalPnl / Math.max(maxDrawdown, 1);

  return {
    total_pnl: Math.round(totalPnl * 100) / 100,
    wins,
    losses,
    win_rate: Math.round(winRate * 1000) / 10,
    max_drawdown: Math.round(maxDrawdown * 100) / 100,
    sharpe: Math.round(sharpe * 1000) / 1000,
    trades_evaluated: totalTrades,
  };
}

// ─── Main Handler ────────────────────────────────────────────────

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-cache");

  if (!isRedisConfigured()) {
    return res.status(500).json({ error: "Redis not configured" });
  }

  try {
    // GET — return current state
    if (req.method === "GET" && req.query?.reset !== "1") {
      const params = (await redisGet(PARAMS_KEY)) || DEFAULT_PARAMS;
      const baseline = (await redisGet(BASELINE_KEY)) || null;
      const log = (await redisGet(LOG_KEY)) || [];

      // Compute summary stats
      const accepted = log.filter(e => e.accepted);
      const total = log.length;
      const improvements = accepted.length;
      const lastExperiment = log[log.length - 1] || null;

      // Compute cumulative improvement
      let cumulativePnlDelta = 0;
      for (const entry of accepted) {
        cumulativePnlDelta += (entry.candidate_result?.total_pnl || 0) - (entry.baseline_result?.total_pnl || 0);
      }

      return res.status(200).json({
        status: "ok",
        current_params: params,
        baseline,
        experiments: {
          total,
          accepted: improvements,
          rejected: total - improvements,
          acceptance_rate: total > 0 ? Math.round((improvements / total) * 1000) / 10 : 0,
          cumulative_pnl_delta: Math.round(cumulativePnlDelta * 100) / 100,
        },
        last_experiment: lastExperiment,
        log: log.slice(-50), // last 50
        research_program: RESEARCH_PROGRAM,
      });
    }

    // Reset
    if (req.query?.reset === "1") {
      await redisSet(PARAMS_KEY, DEFAULT_PARAMS);
      await redisSet(BASELINE_KEY, null);
      await redisSet(LOG_KEY, []);
      return res.status(200).json({ status: "reset", params: DEFAULT_PARAMS });
    }

    // POST — run one experiment cycle
    const portfolio = await redisGet("oracle:strategy100:portfolio");
    if (!portfolio || !portfolio.trades) {
      return res.status(200).json({
        status: "skipped",
        reason: "No trade history to backtest against",
      });
    }

    const closedTrades = portfolio.trades.filter(t => t.status === "closed");
    if (closedTrades.length < 3) {
      return res.status(200).json({
        status: "skipped",
        reason: `Need at least 3 closed trades for backtesting (have ${closedTrades.length})`,
      });
    }

    // Load current best params
    const currentParams = (await redisGet(PARAMS_KEY)) || deepClone(DEFAULT_PARAMS);

    // Backtest baseline
    const baselineResult = backtestParams(closedTrades, currentParams);

    // Mutate and backtest candidate
    const { candidate, mutation } = mutateParams(currentParams);
    const candidateResult = backtestParams(closedTrades, candidate);

    // Accept/reject: candidate must improve Sharpe OR P&L without worsening drawdown
    const pnlImproved = candidateResult.total_pnl > baselineResult.total_pnl;
    const sharpeImproved = candidateResult.sharpe > baselineResult.sharpe;
    const drawdownWorse = candidateResult.max_drawdown > baselineResult.max_drawdown * 1.2;
    const accepted = (pnlImproved || sharpeImproved) && !drawdownWorse;

    // Log experiment
    const experiment = {
      id: Date.now(),
      timestamp: new Date().toISOString(),
      mutation,
      baseline_result: baselineResult,
      candidate_result: candidateResult,
      accepted,
      reason: accepted
        ? `+$${(candidateResult.total_pnl - baselineResult.total_pnl).toFixed(2)} PnL, ${candidateResult.sharpe.toFixed(3)} Sharpe`
        : drawdownWorse
          ? `Drawdown increased: $${candidateResult.max_drawdown.toFixed(2)} vs $${baselineResult.max_drawdown.toFixed(2)}`
          : `No improvement: $${candidateResult.total_pnl.toFixed(2)} vs $${baselineResult.total_pnl.toFixed(2)}`,
    };

    const log = (await redisGet(LOG_KEY)) || [];
    log.push(experiment);
    await redisSet(LOG_KEY, log.slice(-200));

    // If accepted, update params
    if (accepted) {
      await redisSet(PARAMS_KEY, candidate);
      await redisSet(BASELINE_KEY, candidateResult);
    }

    return res.status(200).json({
      status: "ok",
      experiment,
      current_params: accepted ? candidate : currentParams,
      trades_evaluated: closedTrades.length,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
