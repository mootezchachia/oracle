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

const FILTERS = ["ALL", "TRADES", "SCANS", "EXITS"];

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

function strategyColor(strategy) {
  return STRATEGY_COLORS[strategy] || "#888";
}

function StrategyBadge({ strategy }) {
  const color = strategyColor(strategy);
  return (
    <span
      className="text-[8px] px-1.5 py-0.5 rounded"
      style={{ color, background: color + "20" }}
    >
      {STRATEGY_LABELS[strategy] || strategy?.toUpperCase()}
    </span>
  );
}

function ScanEvent({ detail }) {
  return (
    <div className="text-[10px] font-mono text-text-2 space-y-0.5">
      <div>
        Scanned <span className="text-text-0">{detail.markets_scanned}</span> markets
        {" — "}
        <span className="text-green">{detail.bonds_found}</span> bonds,{" "}
        <span className="text-blue-400">{detail.expertise_found}</span> expertise,{" "}
        <span className="text-orange">{detail.crashes_found}</span> crashes
      </div>
      {detail.executed > 0 && (
        <div className="text-green">
          Executed {detail.executed} trade{detail.executed > 1 ? "s" : ""}
        </div>
      )}
      {detail.closed > 0 && (
        <div className="text-red">
          Closed {detail.closed} position{detail.closed > 1 ? "s" : ""}
        </div>
      )}
    </div>
  );
}

function TradeEvent({ detail }) {
  const pnlColor = (detail.pnl || 0) >= 0 ? "text-green" : "text-red";
  const pnlPrefix = (detail.pnl || 0) >= 0 ? "+" : "";

  return (
    <div className="text-[10px] font-mono space-y-0.5">
      <div className="text-text-0 truncate">{detail.question || detail.slug}</div>
      <div className="flex items-center gap-3 text-text-2">
        {detail.portfolio && (
          <span className="text-[8px] bg-bg-3 px-1.5 py-0.5 rounded">{detail.portfolio}</span>
        )}
        {detail.strategy && <StrategyBadge strategy={detail.strategy} />}
        <span>
          {detail.side?.toUpperCase()} @ {detail.entry_price ? `${(detail.entry_price * 100).toFixed(0)}c` : "—"}
        </span>
        <span>${detail.invested?.toFixed(2)}</span>
        {detail.status === "closed" && detail.pnl != null && (
          <span className={`font-semibold ${pnlColor}`}>
            {pnlPrefix}${detail.pnl.toFixed(2)} ({pnlPrefix}{detail.pnl_pct?.toFixed(1)}%)
          </span>
        )}
      </div>
      {detail.close_reason && (
        <div className="text-[9px] text-text-2 italic">{detail.close_reason}</div>
      )}
    </div>
  );
}

