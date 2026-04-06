import { useState } from "react";

const AGENT_COLORS = {
  base_rate: { bg: "bg-blue-500/10", text: "text-blue-400", bar: "bg-blue-400", label: "BASE RATE" },
  causal: { bg: "bg-emerald-500/10", text: "text-emerald-400", bar: "bg-emerald-400", label: "CAUSAL" },
  adversarial: { bg: "bg-red-500/10", text: "text-red-400", bar: "bg-red-400", label: "ADVERSARIAL" },
  crowd: { bg: "bg-amber-500/10", text: "text-amber-400", bar: "bg-amber-400", label: "CROWD" },
};

function SignalBadge({ signal }) {
  if (!signal || signal === "HOLD") return <span className="text-text-2 text-[9px]">HOLD</span>;
  const isBuy = signal.startsWith("BUY");
  return (
    <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold ${isBuy ? "bg-green/10 text-green" : "bg-red/10 text-red"}`}>
      {signal}
    </span>
  );
}

function ForecastRow({ item, rank }) {
  const [expanded, setExpanded] = useState(false);

  // Data lives at: item.market.*, item.agents[], item.aggregation.*, item.edge_pct, item.signal, etc.
  const m = item.market || {};
  const agg = item.aggregation || {};
  const agents = item.agents || [];
  const cls = item.classification || {};

  const yesPrice = m.yes_price ?? 0;
  const finalProb = agg.final_probability ?? 0;
  const edgePct = item.edge_pct ?? 0;
  const confidence = agg.ensemble_confidence ?? 0;
  const velocity = item.repricing_velocity ?? 0;

  const edgeColor = edgePct > 2 ? "text-green" : edgePct < -2 ? "text-red" : "text-text-2";
  const confColor = confidence >= 60 ? "text-green" : confidence >= 40 ? "text-amber-400" : "text-text-2";

  return (
    <div className="border-b border-border hover:bg-bg-2/50 transition-colors">
      <div
        className="grid grid-cols-[28px_1fr_55px_65px_65px_50px_55px] items-center px-3 py-2.5 cursor-pointer gap-1"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-[9px] text-text-2 font-mono">#{rank}</span>
        <div className="min-w-0">
          <div className="text-[11px] font-mono text-text-0 truncate">{m.question || "?"}</div>
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            {cls.label && <span className="text-[8px] text-text-2 uppercase bg-bg-3 px-1 py-0.5 rounded">{cls.label}</span>}
            <SignalBadge signal={item.signal} />
            {m.days_to_expiry != null && m.days_to_expiry < 999 && (
              <span className="text-[8px] text-text-2">{m.days_to_expiry}d</span>
            )}
          </div>
        </div>
        <div className="text-[10px] font-mono text-text-1 text-center tabular-nums">
          {(yesPrice * 100).toFixed(0)}c
        </div>
        <div className="text-[10px] font-mono text-center tabular-nums text-gold font-semibold">
          {(finalProb * 100).toFixed(0)}c
        </div>
        <div className={`text-[10px] font-mono font-semibold text-center tabular-nums ${edgeColor}`}>
          {edgePct > 0 ? "+" : ""}{edgePct.toFixed(1)}%
        </div>
        <div className={`text-[10px] font-mono text-center tabular-nums ${confColor}`}>
          {confidence}%
        </div>
        <div className="text-[10px] font-mono text-text-2 text-center tabular-nums">
          {velocity.toFixed(1)}
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-3 space-y-3 animate-fade-in">
          {/* Agent breakdown cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {agents.map((a) => {
              const colors = AGENT_COLORS[a.agent] || { bg: "bg-gray-500/10", text: "text-gray-400", label: a.agent };
              return (
                <div key={a.agent} className={`rounded-lg p-2.5 ${colors.bg} border border-transparent hover:border-white/5 transition-colors`}>
                  <div className={`text-[8px] font-bold tracking-wider ${colors.text}`}>{colors.label}</div>
                  <div className={`text-[16px] font-mono font-bold mt-1 ${colors.text}`}>
                    {(a.probability * 100).toFixed(1)}c
                  </div>
                  <div className="text-[8px] text-text-2 mt-1">
                    confidence: {(a.confidence * 100).toFixed(0)}%
                  </div>
                  {a.reasoning && (
                    <div className="text-[8px] text-text-2/70 mt-1 leading-relaxed line-clamp-2">
                      {a.reasoning}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Visual probability bar */}
          <div>
            <div className="flex items-center justify-between text-[8px] text-text-2 mb-1">
              <span>0c (NO)</span>
              <span>
                Market: <span className="text-text-1">{(yesPrice * 100).toFixed(0)}c</span>
                {" | "}
                Forecast: <span className="text-gold">{(finalProb * 100).toFixed(0)}c</span>
                {" | "}
                Edge: <span className={edgeColor}>{edgePct > 0 ? "+" : ""}{edgePct.toFixed(1)}%</span>
              </span>
              <span>100c (YES)</span>
            </div>
            <div className="relative h-3 bg-bg-3 rounded-full overflow-visible">
              {/* Agent markers */}
              {agents.map((a) => {
                const colors = AGENT_COLORS[a.agent] || {};
                const pos = a.probability * 100;
                return (
                  <div
                    key={a.agent}
                    className={`absolute top-0 h-3 w-1 rounded-full ${colors.bar || "bg-gray-400"} opacity-60`}
                    style={{ left: `${Math.min(99, Math.max(1, pos))}%`, transform: "translateX(-50%)" }}
                    title={`${colors.label}: ${pos.toFixed(0)}c`}
                  />
                );
              })}
              {/* Market price */}
              <div
                className="absolute top-[-2px] h-[calc(100%+4px)] w-0.5 bg-text-2"
                style={{ left: `${Math.min(99, Math.max(1, yesPrice * 100))}%`, transform: "translateX(-50%)" }}
                title={`Market: ${(yesPrice * 100).toFixed(0)}c`}
              />
              {/* Final forecast (gold diamond) */}
              <div
                className="absolute top-[-2px] h-[calc(100%+4px)] w-1.5 bg-gold rounded-full z-10"
                style={{ left: `${Math.min(99, Math.max(1, finalProb * 100))}%`, transform: "translateX(-50%)" }}
                title={`Forecast: ${(finalProb * 100).toFixed(0)}c`}
              />
            </div>
          </div>

          {/* Aggregation details */}
          <div className="grid grid-cols-3 gap-2 text-[9px] font-mono">
            <div className="bg-bg-2/50 rounded p-2">
              <div className="text-text-2">GEO MEAN</div>
              <div className="text-text-0 font-semibold">{((agg.raw_geo_mean || 0) * 100).toFixed(1)}c</div>
            </div>
            <div className="bg-bg-2/50 rounded p-2">
              <div className="text-text-2">EXTREMIZED</div>
              <div className="text-gold font-semibold">{((agg.extremized || 0) * 100).toFixed(1)}c</div>
            </div>
            <div className="bg-bg-2/50 rounded p-2">
              <div className="text-text-2">AGENT SPREAD</div>
              <div className="text-text-0 font-semibold">{((agg.agent_spread || 0) * 100).toFixed(1)}c</div>
            </div>
          </div>

          {/* Market metadata */}
          <div className="flex items-center gap-3 text-[8px] text-text-2">
            <span>Vol: ${(m.volume / 1000).toFixed(0)}K</span>
            <span>Category: {m.category}</span>
            {m.days_to_expiry < 999 && <span>Expires: {m.days_to_expiry}d</span>}
            <span>Repricing: {velocity.toFixed(2)}/yr</span>
          </div>
        </div>
      )}
    </div>
  );
}

function AIBriefCard({ aiBrief }) {
  const [expanded, setExpanded] = useState(false);
  const analysis = aiBrief?.analysis;
  const brief = aiBrief?.brief;

  if (!analysis && !brief) return null;

  const opportunities = analysis?.opportunities || [];
  const healthAlerts = (analysis?.health_checks || []).filter(h => h.status === "alert" || h.status === "exit");

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      <div
        className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-purple-500/5 to-bg-1 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-purple-400">&#9733;</span> AI INTELLIGENCE BRIEF
          <span className="bg-purple-500/15 text-purple-400 text-[9px] px-2 py-0.5 rounded-full">
            {analysis?.ai_powered ? "CLAUDE" : "ALGO"}
          </span>
          {opportunities.length > 0 && (
            <span className="bg-green/15 text-green text-[9px] px-2 py-0.5 rounded-full">
              {opportunities.length} SIGNAL{opportunities.length !== 1 ? "S" : ""}
            </span>
          )}
          {healthAlerts.length > 0 && (
            <span className="bg-red/15 text-red text-[9px] px-2 py-0.5 rounded-full">
              {healthAlerts.length} ALERT{healthAlerts.length !== 1 ? "S" : ""}
            </span>
          )}
        </div>
        <div className="text-[9px] text-text-2 font-mono">
          {analysis?.timestamp ? new Date(analysis.timestamp).toLocaleString() : ""}
          <span className="ml-2">{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {expanded && (
        <div className="p-4 space-y-3 animate-fade-in">
          {/* AI Opportunities */}
          {opportunities.length > 0 && (
            <div>
              <div className="text-[9px] font-bold tracking-wider text-purple-400 mb-2">AI OPPORTUNITIES</div>
              {opportunities.map((o, i) => (
                <div key={o.slug || i} className="bg-bg-2/50 rounded p-2.5 mb-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono text-text-0 truncate flex-1">{o.question}</span>
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${o.signal?.includes("YES") ? "bg-green/10 text-green" : "bg-red/10 text-red"}`}>
                      {o.signal}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-[8px] text-text-2">
                    <span>Market: {Math.round(o.market_price * 100)}c</span>
                    <span className="text-purple-400">AI: {Math.round(o.ai_probability * 100)}c</span>
                    <span className={o.edge_pct > 0 ? "text-green" : "text-red"}>
                      Edge: {o.edge_pct > 0 ? "+" : ""}{o.edge_pct}%
                    </span>
                  </div>
                  {/* Agent reasoning */}
                  <div className="grid grid-cols-2 gap-1 mt-1.5">
                    {(o.agents || []).map(a => (
                      <div key={a.name} className="text-[8px] text-text-2 truncate">
                        <span className="text-text-1 font-semibold">{a.name}:</span> {a.reasoning || ""}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Health alerts */}
          {healthAlerts.length > 0 && (
            <div>
              <div className="text-[9px] font-bold tracking-wider text-red mb-2">POSITION ALERTS</div>
              {healthAlerts.map((a, i) => (
                <div key={i} className="bg-red/5 border border-red/20 rounded p-2 mb-1 text-[10px]">
                  <span className="text-red font-bold">{a.status.toUpperCase()}</span>
                  <span className="text-text-1 ml-2">{a.slug}</span>
                  <span className="text-text-2 ml-2">— {a.reason}</span>
                </div>
              ))}
            </div>
          )}

          {/* Raw brief */}
          {brief && (
            <details className="text-[9px]">
              <summary className="text-text-2 cursor-pointer hover:text-text-1">Raw Intelligence Brief</summary>
              <pre className="mt-2 p-3 bg-bg-2/50 rounded text-text-2 font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto">
                {typeof brief === "string" ? brief : JSON.stringify(brief, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function ForecastPanel({ data, aiBrief }) {
  const [filter, setFilter] = useState("all");

  if (!data || !data.top_forecasts || data.top_forecasts.length === 0) {
    return (
      <div className="bg-bg-1 border border-border rounded-lg p-8 text-center">
        <div className="text-text-2 text-sm">No forecast data yet.</div>
        <div className="text-[10px] text-text-2 mt-2">
          The superforecasting engine runs with each scan cycle,
          or trigger manually via <code className="bg-bg-3 px-1 rounded text-text-1">/api/strategy100-run?action=forecast</code>
        </div>
      </div>
    );
  }

  const forecasts = data.top_forecasts;
  const withEdge = forecasts.filter(f => f.signal && f.signal !== "HOLD");
  const holds = forecasts.filter(f => !f.signal || f.signal === "HOLD");

  const filtered = filter === "all" ? forecasts
    : filter === "edge" ? withEdge
    : holds;

  return (
    <div className="space-y-3">
      {/* AI Intelligence Brief */}
      <AIBriefCard aiBrief={aiBrief} />

      {/* Summary header */}
      <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
            <span className="text-gold">&#9670;</span> SUPERFORECASTING ENGINE
            <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">
              4 AGENTS
            </span>
          </div>
          <div className="text-[9px] text-text-2 font-mono">
            {data.timestamp ? new Date(data.timestamp).toLocaleString() : ""}
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-border text-center text-[9px] font-mono">
          <div className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">ANALYZED</div>
            <div className="text-text-0 font-semibold">{data.markets_analyzed || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">FORECASTS</div>
            <div className="text-text-0 font-semibold">{data.forecasts_generated || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">WITH EDGE</div>
            <div className="text-green font-semibold">{data.with_edge || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">AGGREGATION</div>
            <div className="text-gold font-semibold">GEO MEAN</div>
          </div>
          <div className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">CALIBRATION</div>
            <div className="text-gold font-semibold">LOGIT x1.5</div>
          </div>
        </div>
      </div>

      {/* Agent legend */}
      <div className="flex flex-wrap items-center gap-2 px-1">
        {Object.entries(AGENT_COLORS).map(([key, { bg, text, label }]) => (
          <div key={key} className={`flex items-center gap-1.5 px-2 py-1 rounded ${bg}`}>
            <div className={`w-2 h-2 rounded-full ${text.replace("text-", "bg-")}`} />
            <span className={`text-[8px] font-bold tracking-wider ${text}`}>{label}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-gold/10">
          <div className="w-2 h-2 rounded-full bg-gold" />
          <span className="text-[8px] font-bold tracking-wider text-gold">ENSEMBLE</span>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2 px-1">
        {[
          { key: "all", label: `ALL (${forecasts.length})` },
          { key: "edge", label: `EDGE (${withEdge.length})` },
          { key: "hold", label: `HOLD (${holds.length})` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`px-3 py-1 rounded text-[9px] font-mono tracking-wider transition-all ${
              filter === key
                ? "bg-gold/10 text-gold border border-gold/30"
                : "bg-bg-2 text-text-2 border border-border hover:text-text-1"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Forecasts table */}
      <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
        <div className="grid grid-cols-[28px_1fr_55px_65px_65px_50px_55px] items-center px-3 py-2 border-b border-border bg-bg-2/50 text-[8px] font-mono text-text-2 tracking-wider uppercase gap-1">
          <span>#</span>
          <span>MARKET</span>
          <span className="text-center">PRICE</span>
          <span className="text-center">FORECAST</span>
          <span className="text-center">EDGE</span>
          <span className="text-center">CONF</span>
          <span className="text-center">VEL</span>
        </div>

        <div className="max-h-[600px] overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((f, i) => (
              <ForecastRow key={f.market?.slug || i} item={f} rank={i + 1} />
            ))
          ) : (
            <div className="p-6 text-center text-[10px] text-text-2">
              No forecasts in this category.
            </div>
          )}
        </div>
      </div>

      {/* Methodology card */}
      {data.methodology && (
        <div className="bg-bg-1 border border-border rounded-lg p-4">
          <div className="text-[9px] font-semibold tracking-widest uppercase text-text-2 mb-3">METHODOLOGY</div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[10px] font-mono">
            {Object.entries(data.methodology).map(([key, val]) => (
              <div key={key} className="flex justify-between items-center py-0.5 border-b border-border/30">
                <span className="text-text-2">{key.replace(/_/g, " ").toUpperCase()}</span>
                <span className="text-text-1 font-semibold">{val}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
