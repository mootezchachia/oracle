/**
 * ORACLE $100 Strategy — Dashboard Read Endpoint
 *
 * Reads portfolio from Upstash Redis (written by strategy100-run cron).
 * Falls back to local file if Redis not configured.
 * Enriches open positions with live Polymarket prices.
 */

import { redisGet, isRedisConfigured } from './lib/redis.js';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

const PORTFOLIO_KEY = "oracle:strategy100:portfolio";
const LOG_KEY = "oracle:strategy100:log";
const MAIN_PORTFOLIO_KEY = "oracle:portfolio:main";
const PRICE_HISTORY_KEY = "oracle:price_history";

const EMPTY_PORTFOLIO = {
  account: { starting_balance: 100, total_cash: 100 },
  allocations: {
    bonds: { budget: 60, cash: 60, invested: 0 },
    expertise: { budget: 30, cash: 30, invested: 0 },
    flash_crash: { budget: 10, cash: 10, invested: 0 },
  },
  trades: [],
  stats: { total_trades: 0, wins: 0, losses: 0, total_pnl: 0 },
  initialized: false,
};

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");

  if (req.method === "OPTIONS") return res.status(204).end();

  // History view: ?view=history
  if (req.query?.view === "history") {
    return handleHistory(req, res);
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
              crashes_found: entry.scanned?.crashes || 0,
              executed: entry.executed?.length || 0,
              closed: entry.closed?.length || 0,
              trades: entry.executed || [],
              exits: entry.closed || [],
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
