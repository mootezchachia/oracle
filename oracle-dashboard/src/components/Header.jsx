import { useState, useEffect } from "react";

const NAV_TABS = ["DASHBOARD", "MARKETS", "SIGNALS", "PREDICTIONS"];

export default function Header({ status, onScan }) {
  const [utc, setUtc] = useState(formatUTC());

  useEffect(() => {
    const id = setInterval(() => setUtc(formatUTC()), 1000);
    return () => clearInterval(id);
  }, []);

  const isLive = status !== null;

  return (
    <header className="sticky top-0 z-50 flex items-center justify-between h-[52px] px-4 bg-gradient-to-r from-bg-2 to-bg-1 border-b border-border font-mono uppercase tracking-widest select-none">
      {/* Left — Logo */}
      <div className="flex items-center gap-3">
        <span className="relative flex h-7 w-7 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-gold/30 animate-pulse" />
          <span className="relative h-5 w-5 rounded-full bg-gold" />
        </span>
        <div className="flex flex-col leading-none">
          <span className="text-gold text-sm font-bold tracking-[0.25em]">ORACLE</span>
          <span className="text-text-2 text-[9px] tracking-[0.15em]">NARRATIVE ARBITRAGE ENGINE</span>
        </div>
      </div>

      {/* Center — Nav */}
      <nav className="hidden md:flex items-center gap-6 text-[11px] tracking-[0.2em]">
        {NAV_TABS.map((tab) => (
          <button
            key={tab}
            className={
              tab === "DASHBOARD"
                ? "text-gold"
                : "text-text-2 hover:text-text-1 transition-colors"
            }
          >
            {tab}
          </button>
        ))}
      </nav>

      {/* Right — Status / Clock / Scan */}
      <div className="flex items-center gap-4 text-[11px]">
        {/* Live badge */}
        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${
              isLive ? "bg-green animate-pulse" : "bg-red"
            }`}
          />
          <span className={isLive ? "text-green" : "text-red"}>
            {isLive ? "LIVE" : "OFFLINE"}
          </span>
        </div>

        {/* UTC clock */}
        <span className="text-text-2 tabular-nums">{utc}</span>

        {/* Scan button */}
        <button
          onClick={onScan}
          className="px-3 py-1 border border-border bg-bg-3 text-text-1 text-[10px] tracking-[0.15em] hover:border-gold hover:text-gold transition-colors"
        >
          SCAN NOW
        </button>
      </div>
    </header>
  );
}

function formatUTC() {
  const d = new Date();
  return d.toISOString().slice(11, 19) + " UTC";
}