function PriceEvent({ detail }) {
  const change =
    detail.first_price && detail.latest_price
      ? ((detail.latest_price - detail.first_price) / detail.first_price) * 100
      : null;
  const changeColor = change ? (change >= 0 ? "text-green" : "text-red") : "text-text-2";

  return (
    <div className="text-[10px] font-mono space-y-0.5">
      <div className="text-text-0 truncate">{detail.slug}</div>
      <div className="flex items-center gap-3 text-text-2">
        <span>{detail.data_points} snapshots</span>
        <span>
          {detail.first_price ? `${(detail.first_price * 100).toFixed(0)}c` : "—"} →{" "}
          {detail.latest_price ? `${(detail.latest_price * 100).toFixed(0)}c` : "—"}
        </span>
        {change != null && (
          <span className={changeColor}>
            {change >= 0 ? "+" : ""}
            {change.toFixed(1)}%
          </span>
        )}
      </div>
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

  const events = useMemo(() => {
    if (!history?.events) return [];
    return history.events.filter((ev) => {
      if (filter === "ALL") return true;
      if (filter === "TRADES")
        return ev.type === "trade_opened" || ev.type === "trade_open";
      if (filter === "SCANS") return ev.type === "scan";
      if (filter === "EXITS")
        return ev.type === "trade_closed" || ev.type === "trade_close";
      return true;
    });
  }, [history, filter]);

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

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-gold">~</span> FULL HISTORY
          <span className="bg-gold/10 text-gold text-[9px] px-2 py-0.5 rounded-full border border-gold/20">
            {history.total} EVENTS
          </span>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-border text-center text-[9px] font-mono">
          <div className="bg-bg-1 py-2 hover:bg-bg-2/30 transition-colors">
            <span className="text-text-2">EVENTS </span>
            <span className="text-text-0 tabular-nums">{stats.total}</span>
          </div>
          <div className="bg-bg-1 py-2 hover:bg-bg-2/30 transition-colors">
            <span className="text-text-2">SCANS </span>
            <span className="text-purple-400 tabular-nums">{stats.scans}</span>
          </div>
          <div className="bg-bg-1 py-2 hover:bg-bg-2/30 transition-colors">
            <span className="text-text-2">OPENS </span>
            <span className="text-green tabular-nums">{stats.opens}</span>
          </div>
          <div className="bg-bg-1 py-2 hover:bg-bg-2/30 transition-colors">
            <span className="text-text-2">CLOSES </span>
            <span className="text-red tabular-nums">{stats.closes}</span>
          </div>
          <div className="bg-bg-1 py-2 hover:bg-bg-2/30 transition-colors">
            <span className="text-text-2">REALIZED </span>
            <span className={`tabular-nums ${stats.totalPnl >= 0 ? "text-green" : "text-red"}`}>
              {stats.totalPnl >= 0 ? "+" : ""}${stats.totalPnl.toFixed(2)}
            </span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-1 px-4 py-2 border-b border-border bg-bg-2/50">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded text-[9px] font-mono tracking-wider transition-all ${
              filter === f
                ? "bg-gold/15 text-gold border border-gold/30"
                : "text-text-2 hover:text-text-1 hover:bg-bg-3/50 border border-transparent"
            }`}
          >
            {f}
          </button>
        ))}
        <span className="ml-auto text-[9px] text-text-2 font-mono tabular-nums">
          {events.length} shown
        </span>
      </div>

      <div className="max-h-[600px] overflow-y-auto">
        {events.length === 0 ? (
          <div className="px-4 py-8 text-center text-[10px] text-text-2">
            No events match this filter.
          </div>
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
                <div className="flex items-start gap-3 px-4 py-2.5">
                  <div className="flex flex-col items-center mt-0.5">
                    <span
                      className="w-6 h-6 flex items-center justify-center rounded-full text-[10px] font-bold"
                      style={{
                        color: cfg.color,
                        background: cfg.color + "20",
                        border: `1px solid ${cfg.color}30`,
                      }}
                    >
                      {cfg.icon}
                    </span>
                    {i < events.length - 1 && (
                      <div
                        className="w-px flex-1 mt-1 min-h-[8px]"
                        style={{ background: cfg.color + "20" }}
                      />
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="text-[8px] font-mono font-bold tracking-wider"
                        style={{ color: cfg.color }}
                      >
                        {cfg.label}
                      </span>
                      <span
                        className="text-[9px] text-text-2 font-mono tabular-nums"
                        title={formatFullDate(ev.timestamp)}
                      >
                        {formatTime(ev.timestamp)}
                      </span>
                    </div>

                    {ev.type === "scan" && <ScanEvent detail={ev.detail} />}
                    {(ev.type === "trade_opened" || ev.type === "trade_closed") && (
                      <TradeEvent detail={ev.detail} />
                    )}
                    {(ev.type === "trade_open" || ev.type === "trade_close") && (
                      <div className="text-[10px] font-mono text-text-2">
                        {ev.detail.strategy && (
                          <span className="text-text-0">[{ev.detail.strategy}] </span>
                        )}
                        {ev.detail.slug || `Trade #${ev.detail.id}`}
                        {ev.detail.pnl != null && (
                          <span className={ev.detail.pnl >= 0 ? "text-green" : "text-red"}>
                            {" "}{ev.detail.pnl >= 0 ? "+" : ""}${ev.detail.pnl.toFixed(2)}
                          </span>
                        )}
                        {ev.detail.reason && (
                          <span className="italic"> — {ev.detail.reason}</span>
                        )}
                      </div>
                    )}
                    {ev.type === "price_tracking" && <PriceEvent detail={ev.detail} />}

                    {isOpen && (
                      <div className="mt-2 p-2 bg-bg-3/50 rounded text-[9px] font-mono text-text-2 whitespace-pre-wrap break-all">
                        <div className="text-text-2 mb-1">{formatFullDate(ev.timestamp)}</div>
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
    </div>
  );
}
