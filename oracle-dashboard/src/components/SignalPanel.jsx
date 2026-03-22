/**
 * SignalPanel — Displays signal fusion results, ensemble votes, and TA indicators.
 */

export default function SignalPanel({ signals }) {
  if (!signals) return null;

  const fusion = signals.fusion || [];
  const ensemble = signals.ensemble || [];
  const ta = signals.ta_signals || [];
  const executor = signals.executor;
  const crypto = signals.crypto_15m || [];

  const latestFusion = fusion[fusion.length - 1];
  const latestEnsemble = ensemble[ensemble.length - 1];

  return (
    <div className="space-y-3 stagger-children">
      {/* Signal Fusion Card */}
      {latestFusion && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
          <div className="px-4 py-2.5 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border flex items-center justify-between">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2 flex items-center gap-2">
              <span className="text-gold">◆</span> Signal Fusion
            </span>
            <span className="text-[10px] text-text-2 bg-bg-3 px-2 py-0.5 rounded-full">
              {latestFusion.signal_count} signals
            </span>
          </div>
          <div className="p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <span className={`text-2xl font-bold ${
                  latestFusion.recommendation.includes("YES") ? "text-green" :
                  latestFusion.recommendation.includes("NO") ? "text-red" : "text-text-2"
                }`}>
                  {latestFusion.recommendation}
                </span>
              </div>
              <div className="text-right">
                <div className="text-gold text-xl font-bold tabular-nums">
                  {latestFusion.edge_pct.toFixed(1)}%
                </div>
                <div className="text-[9px] text-text-2 uppercase tracking-wider">Edge</div>
              </div>
            </div>

            {/* Metrics row */}
            <div className="grid grid-cols-3 gap-2 text-center">
              {[
                {
                  label: "Direction",
                  value: `${latestFusion.direction > 0 ? "↑" : "↓"} ${Math.abs(latestFusion.direction).toFixed(3)}`,
                  color: latestFusion.direction > 0 ? "text-green" : "text-red"
                },
                {
                  label: "Confidence",
                  value: `${(latestFusion.confidence * 100).toFixed(1)}%`,
                  color: "text-text-0"
                },
                {
                  label: "Agreement",
                  value: `${(latestFusion.agreement * 100).toFixed(1)}%`,
                  color: "text-text-0"
                }
              ].map(({ label, value, color }) => (
                <div key={label} className="bg-bg-2/80 rounded-lg p-2.5">
                  <div className="text-[10px] text-text-2 mb-0.5">{label}</div>
                  <div className={`font-bold tabular-nums ${color}`}>{value}</div>
                </div>
              ))}
            </div>

            {/* Signal breakdown */}
            {latestFusion.breakdown && latestFusion.breakdown.length > 0 && (
              <div className="mt-4 space-y-1.5">
                <div className="text-[10px] text-text-2 uppercase tracking-wider">
                  Top Signals
                </div>
                {latestFusion.breakdown.slice(0, 5).map((b, i) => (
                  <div key={i} className="flex items-center justify-between text-xs group">
                    <span className="text-text-2 truncate w-32 group-hover:text-text-1 transition-colors">{b.source}</span>
                    <div className="flex-1 mx-2 h-1.5 bg-bg-3 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${b.direction > 0 ? "bg-green" : "bg-red"}`}
                        style={{ width: `${Math.min(Math.abs(b.contribution) * 500, 100)}%` }}
                      />
                    </div>
                    <span className={`font-mono w-16 text-right tabular-nums ${
                      b.contribution > 0 ? "text-green" : "text-red"
                    }`}>
                      {b.contribution > 0 ? "+" : ""}{b.contribution.toFixed(4)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Ensemble Card */}
      {latestEnsemble && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
          <div className="px-4 py-2.5 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2 flex items-center gap-2">
              <span className="text-purple">◆</span> Ensemble ({latestEnsemble.model_count} models)
            </span>
          </div>
          <div className="p-4">
            <div className="flex items-center justify-between mb-3">
              <span className={`text-lg font-bold ${
                latestEnsemble.recommendation.includes("YES") ? "text-green" :
                latestEnsemble.recommendation.includes("NO") ? "text-red" : "text-text-2"
              }`}>
                {latestEnsemble.recommendation}
              </span>
              <span className="text-gold font-bold tabular-nums">
                {latestEnsemble.edge_pct.toFixed(1)}% edge
              </span>
            </div>

            {/* Model votes */}
            {latestEnsemble.votes && (
              <div className="space-y-2">
                {latestEnsemble.votes.map((v, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs group">
                    <span className={`w-2 h-2 rounded-full ${
                      v.direction > 0 ? "bg-green" : "bg-red"
                    }`} />
                    <span className="text-text-2 truncate flex-1 group-hover:text-text-1 transition-colors">{v.name}</span>
                    <span className={`font-mono tabular-nums ${v.direction > 0 ? "text-green" : "text-red"}`}>
                      {v.direction > 0 ? "↑" : "↓"}{Math.abs(v.direction).toFixed(2)}
                    </span>
                    <span className="text-text-2 font-mono w-12 text-right tabular-nums">
                      {(v.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Executor Status */}
      {executor && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
          <div className="px-4 py-2.5 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border flex items-center justify-between">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2 flex items-center gap-2">
              <span className="text-cyan">◆</span> Executor
            </span>
            <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${
              executor.positions ? "bg-green/15 text-green border border-green/20" : "bg-bg-3 text-text-2"
            }`}>
              Paper
            </span>
          </div>
          <div className="p-4 grid grid-cols-3 gap-2 text-center text-xs">
            {[
              { label: "Open", value: (executor.positions || []).filter(p => p.status === "open").length },
              { label: "Total", value: executor.total_trades || 0 },
              { label: "P&L", value: `$${(executor.daily_pnl || 0).toFixed(2)}`, color: (executor.daily_pnl || 0) >= 0 ? "text-green" : "text-red" },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-bg-2/80 rounded-lg p-2.5">
                <div className="text-text-2 text-[10px]">{label}</div>
                <div className={`font-bold tabular-nums ${color || "text-text-0"}`}>{value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Crypto 15m Markets */}
      {crypto.length > 0 && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
          <div className="px-4 py-2.5 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2 flex items-center gap-2">
              <span className="text-blue">◆</span> 15m Crypto Markets
            </span>
          </div>
          <div className="p-4 space-y-2">
            {crypto.slice(-5).map((c, i) => (
              <div key={i} className="flex items-center justify-between text-xs hover:bg-bg-2/50 -mx-2 px-2 py-1 rounded transition-colors">
                <span className="text-text-0 font-bold">{c.crypto}</span>
                {c.crypto_price && (
                  <span className="text-text-2 font-mono tabular-nums">
                    ${c.crypto_price.toLocaleString()}
                  </span>
                )}
                <span className={`font-bold ${
                  c.recommendation === "HOLD" ? "text-text-2" :
                  c.recommendation?.includes("YES") ? "text-green" : "text-red"
                }`}>
                  {c.recommendation || "—"}
                </span>
                <span className="text-gold font-mono tabular-nums">
                  {(c.confidence * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TA Signals Summary */}
      {ta.length > 0 && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
          <div className="px-4 py-2.5 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2 flex items-center gap-2">
              <span className="text-orange">◆</span> Technical Signals ({ta.length})
            </span>
          </div>
          <div className="p-4 space-y-1.5">
            {ta.slice(-8).map((s, i) => (
              <div key={i} className="flex items-center justify-between text-xs group">
                <span className="text-text-2 truncate w-28 group-hover:text-text-1 transition-colors">{s.source}</span>
                <span className={`font-mono tabular-nums ${s.direction > 0 ? "text-green" : "text-red"}`}>
                  {s.direction > 0 ? "↑" : "↓"}{Math.abs(s.direction).toFixed(3)}
                </span>
                <div className="w-16 h-1.5 bg-bg-3 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gold rounded-full transition-all duration-500"
                    style={{ width: `${s.strength * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
