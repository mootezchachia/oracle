const SOURCES = ["MARKETS", "REDDIT", "NEWS", "FRED"];

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
    <footer className="fixed bottom-0 left-0 right-0 z-50 flex items-center justify-between h-7 px-4 bg-bg-2 border-t border-border font-mono text-[10px] text-text-2 uppercase tracking-wider select-none">
      {/* Left — source status dots */}
      <div className="flex items-center gap-4">
        {SOURCES.map((src) => {
          const key = src.toLowerCase();
          const count = counts[key] ?? 0;
          const ok = count > 0;
          return (
            <div key={src} className="flex items-center gap-1.5">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  ok ? "bg-green" : "bg-orange"
                }`}
              />
              <span>{src}</span>
            </div>
          );
        })}
      </div>

      {/* Right — stats */}
      <div className="flex items-center gap-4">
        <span>SCANS: {scans}</span>
        <span>LAST: {lastScan}</span>
        <span className={errors > 0 ? "text-orange" : ""}>
          ERRORS: {errors}
        </span>
      </div>
    </footer>
  );
}
