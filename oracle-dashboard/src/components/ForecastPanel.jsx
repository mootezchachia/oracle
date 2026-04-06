import { useState } from "react";

const AGENT_COLORS = {
  base_rate: { bg: "bg-blue-500/10", text: "text-blue-400", label: "BASE RATE" },
  causal: { bg: "bg-emerald-500/10", text: "text-emerald-400", label: "CAUSAL" },
  adversarial: { bg: "bg-red-500/10", text: "text-red-400", label: "ADVERSARIAL" },
  crowd: { bg: "bg-amber-500/10", text: "text-amber-400", label: "CROWD" },
};

function AgentDot({ agent, prob }) {
  const colors = AGENT_COLORS[agent] || { bg: "bg-gray-500/10", text: "text-gray-400" };
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-bold ${colors.bg} ${colors.text}`}
      title={`${colors.label}: ${(prob * 100).toFixed(0)}c`}
    >
      {(prob * 100).toFixed(0)}c
    </span>
  );
}

function SignalBadge({ signal }) {
  if (!signal || signal === "HOLD") return <span className="text-text-2 text-[9px]">HOLD</span>;
  const isBuy = signal.startsWith("BUY");
  return (
    <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold ${isBuy ? "bg-green/10 text-green" : "bg-red/10 text-red"}`}>
      {signal}
    </span>
  );
}

