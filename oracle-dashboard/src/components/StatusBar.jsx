const SOURCES = ["MARKETS", "REDDIT", "NEWS", "FRED", "SIGNALS", "CRYPTO"];

export default function StatusBar({ status }) {
  const counts = status?.counts ?? {};
  const scans = status?.scans ?? 0;
  const errors = Array.isArray(status?.errors) ? status.errors.length : (status?.errors ?? 0);
  const lastScan = status?.last_scan
    ? new Date(status.last_scan).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
    : "--:--:--";

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-50 flex items-center justify-between h-7 px-4 bg-bg-2/95 glass border-t border-border font-mono text-[10px] text-text-2 uppercase tracking-wider select-none">
      {/* Left — source status dots */}
      <div className="flex items-center gap-3 sm:gap-4 overflow-x-auto">
        {SOURCES.map((src) => {
          const key = src.toLowerCase();
          const count = counts[key] ?? 0;
          const ok = count > 0;
          return (
            <div key={src} className="flex items-center gap-1.5 shrink-0" title={`${src}: ${count} items`}>
              <span className="relative flex h-1.5 w-1.5">
                {ok && <span className="absolute inset-0 rounded-full bg-green animate-pulse opacity-50" />}
                <span className={`relative h-1.5 w-1.5 rounded-full ${ok ? "bg-green" : "bg-orange"}`} />
              </span>
              <span className="hidden sm:inline">{src}</span>
              <span className="sm:hidden">{src.slice(0, 3)}</span>
            </div>
          );
        })}
      </div>

      {/* Right — stats */}
      <div className="flex items-center gap-3 sm:gap-4 shrink-0">
        <span className="hidden sm:inline">SCANS: <span className="text-text-1 tabular-nums">{scans}</span></span>
        <span>LAST: <span className="text-text-1 tabular-nums">{lastScan}</span></span>
        <span className={errors > 0 ? "text-orange" : ""}>
          ERR: <span className="tabular-nums">{errors}</span>
        </span>
      </div>
    </footer>
  );
}
