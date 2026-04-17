export default function PortfolioPanel({ portfolio }) {
  if (!portfolio || !portfolio.account) return null;

  const { account, positions = [] } = portfolio;
  const pnlColor = account.pnl >= 0 ? "text-green" : "text-red";
  const pnlPrefix = account.pnl >= 0 ? "+" : "";

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-gold">◆</span> PORTFOLIO
          <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">
            {positions.length}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-mono">
          <span className="text-text-2">
            VALUE{" "}
            <span className="text-text-0 font-semibold tabular-nums">
              ${account.total_value?.toLocaleString() || "—"}
            </span>
          </span>
          <span className={`font-semibold tabular-nums ${pnlColor}`}>
            {pnlPrefix}${account.pnl?.toFixed(2)} ({pnlPrefix}
            {account.pnl_pct?.toFixed(1)}%)
          </span>
        </div>
      </div>

      {/* Account summary bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-border text-center text-[9px] font-mono">
        {[
          { label: "CASH", value: `$${account.cash?.toLocaleString()}` },
          { label: "INVESTED", value: `$${account.positions_value?.toLocaleString()}` },
          { label: "P&L", value: `${pnlPrefix}$${account.pnl?.toFixed(2)}`, color: pnlColor },
          { label: "RETURN", value: `${pnlPrefix}${account.pnl_pct?.toFixed(1)}%`, color: pnlColor },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-bg-1 py-2.5 hover:bg-bg-2/50 transition-colors">
            <div className="text-text-2">{label}</div>
            <div className={`font-semibold tabular-nums ${color || "text-text-0"}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Positions table */}
      <div className="max-h-[350px] overflow-y-auto">
        {positions.map((pos, i) => {
          const isClosed = pos.status === "closed" || !!pos.close_reason;
          const isLive = pos.live && !isClosed;
          const isUnpriced = !pos.live && !isClosed;

          // Determine P&L and color
          const pnl = pos.pnl;
          const hasPnl = pnl != null;
          const posColor = isClosed
            ? (pnl > 0 ? "text-green" : pnl < 0 ? "text-red" : "text-text-2")
            : isLive
              ? (pos.status === "winning" ? "text-green" : pos.status === "losing" ? "text-red" : "text-text-2")
              : "text-text-2";
          const posPnlPrefix = (pnl || 0) >= 0 ? "+" : "";

          // Status badge
          const badge = isClosed
            ? { text: pos.close_reason || "CLOSED", color: pnl > 0 ? "bg-green/10 text-green" : "bg-red/10 text-red" }
            : isUnpriced
              ? { text: "DELISTED", color: "bg-yellow-500/10 text-yellow-400" }
              : null;

          return (
            <div
              key={pos.id || i}
              className={`grid grid-cols-[1fr_80px_80px_110px] items-center px-4 py-2.5 border-b border-border hover:bg-bg-2/70 transition-colors ${isClosed ? "opacity-70" : ""}`}
            >
              <div className="min-w-0">
                <div className="text-[11px] font-mono text-text-0 truncate flex items-center gap-2">
                  <span>
                    <span className="text-text-2 mr-1">#{pos.id}</span>
                    {pos.question}
                  </span>
                  {badge && (
                    <span className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[8px] font-bold ${badge.color}`}>
                      {badge.text.length > 20 ? badge.text.slice(0, 20) : badge.text}
                    </span>
                  )}
                </div>
                <div className="text-[9px] text-text-2 mt-0.5">
                  {pos.side?.toUpperCase()} @ {(pos.entry_price * 100).toFixed(0)}¢
                  {isClosed && pos.exit_price != null && (
                    <span className="ml-1">→ {(pos.exit_price * 100).toFixed(0)}¢</span>
                  )}
                </div>
              </div>
              <div className="text-[10px] font-mono text-text-1 text-center tabular-nums">
                {isLive && pos.current_price != null
                  ? `${(pos.current_price * 100).toFixed(0)}¢`
                  : isClosed && pos.exit_price != null
                    ? `${(pos.exit_price * 100).toFixed(0)}¢`
                    : isUnpriced
                      ? <span className="text-yellow-400/60">n/a</span>
                      : "—"}
              </div>
              <div className="text-[10px] font-mono text-center tabular-nums">
                {isLive
                  ? <span className="text-text-0">${pos.current_value?.toFixed(0)}</span>
                  : isClosed
                    ? <span className="text-text-2">${pos.current_value?.toFixed(0)}</span>
                    : isUnpriced
                      ? <span className="text-yellow-400/60">${pos.invested?.toFixed(0)}</span>
                      : "—"}
              </div>
              <div className={`text-[10px] font-mono font-semibold text-right tabular-nums ${posColor}`}>
                {hasPnl
                  ? `${posPnlPrefix}$${pnl.toFixed(0)} (${posPnlPrefix}${pos.pnl_pct?.toFixed(1)}%)`
                  : isUnpriced
                    ? <span className="text-yellow-400/60">at cost</span>
                    : "—"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
