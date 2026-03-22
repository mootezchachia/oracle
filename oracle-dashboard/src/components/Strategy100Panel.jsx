export default function Strategy100Panel({ data }) {
  if (!data || !data.account) return null;

  const { account, allocations, positions = [], closed_trades = [], stats } = data;
  const returnColor = (account.total_return || 0) >= 0 ? "text-green" : "text-red";
  const returnPrefix = (account.total_return || 0) >= 0 ? "+" : "";

  const strategyColors = {
    bonds: "#4ade80",
    expertise: "#60a5fa",
    flash_crash: "#f59e0b",
  };

  const strategyLabels = {
    bonds: "BONDS",
    expertise: "EXPERTISE",
    flash_crash: "CRASH",
  };

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-bg-2">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span style={{ color: "#f59e0b" }}>$</span> $100 STRATEGY
          <span className="bg-bg-3 text-text-1 text-[9px] px-1.5 rounded-full">
            PAPER
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-mono">
          <span className="text-text-2">
            VALUE{" "}
            <span className="text-text-0 font-semibold">
              ${account.total_value?.toFixed(2) || "100.00"}
            </span>
          </span>
          <span className={`font-semibold ${returnColor}`}>
            {returnPrefix}${account.total_return?.toFixed(2) || "0.00"} ({returnPrefix}
            {account.total_return_pct?.toFixed(1) || "0.0"}%)
          </span>
        </div>
      </div>

      {/* Allocation bars */}
      {allocations && (
        <div className="grid grid-cols-3 gap-px bg-border text-center text-[9px] font-mono">
          {Object.entries(allocations).map(([key, alloc]) => {
            const pct = alloc.budget > 0 ? (alloc.invested / alloc.budget) * 100 : 0;
            return (
              <div key={key} className="bg-bg-1 py-2 relative overflow-hidden">
                <div
                  className="absolute inset-0 opacity-10"
                  style={{
                    background: strategyColors[key],
                    width: `${pct}%`,
                  }}
                />
                <div className="relative">
                  <div className="text-text-2" style={{ color: strategyColors[key] }}>
                    {strategyLabels[key] || key.toUpperCase()}
                  </div>
                  <div className="text-text-0 font-semibold">
                    ${alloc.invested?.toFixed(0)} / ${alloc.budget?.toFixed(0)}
                  </div>
                  <div className="text-text-2">
                    ${alloc.cash?.toFixed(2)} free
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-4 gap-px bg-border text-center text-[9px] font-mono">
          <div className="bg-bg-1 py-1.5">
            <span className="text-text-2">TRADES </span>
            <span className="text-text-0">{stats.total_trades}</span>
          </div>
          <div className="bg-bg-1 py-1.5">
            <span className="text-text-2">W/L </span>
            <span className="text-green">{stats.wins}</span>
            <span className="text-text-2">/</span>
            <span className="text-red">{stats.losses}</span>
          </div>
          <div className="bg-bg-1 py-1.5">
            <span className="text-text-2">P&L </span>
            <span className={stats.total_pnl >= 0 ? "text-green" : "text-red"}>
              {stats.total_pnl >= 0 ? "+" : ""}${stats.total_pnl?.toFixed(2)}
            </span>
          </div>
          <div className="bg-bg-1 py-1.5">
            <span className="text-text-2">CASH </span>
            <span className="text-text-0">${account.total_cash?.toFixed(2)}</span>
          </div>
        </div>
      )}

      {/* Open positions */}
      <div className="max-h-[300px] overflow-y-auto">
        {positions.length > 0 ? (
          positions.map((pos, i) => {
            const hasPnl = pos.pnl != null;
            const posColor = hasPnl
              ? pos.pnl > 0.5 ? "text-green" : pos.pnl < -0.5 ? "text-red" : "text-text-2"
              : "text-text-2";
            const pnlPrefix = (pos.pnl || 0) >= 0 ? "+" : "";

            return (
              <div
                key={pos.id || i}
                className="grid grid-cols-[auto_1fr_70px_70px_90px] items-center px-4 py-2 border-b border-border hover:bg-bg-2 gap-2"
              >
                <span
                  className="text-[8px] font-mono font-bold px-1.5 py-0.5 rounded"
                  style={{
                    color: strategyColors[pos.strategy] || "#888",
                    background: `${strategyColors[pos.strategy] || "#888"}20`,
                  }}
                >
                  {strategyLabels[pos.strategy] || pos.strategy?.toUpperCase()}
                </span>
                <div className="min-w-0">
                  <div className="text-[11px] font-mono text-text-0 truncate">
                    {pos.question}
                  </div>
                  <div className="text-[9px] text-text-2 mt-0.5">
                    {pos.side?.toUpperCase()} @ {(pos.entry_price * 100).toFixed(0)}c
                  </div>
                </div>
                <div className="text-[10px] font-mono text-text-1 text-center">
                  {pos.current_price != null
                    ? `${(pos.current_price * 100).toFixed(0)}c`
                    : "..."}
                </div>
                <div className="text-[10px] font-mono text-center text-text-0">
                  ${pos.invested?.toFixed(0)}
                </div>
                <div className={`text-[10px] font-mono font-semibold text-right ${posColor}`}>
                  {hasPnl
                    ? `${pnlPrefix}$${pos.pnl.toFixed(2)} (${pnlPrefix}${pos.pnl_pct?.toFixed(1)}%)`
                    : "..."}
                </div>
              </div>
            );
          })
        ) : (
          <div className="px-4 py-6 text-center text-[10px] text-text-2">
            No open positions. Run <code className="bg-bg-3 px-1 rounded">./run.sh 100 auto</code> to start.
          </div>
        )}

        {/* Closed trades */}
        {closed_trades.length > 0 && (
          <>
            <div className="px-4 py-1.5 bg-bg-2 text-[9px] text-text-2 font-mono tracking-wider">
              CLOSED ({closed_trades.length})
            </div>
            {closed_trades.map((t, i) => {
              const pnlColor = (t.pnl || 0) >= 0 ? "text-green" : "text-red";
              const pnlPfx = (t.pnl || 0) >= 0 ? "+" : "";
              return (
                <div
                  key={`closed-${t.id || i}`}
                  className="grid grid-cols-[auto_1fr_90px] items-center px-4 py-1.5 border-b border-border opacity-60 gap-2"
                >
                  <span
                    className="text-[8px] font-mono px-1.5 py-0.5 rounded"
                    style={{
                      color: strategyColors[t.strategy] || "#888",
                      background: `${strategyColors[t.strategy] || "#888"}15`,
                    }}
                  >
                    {strategyLabels[t.strategy] || t.strategy?.toUpperCase()}
                  </span>
                  <div className="text-[10px] font-mono text-text-2 truncate">
                    {t.question}
                  </div>
                  <div className={`text-[10px] font-mono font-semibold text-right ${pnlColor}`}>
                    {pnlPfx}${t.pnl?.toFixed(2)} ({t.close_reason})
                  </div>
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
