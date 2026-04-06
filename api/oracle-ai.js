/**
 * ORACLE AI — Claude-Powered Superforecasting Engine
 *
 * Uses Claude API to run deep market analysis with 4 reasoning agents.
 * Called by QStash schedule or manual trigger.
 *
 * Routes:
 *   GET  /api/oracle-ai              — Run full AI analysis cycle
 *   GET  /api/oracle-ai?view=brief   — Read latest cached intelligence brief
 */

import { redisGet, redisSet, isRedisConfigured } from './lib/redis.js';

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
const ANTHROPIC_BASE_URL = process.env.ANTHROPIC_BASE_URL || "https://api.anthropic.com";
const BRIEF_KEY = "oracle:ai:brief";
const ANALYSIS_KEY = "oracle:ai:analysis";
const BASE_URL = "https://oracle-psi-orpin.vercel.app";

// ─── Data Fetching ──────────────────────────────────────────

async function fetchJSON(path) {
  try {
    const r = await fetch(`${BASE_URL}${path}`, { signal: AbortSignal.timeout(12000) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

async function gatherIntelligence() {
  const [markets, signals, news, reddit, strategy, portfolio, forecasts, fred] = await Promise.all([
    fetchJSON("/api/markets"),
    fetchJSON("/api/signals"),
    fetchJSON("/api/news"),
    fetchJSON("/api/reddit"),
    fetchJSON("/api/strategy100"),
    fetchJSON("/api/portfolio"),
    fetchJSON("/api/strategy100?view=forecast"),
    fetchJSON("/api/fred"),
  ]);
  return { markets, signals, news, reddit, strategy, portfolio, forecasts, fred };
}

// ─── Claude API Call ────────────────────────────────────────

async function callClaude(systemPrompt, userPrompt, maxTokens = 4000) {
  if (!ANTHROPIC_API_KEY) {
    // Fallback: generate a structured analysis without LLM
    return fallbackAnalysis(userPrompt);
  }

  const res = await fetch(`${ANTHROPIC_BASE_URL}/v1/messages`, {
    method: "POST",
    headers: {
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: maxTokens,
      system: systemPrompt,
      messages: [{ role: "user", content: userPrompt }],
    }),
    signal: AbortSignal.timeout(25000),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Claude API error ${res.status}: ${text}`);
  }

  const data = await res.json();
  return data.content?.[0]?.text || "";
}

function fallbackAnalysis(prompt) {
  return "LLM analysis unavailable — using algorithmic forecasts only. Set ANTHROPIC_API_KEY for AI-powered analysis.";
}

// ─── Agent Prompts ──────────────────────────────────────────

function buildBaseRatePrompt(market, news, forecasts) {
  return `You are a BASE RATE forecasting agent. Estimate the probability of this market resolving YES.

MARKET: ${market.question}
Current YES price: ${market.yes_price} (market thinks ${Math.round(market.yes_price * 100)}% chance)
Volume: $${market.volume?.toLocaleString() || 0}
Days to expiry: ${market.days_to_expiry || "unknown"}
Category: ${market.category || "unknown"}

ALGORITHMIC FORECAST: ${forecasts?.final_probability ? `${Math.round(forecasts.final_probability * 100)}%` : "unavailable"}

Think about:
1. Historical base rate: How often do events like this actually happen?
2. Time horizon: Is this realistic in the remaining time?
3. Reference class: What similar events from the past can inform this?

Respond in EXACTLY this JSON format:
{"probability": 0.XX, "confidence": "low|medium|high", "reasoning": "1-2 sentences"}`;
}

function buildCausalPrompt(market, news, signals) {
  const relevantNews = (news || [])
    .filter(n => {
      const q = market.question?.toLowerCase() || "";
      const title = (n.title || "").toLowerCase();
      return q.split(" ").some(w => w.length > 4 && title.includes(w));
    })
    .slice(0, 5)
    .map(n => `- ${n.title}`)
    .join("\n");

  return `You are a CAUSAL ANALYSIS agent. Identify the key drivers and estimate probability.

MARKET: ${market.question}
Current YES price: ${market.yes_price}
Days to expiry: ${market.days_to_expiry || "unknown"}

RELEVANT NEWS:
${relevantNews || "No directly relevant news found."}

SIGNALS: ${signals ? JSON.stringify({
    narrative_dominance: signals.narrative_dominance,
    rsi: signals.rsi,
    macd_signal: signals.macd_signal,
    sentiment: signals.sentiment,
  }) : "unavailable"}

Think about:
1. What concrete events or decisions would make this YES vs NO?
2. Does the news suggest movement toward YES or NO?
3. Are there upcoming catalysts (meetings, deadlines, announcements)?

Respond in EXACTLY this JSON format:
{"probability": 0.XX, "confidence": "low|medium|high", "reasoning": "1-2 sentences", "catalysts": ["upcoming event 1", "event 2"]}`;
}

function buildAdversarialPrompt(market, baseRateResult, causalResult) {
  return `You are an ADVERSARIAL agent. Challenge the other agents' forecasts and find flaws.

MARKET: ${market.question}
Current market price: ${Math.round(market.yes_price * 100)}% YES
Base Rate agent says: ${baseRateResult}
Causal agent says: ${causalResult}

Your job:
1. Why might the MARKET PRICE be more accurate than our agents?
2. What information could the market be pricing in that we're missing?
3. What's the strongest argument AGAINST our agents' consensus?
4. Are our agents falling for any cognitive biases?

If you think the other agents are wrong, give YOUR probability. If you think they're roughly right, confirm with slight adjustment.

Respond in EXACTLY this JSON format:
{"probability": 0.XX, "confidence": "low|medium|high", "reasoning": "1-2 sentences", "challenge": "key counterargument"}`;
}

function buildCrowdPrompt(market, reddit) {
  const relevantReddit = (reddit || [])
    .filter(p => {
      const q = market.question?.toLowerCase() || "";
      const title = (p.title || "").toLowerCase();
      return q.split(" ").some(w => w.length > 4 && title.includes(w));
    })
    .slice(0, 5)
    .map(p => `- [${p.score || 0} pts] ${p.title}`)
    .join("\n");

  return `You are a CROWD WISDOM agent. Analyze social sentiment and trading patterns.

MARKET: ${market.question}
Current YES price: ${market.yes_price}
Volume: $${market.volume?.toLocaleString() || 0}

REDDIT SENTIMENT:
${relevantReddit || "No relevant Reddit posts found."}

Think about:
1. Is the crowd overly bullish or bearish? (contrarian signal)
2. Does high/low volume suggest informed or retail activity?
3. Is Reddit sentiment aligned with or against the market price?

Respond in EXACTLY this JSON format:
{"probability": 0.XX, "confidence": "low|medium|high", "reasoning": "1-2 sentences", "crowd_bias": "bullish|bearish|neutral"}`;
}

// ─── Analysis Pipeline ──────────────────────────────────────

function parseAgentResponse(text) {
  try {
    // Try to extract JSON from the response
    const match = text.match(/\{[\s\S]*\}/);
    if (match) return JSON.parse(match[0]);
  } catch {}
  return { probability: 0.5, confidence: "low", reasoning: text.slice(0, 200) };
}

async function analyzeMarket(market, intel) {
  const { news, reddit, signals, forecasts } = intel;

  // Find algorithmic forecast for this market
  const algoForecast = forecasts?.top_forecasts?.find(
    f => f.market?.slug === market.slug
  );

  const systemPrompt = "You are a superforecaster. Be precise, calibrated, and concise. Output ONLY valid JSON.";

  // Run base rate and causal in parallel (they're independent)
  const [baseRateRaw, causalRaw] = await Promise.all([
    callClaude(systemPrompt, buildBaseRatePrompt(market, news, algoForecast?.aggregation)),
    callClaude(systemPrompt, buildCausalPrompt(market, news, signals)),
  ]);

  const baseRate = parseAgentResponse(baseRateRaw);
  const causal = parseAgentResponse(causalRaw);

  // Adversarial sees the other two agents' outputs
  const adversarialRaw = await callClaude(
    systemPrompt,
    buildAdversarialPrompt(market, JSON.stringify(baseRate), JSON.stringify(causal))
  );
  const adversarial = parseAgentResponse(adversarialRaw);

  // Crowd runs in parallel with adversarial
  const crowdRaw = await callClaude(systemPrompt, buildCrowdPrompt(market, reddit));
  const crowd = parseAgentResponse(crowdRaw);

  // Aggregate: confidence-weighted geometric mean + logit extremizing
  const agents = [
    { name: "base_rate", ...baseRate },
    { name: "causal", ...causal },
    { name: "adversarial", ...adversarial },
    { name: "crowd", ...crowd },
  ];

  const confWeights = { high: 1.0, medium: 0.7, low: 0.4 };
  let totalWeight = 0;
  let logGeoMean = 0;
  for (const a of agents) {
    const w = confWeights[a.confidence] || 0.5;
    totalWeight += w;
    logGeoMean += w * Math.log(Math.max(0.01, Math.min(0.99, a.probability)));
  }
  const geoMean = Math.exp(logGeoMean / totalWeight);

  // Logit extremizing
  const clamp = p => Math.max(0.01, Math.min(0.99, p));
  const logit = p => Math.log(clamp(p) / (1 - clamp(p)));
  const invLogit = l => 1 / (1 + Math.exp(-l));
  const finalProb = invLogit(logit(geoMean) * 1.5);

  const edge = finalProb - market.yes_price;
  const edgePct = Math.round(edge * 10000) / 100;

  let signal = "HOLD";
  if (edge > 0.04 && agents.some(a => a.confidence === "high")) signal = "BUY YES";
  else if (edge < -0.04 && agents.some(a => a.confidence === "high")) signal = "BUY NO";

  return {
    slug: market.slug,
    question: market.question,
    market_price: market.yes_price,
    ai_probability: Math.round(finalProb * 1000) / 1000,
    edge_pct: edgePct,
    signal,
    agents,
    algo_forecast: algoForecast?.aggregation?.final_probability || null,
    category: market.category,
    days_to_expiry: market.days_to_expiry,
    volume: market.volume,
  };
}

// ─── Position Health Check ──────────────────────────────────

async function checkPositionHealth(positions, intel) {
  if (!positions || positions.length === 0) return [];
  if (!ANTHROPIC_API_KEY) {
    return positions.map(p => ({
      slug: p.slug, question: p.question, status: "unchecked",
      reason: "No API key for AI health check"
    }));
  }

  const alerts = [];
  const newsText = (intel.news || []).slice(0, 20).map(n => `- ${n.title}`).join("\n");

  // Batch check: send all positions to Claude in one call
  const positionList = positions.slice(0, 15).map(p =>
    `- "${p.question}" | Side: ${p.side} | Entry: ${p.entry_price} | Current PnL: ${p.pnl != null ? `$${p.pnl}` : "unknown"}`
  ).join("\n");

  const prompt = `Check if these open prediction market positions still have valid theses given the latest news.

OPEN POSITIONS:
${positionList}

LATEST NEWS:
${newsText}

For each position, respond with a JSON array:
[{"slug": "...", "status": "hold|alert|exit", "reason": "brief explanation"}]

Only flag "alert" or "exit" if news DIRECTLY contradicts the trade thesis. Most positions should be "hold".`;

  try {
    const result = await callClaude(
      "You are a risk manager checking open positions against latest news. Output ONLY a JSON array.",
      prompt, 2000
    );
    const parsed = parseAgentResponse(`{"alerts": ${result}}`);
    return parsed.alerts || [];
  } catch {
    return [{ slug: "all", status: "error", reason: "Health check failed" }];
  }
}

// ─── Intelligence Brief ─────────────────────────────────────

function generateBrief(analyses, healthChecks, intel) {
  const now = new Date().toISOString();
  const strat = intel.strategy?.account || {};
  const main = intel.portfolio?.account || {};

  const topOpps = analyses
    .filter(a => a.signal !== "HOLD")
    .sort((a, b) => Math.abs(b.edge_pct) - Math.abs(a.edge_pct))
    .slice(0, 5);

  const positionAlerts = healthChecks.filter(h => h.status === "alert" || h.status === "exit");

  let brief = `=== ORACLE AI INTELLIGENCE BRIEF ===\n`;
  brief += `Generated: ${now}\n`;
  brief += `Markets analyzed: ${analyses.length}\n`;
  brief += `AI-powered: ${ANTHROPIC_API_KEY ? "YES (Claude Haiku)" : "NO (algorithmic only)"}\n\n`;

  brief += `--- PORTFOLIO STATUS ---\n`;
  brief += `$10K Portfolio: $${main.total_value?.toFixed(2) || "?"} (${main.pnl >= 0 ? "+" : ""}$${main.pnl?.toFixed(2) || "?"}) — ${intel.portfolio?.trade_count || "?"} positions\n`;
  brief += `$1K Strategy:   $${strat.total_value?.toFixed(2) || "?"} (${strat.total_return >= 0 ? "+" : ""}$${strat.total_return?.toFixed(2) || "?"}) — ${intel.strategy?.positions?.length || 0} open\n\n`;

  if (topOpps.length > 0) {
    brief += `--- TOP OPPORTUNITIES ---\n`;
    for (let i = 0; i < topOpps.length; i++) {
      const o = topOpps[i];
      brief += `${i + 1}. [${o.signal}] ${o.question}\n`;
      brief += `   Market: ${Math.round(o.market_price * 100)}c | AI: ${Math.round(o.ai_probability * 100)}c | Edge: ${o.edge_pct > 0 ? "+" : ""}${o.edge_pct}%\n`;
      const reasoning = o.agents.map(a => `${a.name}: ${a.reasoning || ""}`).join(" | ");
      brief += `   ${reasoning.slice(0, 200)}\n\n`;
    }
  } else {
    brief += `--- NO HIGH-CONFIDENCE OPPORTUNITIES ---\n`;
    brief += `All markets within normal pricing. Agents found no significant edge.\n\n`;
  }

  if (positionAlerts.length > 0) {
    brief += `--- POSITION ALERTS ---\n`;
    for (const a of positionAlerts) {
      brief += `!! ${a.status.toUpperCase()}: ${a.slug} — ${a.reason}\n`;
    }
    brief += `\n`;
  }

  brief += `--- KEY METRICS ---\n`;
  brief += `Algo forecasts cached: ${intel.forecasts?.forecasts_generated || 0}\n`;
  brief += `Algo edge signals: ${intel.forecasts?.with_edge || 0}\n`;
  brief += `AI analyses run: ${analyses.length}\n`;
  brief += `AI edge signals: ${topOpps.length}\n`;

  return brief;
}

// ─── Main Handler ───────────────────────────────────────────

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-cache");

  if (req.method === "OPTIONS") return res.status(204).end();

  if (!isRedisConfigured()) {
    return res.status(500).json({ error: "Redis not configured" });
  }

  // Read cached brief
  if (req.query?.view === "brief") {
    const brief = await redisGet(BRIEF_KEY);
    const analysis = await redisGet(ANALYSIS_KEY);
    return res.status(200).json({ brief, analysis, cached: true });
  }

  try {
    // Step 1: Gather all intelligence
    const intel = await gatherIntelligence();

    // Step 2: Select markets to analyze (top from algo forecasts + open positions)
    const marketsToAnalyze = [];

    // Add top algorithmic forecast markets
    if (intel.forecasts?.top_forecasts) {
      for (const f of intel.forecasts.top_forecasts.slice(0, 8)) {
        const m = f.market;
        if (m) marketsToAnalyze.push(m);
      }
    }

    // Add markets from open strategy positions (health check)
    const openPositions = intel.strategy?.positions?.filter(p => p.current_price != null) || [];

    // Step 3: Run AI analysis on selected markets
    // Process sequentially to respect rate limits (Haiku is fast)
    const analyses = [];
    for (const market of marketsToAnalyze.slice(0, 10)) {
      try {
        const result = await analyzeMarket(market, intel);
        analyses.push(result);
      } catch (err) {
        analyses.push({
          slug: market.slug, question: market.question,
          error: err.message, signal: "ERROR",
        });
      }
    }

    // Step 4: Position health check
    const allPositions = [
      ...(intel.strategy?.positions || []),
      ...(intel.portfolio?.positions || []),
    ].filter(p => p.status !== "closed");
    const healthChecks = await checkPositionHealth(allPositions, intel);

    // Step 5: Generate brief
    const brief = generateBrief(analyses, healthChecks, intel);

    // Step 6: Cache results
    const result = {
      timestamp: new Date().toISOString(),
      markets_analyzed: analyses.length,
      ai_powered: !!ANTHROPIC_API_KEY,
      analyses,
      health_checks: healthChecks,
      brief,
      opportunities: analyses.filter(a => a.signal !== "HOLD" && a.signal !== "ERROR"),
    };

    await redisSet(BRIEF_KEY, brief);
    await redisSet(ANALYSIS_KEY, result);

    return res.status(200).json(result);
  } catch (err) {
    return res.status(500).json({ error: err.message, stack: err.stack?.split("\n").slice(0, 3) });
  }
}
