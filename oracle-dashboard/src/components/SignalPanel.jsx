/**
 * SignalPanel — Displays signal fusion results, ensemble votes, and TA indicators.
 * The nerve center view for ORACLE's analysis pipeline.
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
    <div className="space-y-3">
      {/* Signal Fusion Card */}
      {latestFusion && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 bg-bg-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2">
              Signal Fusion
            </span>
            <span className="text-[10px] text-text-2">
              {latestFusion.signal_count} signals
            </span>
          </div>
          <div className="p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className={`text-2xl font-bold ${
                  latestFusion.recommendation.includes("YES") ? "text-green" :
                  latestFusion.recommendation.includes("NO") ? "text-red" : "text-text-2"
                }`}>
                  {latestFusion.recommendation}
                </span>
              </div>
              <div className="text-right">
                <div className="text-gold text-xl font-bold">
                  {latestFusion.edge_pct.toFixed(1)}%
                </div>
                <div className="text-[10px] text-text-2 uppercase">Edge</div>
              </div>
            </div>

            {/* Metrics row */}
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="bg-bg-2 rounded p-2">
                <div className="text-xs text-text-2">Direction</div>
                <div className={`font-bold ${latestFusion.direction > 0 ? "text-green" : "text-red"}`}>
                  {latestFusion.direction > 0 ? "↑" : "↓"} {Math.abs(latestFusion.direction).toFixed(3)}
                </div>
              </div>
              <div className="bg-bg-2 rounded p-2">
                <div className="text-xs text-text-2">Confidence</div>
                <div className="text-text-0 font-bold">
                  {(latestFusion.confidence * 100).toFixed(1)}%
                </div>
              </div>
              <div className="bg-bg-2 rounded p-2">
                <div className="text-xs text-text-2">Agreement</div>
                <div className="text-text-0 font-bold">
                  {(latestFusion.agreement * 100).toFixed(1)}%
                </div>
              </div>
            </div>

            {/* Signal breakdown */}
            {latestFusion.breakdown && latestFusion.breakdown.length > 0 && (
              <div className="mt-3 space-y-1">
                <div className="text-[10px] text-text-2 uppercase tracking-wider">
                  Top Signals
                </div>
                {latestFusion.breakdown.slice(0, 5).map((b, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className="text-text-2 truncate w-32">{b.source}</span>
                    <div className="flex-1 mx-2 h-1 bg-bg-3 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${b.direction > 0 ? "bg-green" : "bg-red"}`}
                        style={{ width: `${Math.min(Math.abs(b.contribution) * 500, 100)}%` }}
                      />
                    </div>
                    <span className={`font-mono w-16 text-right ${
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
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 bg-bg-2 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2">
              Ensemble ({latestEnsemble.model_count} models)
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
              <span className="text-gold font-bold">
                {latestEnsemble.edge_pct.toFixed(1)}% edge
              </span>
            </div>

            {/* Model votes */}
            {latestEnsemble.votes && (
              <div className="space-y-2">
                {latestEnsemble.votes.map((v, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={`w-2 h-2 rounded-full ${
                      v.direction > 0 ? "bg-green" : "bg-red"
                    }`} />
                    <span className="text-text-2 truncate flex-1">{v.name}</span>
                    <span className={`font-mono ${v.direction > 0 ? "text-green" : "text-red"}`}>
                      {v.direction > 0 ? "↑" : "↓"}{Math.abs(v.direction).toFixed(2)}
                    </span>
                    <span className="text-text-2 font-mono w-12 text-right">
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
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 bg-bg-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2">
              Executor
            </span>
            <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${
              executor.positions ? "bg-green/20 text-green" : "bg-bg-3 text-text-2"
            }`}>
              Paper
            </span>
          </div>
          <div className="p-4 grid grid-cols-3 gap-2 text-center text-xs">
            <div>
              <div className="text-text-2">Open</div>
              <div className="text-text-0 font-bold">
                {(executor.positions || []).filter(p => p.status === "open").length}
              </div>
            </div>
            <div>
              <div className="text-text-2">Total</div>
              <div className="text-text-0 font-bold">{executor.total_trades || 0}</div>
            </div>
            <div>
              <div className="text-text-2">P&L</div>
              <div className={`font-bold ${
                (executor.daily_pnl || 0) >= 0 ? "text-green" : "text-red"
              }`}>
                ${(executor.daily_pnl || 0).toFixed(2)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Crypto 15m Markets */}
      {crypto.length > 0 && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 bg-bg-2 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2">
              15m Crypto Markets
            </span>
          </div>
          <div className="p-4 space-y-2">
            {crypto.slice(-5).map((c, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="text-text-0 font-bold">{c.crypto}</span>
                {c.crypto_price && (
                  <span className="text-text-2 font-mono">
                    ${c.crypto_price.toLocaleString()}
                  </span>
                )}
                <span className={`font-bold ${
                  c.recommendation === "HOLD" ? "text-text-2" :
                  c.recommendation?.includes("YES") ? "text-green" : "text-red"
                }`}>
                  {c.recommendation || "—"}
                </span>
                <span className="text-gold font-mono">
                  {(c.confidence * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TA Signals Summary */}
      {ta.length > 0 && (
        <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2 bg-bg-2 border-b border-border">
            <span className="text-[10px] tracking-[2px] uppercase text-text-2">
              Technical Signals ({ta.length})
            </span>
          </div>
          <div className="p-4 space-y-1">
            {ta.slice(-8).map((s, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="text-text-2 truncate w-28">{s.source}</span>
                <span className={`font-mono ${s.direction > 0 ? "text-green" : "text-red"}`}>
                  {s.direction > 0 ? "↑" : "↓"}{Math.abs(s.direction).toFixed(3)}
                </span>
                <div className="w-16 h-1 bg-bg-3 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gold rounded-full"
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
