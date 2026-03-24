import { useState, useMemo } from "react";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "./Strategy100Panel";

const EVENT_CONFIG = {
  scan: { icon: "~", color: "#8b5cf6", label: "SCAN" },
  trade_opened: { icon: "+", color: "#4ade80", label: "OPEN" },
  trade_closed: { icon: "x", color: "#f87171", label: "CLOSE" },
  trade_open: { icon: "+", color: "#4ade80", label: "EXEC" },
  trade_close: { icon: "x", color: "#f87171", label: "EXIT" },
  price_tracking: { icon: "#", color: "#60a5fa", label: "TRACK" },
};

function formatTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts.slice(0, 10);
  const diff = Date.now() - d;
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
  return d.toISOString().slice(0, 10);
}

function formatFullDate(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function strategyColor(s) {
  return STRATEGY_COLORS[s] || "#888";
}

function StrategyBadge({ strategy }) {
  const color = strategyColor(strategy);
  return (
    <span
      className="text-[8px] font-bold px-1.5 py-0.5 rounded"
      style={{ color, background: color + "20", border: `1px solid ${color}30` }}
    >
      {STRATEGY_LABELS[strategy] || strategy?.toUpperCase()}
    </span>
  );
}

function PnlCell({ pnl, pnl_pct }) {
  if (pnl == null) return <span className="text-text-2">—</span>;
  const color = pnl >= 0 ? "text-green" : "text-red";
  const prefix = pnl >= 0 ? "+" : "";
  return (
    <span className={`font-semibold ${color}`}>
      {prefix}${pnl.toFixed(2)}
      {pnl_pct != null && <span className="text-text-2 font-normal"> ({prefix}{pnl_pct.toFixed(1)}%)</span>}
    </span>
  );
}

function PriceCell({ price }) {
  if (price == null) return <span className="text-text-2">—</span>;
  return <span>{(price * 100).toFixed(0)}c</span>;
}

// ─── OPENS VIEW ─────────────────────────────────────────────
function OpensView({ events }) {
  const opens = events.filter(e => e.type === "trade_opened" || e.type === "trade_open");
  // Separate rich trade_opened events from scan-derived trade_open
  const richOpens = opens.filter(e => e.type === "trade_opened");
  const scanOpens = opens.filter(e => e.type === "trade_open");

  // Group by strategy
  const byStrategy = {};
  let totalInvested = 0;
  for (const e of richOpens) {
    const s = e.detail.strategy || "unknown";
    if (!byStrategy[s]) byStrategy[s] = [];
    byStrategy[s].push(e);
    totalInvested += e.detail.invested || 0;
  }

  return (
    <div>
      <div className="px-4 py-2.5 bg-bg-2/60 border-b border-border flex items-center gap-3 text-[9px] font-mono text-text-2">
        <span className="text-green font-bold">{opens.length} TRADES OPENED</span>
        <span>Total invested: <span className="text-text-0">${totalInvested.toFixed(2)}</span></span>
        {Object.entries(byStrategy).map(([s, trades]) => (
          <span key={s} style={{ color: strategyColor(s) }}>
            {STRATEGY_LABELS[s] || s.toUpperCase()}: {trades.length}
          </span>
        ))}
      </div>

      {richOpens.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-text-2 text-left border-b border-border bg-bg-2/30">
                <th className="px-3 py-1.5 font-medium">DATE</th>
                <th className="px-3 py-1.5 font-medium">PORTFOLIO</th>
                <th className="px-3 py-1.5 font-medium">STRATEGY</th>
                <th className="px-3 py-1.5 font-medium">MARKET</th>
                <th className="px-3 py-1.5 font-medium text-right">SIDE</th>
                <th className="px-3 py-1.5 font-medium text-right">ENTRY</th>
                <th className="px-3 py-1.5 font-medium text-right">SHARES</th>
                <th className="px-3 py-1.5 font-medium text-right">INVESTED</th>
              </tr>
            </thead>
            <tbody>
              {richOpens.map((e, i) => (
                <tr key={i} className="border-b border-border hover:bg-bg-2/40 transition-colors">
                  <td className="px-3 py-2 text-text-2 tabular-nums" title={formatFullDate(e.timestamp)}>
                    {formatTime(e.timestamp)}
                  </td>
                  <td className="px-3 py-2">
                    <span className="text-[8px] bg-bg-3 px-1.5 py-0.5 rounded">{e.detail.portfolio}</span>
                  </td>
                  <td className="px-3 py-2"><StrategyBadge strategy={e.detail.strategy} /></td>
                  <td className="px-3 py-2 text-text-0 max-w-[300px] truncate">{e.detail.question || e.detail.slug}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={e.detail.side === "yes" ? "text-green" : "text-red"}>
                      {e.detail.side?.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums"><PriceCell price={e.detail.entry_price} /></td>
                  <td className="px-3 py-2 text-right tabular-nums text-text-2">{e.detail.shares?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-text-0">${e.detail.invested?.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scanOpens.length > 0 && (
        <>
          <div className="px-4 py-1.5 bg-bg-2/40 text-[9px] text-text-2 font-mono tracking-wider border-b border-border">
            EXECUTION LOG ({scanOpens.length})
          </div>
          {scanOpens.map((e, i) => (
            <div key={i} className="px-4 py-1.5 border-b border-border text-[10px] font-mono text-text-2 flex items-center gap-3 hover:bg-bg-2/30">
              <span className="tabular-nums text-text-2">{formatTime(e.timestamp)}</span>
              {e.detail.strategy && <StrategyBadge strategy={e.detail.strategy} />}
              <span className="text-text-0">{e.detail.slug}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ─── CLOSES VIEW ────────────────────────────────────────────
function ClosesView({ events }) {
  const closes = events.filter(e => e.type === "trade_closed" || e.type === "trade_close");
  const richCloses = closes.filter(e => e.type === "trade_closed");
  const scanCloses = closes.filter(e => e.type === "trade_close");

  let totalPnl = 0, wins = 0, losses = 0;
  for (const e of richCloses) {
    if (e.detail.pnl != null) {
      totalPnl += e.detail.pnl;
      if (e.detail.pnl >= 0) wins++; else losses++;
    }
  }

  return (
    <div>
      <div className="px-4 py-2.5 bg-bg-2/60 border-b border-border flex items-center gap-4 text-[9px] font-mono text-text-2">
        <span className="text-red font-bold">{closes.length} TRADES CLOSED</span>
        <span>W/L: <span className="text-green">{wins}</span>/<span className="text-red">{losses}</span></span>
        <span>Realized P&L: <PnlCell pnl={totalPnl} /></span>
      </div>

      {richCloses.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-text-2 text-left border-b border-border bg-bg-2/30">
                <th className="px-3 py-1.5 font-medium">CLOSED</th>
                <th className="px-3 py-1.5 font-medium">PORTFOLIO</th>
                <th className="px-3 py-1.5 font-medium">STRATEGY</th>
                <th className="px-3 py-1.5 font-medium">MARKET</th>
                <th className="px-3 py-1.5 font-medium text-right">SIDE</th>
                <th className="px-3 py-1.5 font-medium text-right">ENTRY</th>
                <th className="px-3 py-1.5 font-medium text-right">EXIT</th>
                <th className="px-3 py-1.5 font-medium text-right">INVESTED</th>
                <th className="px-3 py-1.5 font-medium text-right">P&L</th>
                <th className="px-3 py-1.5 font-medium">REASON</th>
              </tr>
            </thead>
            <tbody>
              {richCloses.map((e, i) => (
                <tr key={i} className="border-b border-border hover:bg-bg-2/40 transition-colors">
                  <td className="px-3 py-2 text-text-2 tabular-nums" title={formatFullDate(e.timestamp)}>
                    {formatTime(e.timestamp)}
                  </td>
                  <td className="px-3 py-2">
                    <span className="text-[8px] bg-bg-3 px-1.5 py-0.5 rounded">{e.detail.portfolio}</span>
                  </td>
                  <td className="px-3 py-2"><StrategyBadge strategy={e.detail.strategy} /></td>
                  <td className="px-3 py-2 text-text-0 max-w-[250px] truncate">{e.detail.question || e.detail.slug}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={e.detail.side === "yes" ? "text-green" : "text-red"}>
                      {e.detail.side?.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums"><PriceCell price={e.detail.entry_price} /></td>
                  <td className="px-3 py-2 text-right tabular-nums"><PriceCell price={e.detail.exit_price} /></td>
                  <td className="px-3 py-2 text-right tabular-nums text-text-2">${e.detail.invested?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    <PnlCell pnl={e.detail.pnl} pnl_pct={e.detail.pnl_pct} />
                  </td>
                  <td className="px-3 py-2 text-text-2 text-[9px] italic max-w-[150px] truncate">
                    {e.detail.close_reason || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scanCloses.length > 0 && (
        <>
          <div className="px-4 py-1.5 bg-bg-2/40 text-[9px] text-text-2 font-mono tracking-wider border-b border-border">
            EXIT LOG ({scanCloses.length})
          </div>
          {scanCloses.map((e, i) => (
            <div key={i} className="px-4 py-1.5 border-b border-border text-[10px] font-mono text-text-2 flex items-center gap-3 hover:bg-bg-2/30">
              <span className="tabular-nums">{formatTime(e.timestamp)}</span>
              <span className="text-text-0">Trade #{e.detail.id}</span>
              {e.detail.pnl != null && <PnlCell pnl={e.detail.pnl} />}
              {e.detail.reason && <span className="italic">{e.detail.reason}</span>}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ─── SCANS VIEW ─────────────────────────────────────────────
function ScansView({ events }) {
  const scans = events.filter(e => e.type === "scan");

  let totalExecuted = 0, totalClosed = 0, totalMarketsScanned = 0;
  for (const e of scans) {
    totalExecuted += e.detail.executed || 0;
    totalClosed += e.detail.closed || 0;
    totalMarketsScanned += e.detail.markets_scanned || 0;
  }

  return (
    <div>
      <div className="px-4 py-2.5 bg-bg-2/60 border-b border-border flex items-center gap-4 text-[9px] font-mono text-text-2">
        <span className="text-purple-400 font-bold">{scans.length} CRON SCANS</span>
        <span>Markets scanned: <span className="text-text-0">{totalMarketsScanned.toLocaleString()}</span></span>
        <span>Trades executed: <span className="text-green">{totalExecuted}</span></span>
        <span>Positions closed: <span className="text-red">{totalClosed}</span></span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[10px] font-mono">
          <thead>
            <tr className="text-text-2 text-left border-b border-border bg-bg-2/30">
              <th className="px-3 py-1.5 font-medium">TIME</th>
              <th className="px-3 py-1.5 font-medium text-right">MARKETS</th>
              <th className="px-3 py-1.5 font-medium text-right">BONDS</th>
              <th className="px-3 py-1.5 font-medium text-right">EXPERTISE</th>
              <th className="px-3 py-1.5 font-medium text-right">CRYPTO</th>
              <th className="px-3 py-1.5 font-medium text-right">VALUE</th>
              <th className="px-3 py-1.5 font-medium text-right">EXECUTED</th>
              <th className="px-3 py-1.5 font-medium text-right">CLOSED</th>
            </tr>
          </thead>
          <tbody>
            {scans.map((e, i) => {
              const d = e.detail;
              const hasAction = d.executed > 0 || d.closed > 0;
              return (
                <tr key={i} className={`border-b border-border hover:bg-bg-2/40 transition-colors ${hasAction ? "" : "opacity-50"}`}>
                  <td className="px-3 py-2 text-text-2 tabular-nums" title={formatFullDate(e.timestamp)}>
                    {formatTime(e.timestamp)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-text-0">{d.markets_scanned}</td>
                  <td className="px-3 py-2 text-right tabular-nums" style={{ color: "#4ade80" }}>{d.bonds_found}</td>
                  <td className="px-3 py-2 text-right tabular-nums" style={{ color: "#60a5fa" }}>{d.expertise_found}</td>
                  <td className="px-3 py-2 text-right tabular-nums" style={{ color: "#e879f9" }}>{d.crypto15m_found ?? d.crashes_found ?? 0}</td>
                  <td className="px-3 py-2 text-right tabular-nums" style={{ color: "#f59e0b" }}>{d.value_found ?? 0}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {d.executed > 0 ? <span className="text-green font-semibold">{d.executed}</span> : <span className="text-text-2">0</span>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {d.closed > 0 ? <span className="text-red font-semibold">{d.closed}</span> : <span className="text-text-2">0</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── STATS VIEW ─────────────────────────────────────────────
function StatBox({ label, value, sub, color }) {
  return (
    <div className="bg-bg-2/40 rounded-lg p-3 hover:bg-bg-2/60 transition-colors">
      <div className="text-[9px] text-text-2 font-mono tracking-wider mb-1">{label}</div>
      <div className={`text-[16px] font-mono font-bold tabular-nums ${color || "text-text-0"}`}>{value}</div>
      {sub && <div className="text-[9px] text-text-2 font-mono mt-0.5">{sub}</div>}
    </div>
  );
}

function StatsView({ events }) {
  const richTrades = events.filter(e => e.type === "trade_opened" || e.type === "trade_closed");
  const openTrades = richTrades.filter(e => e.type === "trade_opened" && e.detail.status !== "closed");
  const closedTrades = richTrades.filter(e => e.type === "trade_closed" || (e.type === "trade_opened" && e.detail.status === "closed"));
  const scans = events.filter(e => e.type === "scan");

  // P&L stats
  let totalPnl = 0, wins = 0, losses = 0, bestTrade = -Infinity, worstTrade = Infinity;
  let totalInvested = 0, totalReturned = 0;
  const pnlByStrategy = {};

  for (const e of closedTrades) {
    const pnl = e.detail?.pnl;
    if (pnl == null) continue;
    totalPnl += pnl;
    totalReturned += (e.detail.invested || 0) + pnl;
    totalInvested += e.detail.invested || 0;
    if (pnl >= 0) wins++; else losses++;
    if (pnl > bestTrade) bestTrade = pnl;
    if (pnl < worstTrade) worstTrade = pnl;

    const s = e.detail.strategy || "unknown";
    if (!pnlByStrategy[s]) pnlByStrategy[s] = { pnl: 0, trades: 0, wins: 0, losses: 0, invested: 0 };
    pnlByStrategy[s].pnl += pnl;
    pnlByStrategy[s].trades++;
    pnlByStrategy[s].invested += e.detail.invested || 0;
    if (pnl >= 0) pnlByStrategy[s].wins++; else pnlByStrategy[s].losses++;
  }

  // Open positions stats
  let openInvested = 0;
  const openByStrategy = {};
  for (const e of openTrades) {
    openInvested += e.detail.invested || 0;
    const s = e.detail.strategy || "unknown";
    if (!openByStrategy[s]) openByStrategy[s] = { count: 0, invested: 0 };
    openByStrategy[s].count++;
    openByStrategy[s].invested += e.detail.invested || 0;
  }

  // Scan stats
  let totalMarketsScanned = 0, totalExecuted = 0, totalExited = 0;
  for (const e of scans) {
    totalMarketsScanned += e.detail.markets_scanned || 0;
    totalExecuted += e.detail.executed || 0;
    totalExited += e.detail.closed || 0;
  }

  const totalTrades = wins + losses;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : "0.0";
  const avgWin = wins > 0 ? (closedTrades.filter(e => (e.detail?.pnl || 0) >= 0).reduce((s, e) => s + e.detail.pnl, 0) / wins) : 0;
  const avgLoss = losses > 0 ? (closedTrades.filter(e => (e.detail?.pnl || 0) < 0).reduce((s, e) => s + e.detail.pnl, 0) / losses) : 0;
  const profitFactor = avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : avgWin > 0 ? Infinity : 0;
  const roi = totalInvested > 0 ? ((totalPnl / totalInvested) * 100).toFixed(1) : "0.0";

  const pnlColor = totalPnl >= 0 ? "text-green" : "text-red";
  const pnlPrefix = totalPnl >= 0 ? "+" : "";

  return (
    <div className="p-4 space-y-4">
      {/* Performance overview */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatBox label="REALIZED P&L" value={`${pnlPrefix}$${totalPnl.toFixed(2)}`} color={pnlColor} sub={`ROI: ${roi}%`} />
        <StatBox label="WIN RATE" value={`${winRate}%`} color={parseFloat(winRate) >= 50 ? "text-green" : "text-red"} sub={`${wins}W / ${losses}L of ${totalTrades}`} />
        <StatBox label="PROFIT FACTOR" value={profitFactor === Infinity ? "∞" : profitFactor.toFixed(2)} color={profitFactor >= 1 ? "text-green" : "text-red"} sub={`Avg win $${avgWin.toFixed(2)} / loss $${avgLoss.toFixed(2)}`} />
        <StatBox label="OPEN POSITIONS" value={openTrades.length} sub={`$${openInvested.toFixed(2)} deployed`} />
      </div>

      {/* Best / Worst */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatBox label="BEST TRADE" value={bestTrade > -Infinity ? `+$${bestTrade.toFixed(2)}` : "—"} color="text-green" />
        <StatBox label="WORST TRADE" value={worstTrade < Infinity ? `-$${Math.abs(worstTrade).toFixed(2)}` : "—"} color="text-red" />
        <StatBox label="TOTAL INVESTED" value={`$${totalInvested.toFixed(2)}`} sub={`Closed trades`} />
        <StatBox label="TOTAL RETURNED" value={`$${totalReturned.toFixed(2)}`} sub={`Capital + P&L`} />
      </div>

      {/* Strategy breakdown */}
      <div>
        <div className="text-[9px] text-text-2 font-mono tracking-widest mb-2 uppercase">Performance by Strategy</div>
        <div className="overflow-x-auto">
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-text-2 text-left border-b border-border bg-bg-2/30">
                <th className="px-3 py-1.5 font-medium">STRATEGY</th>
                <th className="px-3 py-1.5 font-medium text-right">TRADES</th>
                <th className="px-3 py-1.5 font-medium text-right">W/L</th>
                <th className="px-3 py-1.5 font-medium text-right">WIN %</th>
                <th className="px-3 py-1.5 font-medium text-right">INVESTED</th>
                <th className="px-3 py-1.5 font-medium text-right">P&L</th>
                <th className="px-3 py-1.5 font-medium text-right">ROI</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(pnlByStrategy).map(([strategy, s]) => {
                const sRoi = s.invested > 0 ? ((s.pnl / s.invested) * 100) : 0;
                const sWinRate = s.trades > 0 ? ((s.wins / s.trades) * 100) : 0;
                return (
                  <tr key={strategy} className="border-b border-border hover:bg-bg-2/40">
                    <td className="px-3 py-2"><StrategyBadge strategy={strategy} /></td>
                    <td className="px-3 py-2 text-right tabular-nums text-text-0">{s.trades}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <span className="text-green">{s.wins}</span>/<span className="text-red">{s.losses}</span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums" style={{ color: sWinRate >= 50 ? "#4ade80" : "#f87171" }}>
                      {sWinRate.toFixed(0)}%
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-text-2">${s.invested.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      <PnlCell pnl={s.pnl} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums" style={{ color: sRoi >= 0 ? "#4ade80" : "#f87171" }}>
                      {sRoi >= 0 ? "+" : ""}{sRoi.toFixed(1)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Open positions by strategy */}
      {openTrades.length > 0 && (
        <div>
          <div className="text-[9px] text-text-2 font-mono tracking-widest mb-2 uppercase">Open Positions by Strategy</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {Object.entries(openByStrategy).map(([strategy, s]) => (
              <div key={strategy} className="bg-bg-2/40 rounded-lg p-3 flex items-center gap-3">
                <StrategyBadge strategy={strategy} />
                <div className="text-[10px] font-mono">
                  <span className="text-text-0">{s.count}</span>
                  <span className="text-text-2"> pos — </span>
                  <span className="text-text-0">${s.invested.toFixed(2)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Scan activity */}
      <div>
        <div className="text-[9px] text-text-2 font-mono tracking-widest mb-2 uppercase">Automation Activity</div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <StatBox label="CRON RUNS" value={scans.length} sub={scans.length > 0 ? `Last: ${formatTime(scans[0]?.timestamp)}` : "Never"} />
          <StatBox label="MARKETS SCANNED" value={totalMarketsScanned.toLocaleString()} sub={scans.length > 0 ? `Avg ${Math.round(totalMarketsScanned / scans.length)}/run` : ""} />
          <StatBox label="AUTO EXECUTED" value={totalExecuted} color="text-green" />
          <StatBox label="AUTO EXITED" value={totalExited} color="text-red" />
        </div>
      </div>
    </div>
  );
}

// ─── ALL VIEW (timeline) ────────────────────────────────────
function AllView({ events, expanded, toggle }) {
  return (
    <div className="max-h-[500px] overflow-y-auto">
      {events.length === 0 ? (
        <div className="px-4 py-8 text-center text-[10px] text-text-2">No events yet.</div>
      ) : (
        events.map((ev, i) => {
          const cfg = EVENT_CONFIG[ev.type] || { icon: "?", color: "#888", label: ev.type };
          const key = eventKey(ev, i);
          const isOpen = expanded.has(key);

          return (
            <div
              key={key}
              className="border-b border-border hover:bg-bg-2/40 transition-colors cursor-pointer"
              onClick={() => toggle(key)}
            >
              <div className="flex items-start gap-3 px-4 py-2">
                <span
                  className="w-5 h-5 flex items-center justify-center rounded-full text-[9px] font-bold mt-0.5 shrink-0"
                  style={{ color: cfg.color, background: cfg.color + "20", border: `1px solid ${cfg.color}30` }}
                >
                  {cfg.icon}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[8px] font-mono font-bold tracking-wider" style={{ color: cfg.color }}>
                      {cfg.label}
                    </span>
                    <span className="text-[9px] text-text-2 font-mono tabular-nums" title={formatFullDate(ev.timestamp)}>
                      {formatTime(ev.timestamp)}
                    </span>
                    {(ev.type === "trade_opened" || ev.type === "trade_closed") && (
                      <span className="text-[10px] text-text-0 font-mono truncate">
                        {ev.detail.question || ev.detail.slug}
                      </span>
                    )}
                    {ev.type === "scan" && (
                      <span className="text-[10px] text-text-2 font-mono">
                        {ev.detail.markets_scanned} mkts
                        {ev.detail.executed > 0 && <span className="text-green"> +{ev.detail.executed}</span>}
                        {ev.detail.closed > 0 && <span className="text-red"> -{ev.detail.closed}</span>}
                      </span>
                    )}
                    {(ev.type === "trade_open" || ev.type === "trade_close") && (
                      <span className="text-[10px] text-text-2 font-mono">
                        {ev.detail.slug || `#${ev.detail.id}`}
                        {ev.detail.pnl != null && (
                          <span className={ev.detail.pnl >= 0 ? "text-green" : "text-red"}>
                            {" "}{ev.detail.pnl >= 0 ? "+" : ""}${ev.detail.pnl.toFixed(2)}
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                  {isOpen && (
                    <div className="mt-1.5 p-2 bg-bg-3/50 rounded text-[9px] font-mono text-text-2 whitespace-pre-wrap break-all">
                      <div className="mb-1">{formatFullDate(ev.timestamp)}</div>
                      {JSON.stringify(ev.detail, null, 2)}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

function eventKey(ev, i) {
  if (ev.detail?.id != null) return `${ev.type}-${ev.detail.id}`;
  if (ev.detail?.slug) return `${ev.type}-${ev.detail.slug}-${ev.timestamp}`;
  return `${ev.type}-${i}-${ev.timestamp}`;
}

export default function HistoryPanel({ history }) {
  const [filter, setFilter] = useState("ALL");
  const [expanded, setExpanded] = useState(new Set());

  const toggle = (key) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const stats = useMemo(() => {
    if (!history?.events) return null;
    let scans = 0, opens = 0, closes = 0, totalPnl = 0;
    for (const e of history.events) {
      if (e.type === "scan") scans++;
      else if (e.type === "trade_opened" || e.type === "trade_open") opens++;
      else if (e.type === "trade_closed" || e.type === "trade_close") {
        closes++;
        if (e.type === "trade_closed" && e.detail?.pnl != null) totalPnl += e.detail.pnl;
      }
    }
    return { scans, opens, closes, totalPnl, total: history.events.length };
  }, [history]);

  if (!history || !history.events) {
    return (
      <div className="bg-bg-1 border border-border rounded-lg p-8 text-center">
        <div className="text-text-2 text-sm">Loading history...</div>
      </div>
    );
  }

  const allEvents = history.events;

  const tabs = [
    { key: "ALL", label: "ALL", count: stats?.total || 0, color: "text-gold" },
    { key: "SCANS", label: "SCANS", count: stats?.scans || 0, color: "text-purple-400" },
    { key: "OPENS", label: "OPENS", count: stats?.opens || 0, color: "text-green" },
    { key: "CLOSES", label: "CLOSES", count: stats?.closes || 0, color: "text-red" },
    { key: "STATS", label: "STATS", count: "—", color: "text-gold" },
  ];

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-gold">~</span> FULL HISTORY
          {stats && (
            <span className={`tabular-nums ${stats.totalPnl >= 0 ? "text-green" : "text-red"}`}>
              {stats.totalPnl >= 0 ? "+" : ""}${stats.totalPnl.toFixed(2)} realized
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-5 gap-px bg-border text-center text-[9px] font-mono select-none">
        {tabs.map(({ key, label, count, color }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`py-2.5 transition-all ${
              filter === key
                ? "bg-bg-2 border-b-2 border-gold"
                : "bg-bg-1 hover:bg-bg-2/50 border-b-2 border-transparent"
            }`}
          >
            <div className={`font-bold tracking-wider ${filter === key ? color : "text-text-2"}`}>
              {label}
            </div>
            <div className={`text-[12px] tabular-nums mt-0.5 ${filter === key ? "text-text-0" : "text-text-2"}`}>
              {count}
            </div>
          </button>
        ))}
      </div>

      {filter === "ALL" && <AllView events={allEvents} expanded={expanded} toggle={toggle} />}
      {filter === "SCANS" && <ScansView events={allEvents} />}
      {filter === "OPENS" && <OpensView events={allEvents} />}
      {filter === "CLOSES" && <ClosesView events={allEvents} />}
      {filter === "STATS" && <StatsView events={allEvents} />}
    </div>
  );
}
