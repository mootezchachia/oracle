export default function Ticker({ markets = [], reddit = [] }) {
  const redditItems = reddit.slice(0, 5).map((r, i) => (
    <span key={`r-${i}`} className="flex items-center gap-2 whitespace-nowrap group">
      <span className="inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold rounded bg-red/15 text-red border border-red/20">
        ▲{r.velocity ?? 0}/hr
      </span>
      <span className="text-text-1 group-hover:text-text-0 transition-colors">{r.title}</span>
    </span>
  ));

  const marketItems = markets.slice(0, 3).map((m, i) => (
    <span key={`m-${i}`} className="flex items-center gap-2 whitespace-nowrap group">
      <span className="inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold rounded bg-blue/15 text-blue border border-blue/20">
        {m.price ?? 0}%
      </span>
      <span className="text-text-1 group-hover:text-text-0 transition-colors">{m.question ?? m.title}</span>
    </span>
  ));

  const items = interleave(redditItems, marketItems);

  if (items.length === 0) return null;

  return (
    <div className="relative w-full h-8 overflow-hidden bg-bg-1/80 border-b border-border font-mono text-[11px] flex items-center select-none">
      {/* Label */}
      <div className="flex-shrink-0 z-10 px-2.5 py-0.5 bg-gradient-to-r from-red to-red/80 text-white text-[9px] font-bold uppercase tracking-wider flex items-center gap-1">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inset-0 rounded-full bg-white animate-ping opacity-50" />
          <span className="relative h-1.5 w-1.5 rounded-full bg-white" />
        </span>
        LIVE
      </div>

      {/* Scrolling track */}
      <div className="flex items-center gap-8 animate-[ticker-scroll_60s_linear_infinite] whitespace-nowrap pl-4">
        {items}
        <span className="w-16 flex-shrink-0" />
        {items}
      </div>
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
