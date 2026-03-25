import { useState, useEffect } from "react";

export default function ResearchPanel({ data }) {
  if (!data) return null;

  const { experiments, current_params, last_experiment, log, research_program } = data;

  return (
    <div className="space-y-3">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="EXPERIMENTS"
          value={experiments?.total || 0}
          sub="total runs"
        />
        <StatCard
          label="ACCEPTED"
          value={experiments?.accepted || 0}
          sub={`${experiments?.acceptance_rate || 0}% rate`}
          accent={experiments?.accepted > 0}
        />
        <StatCard
          label="REJECTED"
          value={experiments?.rejected || 0}
          sub="no improvement"
        />
        <StatCard
          label="CUMULATIVE"
          value={`${experiments?.cumulative_pnl_delta >= 0 ? "+" : ""}$${experiments?.cumulative_pnl_delta?.toFixed(2) || "0.00"}`}
          sub="PnL delta"
          accent={experiments?.cumulative_pnl_delta > 0}
          negative={experiments?.cumulative_pnl_delta < 0}
        />
      </div>

      {/* Last Experiment */}
      {last_experiment && (
        <div className="bg-bg-1 border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[11px] tracking-[0.2em] text-text-2 uppercase">Latest Experiment</h3>
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
              last_experiment.accepted
                ? "bg-green/10 text-green border border-green/30"
                : "bg-red/10 text-red border border-red/30"
            }`}>
              {last_experiment.accepted ? "ACCEPTED" : "REJECTED"}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-2">
              <div className="text-[10px] text-text-2 uppercase tracking-wider">Mutation</div>
              <div className="bg-bg-2 rounded px-3 py-2 font-mono text-xs">
                <span className="text-gold">{last_experiment.mutation?.parameter}</span>
                <span className="text-text-2 mx-2">&rarr;</span>
                <span className="text-red line-through mr-2">{formatValue(last_experiment.mutation?.old_value)}</span>
                <span className="text-green">{formatValue(last_experiment.mutation?.new_value)}</span>
              </div>
            </div>
            <div className="space-y-2">
              <div className="text-[10px] text-text-2 uppercase tracking-wider">Result</div>
              <div className="bg-bg-2 rounded px-3 py-2 font-mono text-xs text-text-1">
                {last_experiment.reason}
              </div>
            </div>
          </div>

          {/* Baseline vs Candidate comparison */}
          <div className="mt-3 grid grid-cols-2 gap-3">
            <ResultBox
              label="BASELINE"
              result={last_experiment.baseline_result}
            />
            <ResultBox
              label="CANDIDATE"
              result={last_experiment.candidate_result}
              highlight={last_experiment.accepted}
            />
          </div>
        </div>
      )}

      {/* Current Optimized Parameters */}
      {current_params && (
        <div className="bg-bg-1 border border-border rounded-lg p-4">
          <h3 className="text-[11px] tracking-[0.2em] text-text-2 uppercase mb-3">Optimized Parameters</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(current_params).map(([strategy, params]) => (
              <ParamBlock key={strategy} strategy={strategy} params={params} />
            ))}
          </div>
        </div>
      )}

      {/* Experiment Log */}
      {log && log.length > 0 && (
        <div className="bg-bg-1 border border-border rounded-lg p-4">
          <h3 className="text-[11px] tracking-[0.2em] text-text-2 uppercase mb-3">
            Experiment History ({log.length})
          </h3>
          <div className="space-y-1 max-h-[400px] overflow-y-auto">
            {[...log].reverse().map((entry, i) => (
              <div
                key={entry.id || i}
                className={`flex items-center gap-3 px-3 py-2 rounded text-xs font-mono ${
                  entry.accepted ? "bg-green/5" : "bg-bg-2"
                }`}
              >
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  entry.accepted ? "bg-green" : "bg-red/50"
                }`} />
                <span className="text-gold w-[180px] truncate flex-shrink-0">
                  {entry.mutation?.parameter}
                </span>
                <span className="text-text-2 w-[60px] flex-shrink-0">
                  {formatValue(entry.mutation?.old_value)}&rarr;{formatValue(entry.mutation?.new_value)}
                </span>
                <span className={`flex-1 truncate ${entry.accepted ? "text-green" : "text-text-2"}`}>
                  {entry.reason}
                </span>
                <span className="text-text-2 text-[10px] flex-shrink-0">
                  {entry.timestamp ? new Date(entry.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Research Program */}
      {research_program && (
        <div className="bg-bg-1 border border-border rounded-lg p-4">
          <h3 className="text-[11px] tracking-[0.2em] text-text-2 uppercase mb-3">Research Program</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
            <div>
              <div className="text-[10px] text-gold uppercase tracking-wider mb-2">Objectives</div>
              <ul className="space-y-1 text-text-1">
                {research_program.objectives?.map((o, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-gold flex-shrink-0">{i + 1}.</span>
                    <span>{o}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-[10px] text-red uppercase tracking-wider mb-2">Constraints</div>
              <ul className="space-y-1 text-text-2">
                {research_program.constraints?.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-red/60 flex-shrink-0">!</span>
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, accent, negative }) {
  return (
    <div className="bg-bg-1 border border-border rounded-lg p-3">
      <div className="text-[10px] tracking-[0.2em] text-text-2 uppercase">{label}</div>
      <div className={`text-xl font-bold mt-1 ${
        accent ? "text-green" : negative ? "text-red" : "text-text-1"
      }`}>
        {value}
      </div>
      <div className="text-[10px] text-text-2 mt-0.5">{sub}</div>
    </div>
  );
}

function ResultBox({ label, result, highlight }) {
  if (!result) return null;
  return (
    <div className={`bg-bg-2 rounded px-3 py-2 ${highlight ? "ring-1 ring-green/30" : ""}`}>
      <div className="text-[10px] text-text-2 uppercase tracking-wider mb-1">{label}</div>
      <div className="grid grid-cols-2 gap-1 text-xs font-mono">
        <span className="text-text-2">PnL:</span>
        <span className={result.total_pnl >= 0 ? "text-green" : "text-red"}>
          ${result.total_pnl?.toFixed(2)}
        </span>
        <span className="text-text-2">Sharpe:</span>
        <span className="text-text-1">{result.sharpe?.toFixed(3)}</span>
        <span className="text-text-2">Win Rate:</span>
        <span className="text-text-1">{result.win_rate}%</span>
        <span className="text-text-2">Drawdown:</span>
        <span className="text-text-1">${result.max_drawdown?.toFixed(2)}</span>
      </div>
    </div>
  );
}

function ParamBlock({ strategy, params }) {
  if (typeof params !== "object") return null;
  const label = strategy.replace(/_/g, " ").toUpperCase();
  const color = {
    bonds: "text-blue-400",
    expertise: "text-purple-400",
    crypto15m: "text-yellow-400",
    value: "text-emerald-400",
    fusion_weights: "text-gold",
  }[strategy] || "text-text-1";

  return (
    <div className="bg-bg-2 rounded px-3 py-2">
      <div className={`text-[10px] uppercase tracking-wider mb-2 ${color}`}>{label}</div>
      <div className="space-y-0.5">
        {Object.entries(params).map(([k, v]) => (
          <div key={k} className="flex justify-between text-[11px] font-mono">
            <span className="text-text-2 truncate mr-2">{k}</span>
            <span className="text-text-1 flex-shrink-0">{formatValue(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatValue(v) {
  if (v === null || v === undefined) return "-";
  if (Array.isArray(v)) return v.join("-");
  if (typeof v === "number") return v % 1 === 0 ? v.toString() : v.toFixed(v < 1 ? 4 : 2);
  return String(v);
}
