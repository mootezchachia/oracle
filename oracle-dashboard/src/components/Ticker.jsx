export default function Ticker({ markets = [], reddit = [] }) {
  const redditItems = reddit.slice(0, 5).map((r, i) => (
    <span key={`r-${i}`} className="flex items-center gap-2 whitespace-nowrap">
      <span className="inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold rounded bg-red-dim text-red">
        ▲{r.velocity ?? 0}/hr
      </span>
      <span className="text-text-1">{r.title}</span>
    </span>
  ));

  const marketItems = markets.slice(0, 3).map((m, i) => (
    <span key={`m-${i}`} className="flex items-center gap-2 whitespace-nowrap">
      <span className="inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold rounded bg-blue/15 text-blue">
        {m.price ?? 0}%
      </span>
      <span className="text-text-1">{m.question ?? m.title}</span>
    </span>
  ));

  const items = interleave(redditItems, marketItems);

  return (
    <div className="relative w-full h-8 overflow-hidden bg-bg-1 border-b border-border font-mono text-[11px] flex items-center select-none">
      {/* Label */}
      <div className="flex-shrink-0 z-10 px-2 py-0.5 bg-[#f85149] text-black text-[9px] font-bold uppercase tracking-wider">
        SIGNALS
      </div>

      {/* Scrolling track */}
      <div className="flex items-center gap-8 animate-[ticker-scroll_60s_linear_infinite] whitespace-nowrap pl-4">
        {items}
        {/* Duplicate for seamless loop */}
        <span className="w-16 flex-shrink-0" />
        {items}
      </div>

      <style>{`
        @keyframes ticker-scroll {
          0%   { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
}

function interleave(a, b) {
  const result = [];
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    if (i < a.length) result.push(a[i]);
    if (i < b.length) result.push(b[i]);
  }
  return result;
}
