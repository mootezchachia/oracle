import { useState, useEffect } from "react";

const NAV_TABS = [
  { key: "DASHBOARD", icon: "◈" },
  { key: "MARKETS", icon: "◇" },
  { key: "SIGNALS", icon: "△" },
  { key: "PREDICTIONS", icon: "○" },
  { key: "STRATEGY", icon: "⬡" },
];

export default function Header({ status, onScan, activeTab, onTabChange }) {
  const [utc, setUtc] = useState(formatUTC());
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setUtc(formatUTC()), 1000);
    return () => clearInterval(id);
  }, []);

  const handleScan = () => {
    setScanning(true);
    onScan();
    setTimeout(() => setScanning(false), 2000);
  };

  const isLive = status !== null;

  return (
    <header className="sticky top-0 z-50 flex items-center justify-between h-[52px] px-4 bg-bg-2/95 glass border-b border-border font-mono uppercase tracking-widest select-none">
      {/* Left — Logo */}
      <div className="flex items-center gap-3">
        <span className="relative flex h-7 w-7 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-gold/20 animate-pulse" />
          <span className="absolute inset-1 rounded-full bg-gold/10 animate-pulse" style={{ animationDelay: "0.5s" }} />
          <span className="relative h-4 w-4 rounded-full bg-gold animate-logo" />
        </span>
        <div className="flex flex-col leading-none">
          <span className="text-gold text-sm font-bold tracking-[0.25em]">ORACLE</span>
          <span className="text-text-2 text-[8px] tracking-[0.12em] hidden sm:block">NARRATIVE ARBITRAGE ENGINE</span>
        </div>
      </div>

      {/* Center — Nav */}
      <nav className="hidden md:flex items-center gap-1 text-[11px] tracking-[0.2em]">
        {NAV_TABS.map(({ key, icon }) => (
          <button
            key={key}
            onClick={() => onTabChange(key)}
            className={`relative px-3 py-1.5 rounded transition-all duration-200 ${
              activeTab === key
                ? "text-gold tab-active"
                : "text-text-2 hover:text-text-1 hover:bg-bg-3/50"
            }`}
          >
            <span className="mr-1.5 text-[9px]">{icon}</span>
            {key}
          </button>
        ))}
      </nav>

      {/* Mobile Nav */}
      <div className="flex md:hidden items-center gap-1">
        {NAV_TABS.map(({ key, icon }) => (
          <button
            key={key}
            onClick={() => onTabChange(key)}
            className={`px-2 py-1 rounded text-[9px] transition-all ${
              activeTab === key
                ? "text-gold bg-gold/10"
                : "text-text-2"
            }`}
            title={key}
          >
            {icon}
          </button>
        ))}
      </div>

      {/* Right — Status / Clock / Scan */}
      <div className="flex items-center gap-3 text-[11px]">
        {/* Live badge */}
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            {isLive && <span className="absolute inset-0 rounded-full bg-green animate-ping opacity-40" />}
            <span className={`relative h-2 w-2 rounded-full ${isLive ? "bg-green" : "bg-red"}`} />
          </span>
          <span className={`hidden sm:inline ${isLive ? "text-green" : "text-red"}`}>
            {isLive ? "LIVE" : "OFFLINE"}
          </span>
        </div>

        {/* UTC clock */}
        <span className="text-text-2 tabular-nums hidden sm:inline">{utc}</span>

        {/* Refresh button */}
        <button
          onClick={handleScan}
          disabled={scanning}
          className={`px-3 py-1 border rounded text-[10px] tracking-[0.15em] transition-all duration-200 ${
            scanning
              ? "border-gold/50 text-gold bg-gold/10 cursor-wait"
              : "border-border bg-bg-3 text-text-1 hover:border-gold hover:text-gold hover:bg-gold/5 active:scale-95"
          }`}
        >
          {scanning ? "SCANNING..." : "REFRESH"}
        </button>
      </div>
    </header>
  );
}

function formatUTC() {
  const d = new Date();
  return d.toISOString().slice(11, 19) + " UTC";
}