function ForecastRow({ forecast, rank }) {
  const [expanded, setExpanded] = useState(false);
  const f = forecast.forecast || {};
  const edgeColor = f.edge_pct > 0 ? "text-green" : f.edge_pct < 0 ? "text-red" : "text-text-2";
  const confColor = f.confidence >= 60 ? "text-green" : f.confidence >= 40 ? "text-amber-400" : "text-text-2";

  return (
    <div className="border-b border-border hover:bg-bg-2/50 transition-colors">
      <div
        className="grid grid-cols-[24px_1fr_60px_70px_70px_55px_60px] items-center px-3 py-2.5 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-[9px] text-text-2 font-mono">#{rank}</span>
        <div className="min-w-0">
          <div className="text-[11px] font-mono text-text-0 truncate">{forecast.question}</div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-[8px] text-text-2 uppercase">{forecast.label}</span>
            <SignalBadge signal={f.signal} />
            {forecast.days_to_expiry < 999 && (
              <span className="text-[8px] text-text-2">{forecast.days_to_expiry}d</span>
            )}
          </div>
        </div>
        <div className="text-[10px] font-mono text-text-1 text-center tabular-nums">
          {(forecast.yes_price * 100).toFixed(0)}c
        </div>
        <div className="text-[10px] font-mono text-center tabular-nums text-gold">
          {f.final_probability ? `${(f.final_probability * 100).toFixed(0)}c` : "—"}
        </div>
        <div className={`text-[10px] font-mono font-semibold text-center tabular-nums ${edgeColor}`}>
          {f.edge_pct != null ? `${f.edge_pct > 0 ? "+" : ""}${f.edge_pct.toFixed(1)}%` : "—"}
        </div>
        <div className={`text-[10px] font-mono text-center tabular-nums ${confColor}`}>
          {f.confidence != null ? `${f.confidence}%` : "—"}
        </div>
        <div className="text-[10px] font-mono text-text-2 text-center tabular-nums">
          {f.repricing_velocity != null ? f.repricing_velocity.toFixed(1) : "—"}
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-3 space-y-2 animate-fade-in">
          {/* Agent breakdown */}
          <div className="grid grid-cols-4 gap-2">
            {(f.agents || []).map((a) => {
              const colors = AGENT_COLORS[a.agent] || { bg: "bg-gray-500/10", text: "text-gray-400", label: a.agent };
              return (
                <div key={a.agent} className={`rounded p-2 ${colors.bg}`}>
                  <div className={`text-[8px] font-bold tracking-wider ${colors.text}`}>{colors.label}</div>
                  <div className={`text-[14px] font-mono font-bold mt-0.5 ${colors.text}`}>
                    {(a.prob * 100).toFixed(1)}c
                  </div>
                  <div className="text-[8px] text-text-2 mt-0.5">
                    conf: {(a.conf * 100).toFixed(0)}%
                  </div>
                </div>
              );
            })}
          </div>

          {/* Thesis */}
          {forecast.thesis && (
            <div className="text-[10px] text-text-2 leading-relaxed bg-bg-2/50 rounded p-2">
              {forecast.thesis}
            </div>
          )}

          {/* Probability bar */}
          <div className="relative h-2 bg-bg-3 rounded-full overflow-hidden">
            {/* Market price marker */}
            <div
              className="absolute top-0 h-full w-0.5 bg-text-2 z-10"
              style={{ left: `${forecast.yes_price * 100}%` }}
              title={`Market: ${(forecast.yes_price * 100).toFixed(0)}c`}
            />
            {/* Agent forecasts */}
            {(f.agents || []).map((a) => {
              const colors = AGENT_COLORS[a.agent];
              return (
                <div
                  key={a.agent}
                  className={`absolute top-0 h-full w-1 rounded-full ${colors?.text?.replace("text-", "bg-")} opacity-70`}
                  style={{ left: `${a.prob * 100}%` }}
                  title={`${colors?.label}: ${(a.prob * 100).toFixed(0)}c`}
                />
              );
            })}
            {/* Final forecast */}
            {f.final_probability && (
              <div
                className="absolute top-0 h-full w-1.5 bg-gold rounded-full z-20"
                style={{ left: `${f.final_probability * 100}%` }}
                title={`Forecast: ${(f.final_probability * 100).toFixed(0)}c`}
              />
            )}
          </div>
          <div className="flex justify-between text-[8px] text-text-2">
            <span>0c</span>
            <span>Market: {(forecast.yes_price * 100).toFixed(0)}c | Forecast: {f.final_probability ? (f.final_probability * 100).toFixed(0) + "c" : "—"}</span>
            <span>100c</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ForecastPanel({ data }) {
  const [filter, setFilter] = useState("all");

  if (!data || !data.top_forecasts) {
    return (
      <div className="bg-bg-1 border border-border rounded-lg p-8 text-center">
        <div className="text-text-2 text-sm">Loading forecasts... Run a forecast scan first.</div>
        <div className="text-[10px] text-text-2 mt-2">
          Trigger via: <code className="bg-bg-3 px-1 rounded">/api/strategy100-run?action=forecast</code>
        </div>
      </div>
    );
  }

  const forecasts = data.top_forecasts || [];
  const withEdge = forecasts.filter(f => f.forecast?.signal !== "HOLD");
  const holds = forecasts.filter(f => f.forecast?.signal === "HOLD");

  const filtered = filter === "all" ? forecasts
    : filter === "edge" ? withEdge
    : holds;

  return (
    <div className="space-y-3">
      {/* Summary header */}
      <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
            <span className="text-gold">&#9670;</span> SUPERFORECASTING ENGINE
            <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">
              4 AGENTS
            </span>
          </div>
          <div className="text-[9px] text-text-2">
            {data.timestamp ? new Date(data.timestamp).toLocaleString() : ""}
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-border text-center text-[9px] font-mono">
          <div className="bg-bg-1 py-2.5">
            <div className="text-text-2">ANALYZED</div>
            <div className="text-text-0 font-semibold">{data.markets_analyzed || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5">
            <div className="text-text-2">FORECASTS</div>
            <div className="text-text-0 font-semibold">{data.forecasts_generated || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5">
            <div className="text-text-2">WITH EDGE</div>
            <div className="text-green font-semibold">{data.with_edge || 0}</div>
          </div>
          <div className="bg-bg-1 py-2.5">
            <div className="text-text-2">AGGREGATION</div>
            <div className="text-gold font-semibold">GEO MEAN</div>
          </div>
          <div className="bg-bg-1 py-2.5">
            <div className="text-text-2">CALIBRATION</div>
            <div className="text-gold font-semibold">LOGIT x1.5</div>
          </div>
        </div>
      </div>

      {/* Agent legend */}
      <div className="flex items-center gap-3 px-1">
        {Object.entries(AGENT_COLORS).map(([key, { bg, text, label }]) => (
          <div key={key} className={`flex items-center gap-1.5 px-2 py-1 rounded ${bg}`}>
            <div className={`w-2 h-2 rounded-full ${text.replace("text-", "bg-")}`} />
            <span className={`text-[8px] font-bold tracking-wider ${text}`}>{label}</span>
          </div>
        ))}
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
        {/* Table header */}
        <div className="grid grid-cols-[24px_1fr_60px_70px_70px_55px_60px] items-center px-3 py-2 border-b border-border bg-bg-2/50 text-[8px] font-mono text-text-2 tracking-wider uppercase">
          <span>#</span>
          <span>MARKET</span>
          <span className="text-center">PRICE</span>
          <span className="text-center">FORECAST</span>
          <span className="text-center">EDGE</span>
          <span className="text-center">CONF</span>
          <span className="text-center">VELOCITY</span>
        </div>

        <div className="max-h-[600px] overflow-y-auto">
          {filtered.length > 0 ? (
            filtered.map((f, i) => (
              <ForecastRow key={f.slug || i} forecast={f} rank={i + 1} />
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
          <div className="text-[9px] font-semibold tracking-widest uppercase text-text-2 mb-2">METHODOLOGY</div>
          <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
            {Object.entries(data.methodology).map(([key, val]) => (
              <div key={key} className="flex justify-between">
                <span className="text-text-2">{key.replace(/_/g, " ").toUpperCase()}</span>
                <span className="text-text-1">{val}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
