export default function MarketsTable({ markets }) {
  const formatVolume = (v) => {
    if (!v && v !== 0) return '$0';
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
    return `$${v}`;
  };

  const getPrices = (market) => {
    const outcomes = market.outcomes || [];
    const yes = outcomes.find((o) => o.name === 'Yes') || outcomes[0];
    const no = outcomes.find((o) => o.name === 'No') || outcomes[1];
    return {
      yes: yes?.price ?? 0,
      no: no?.price ?? 0,
    };
  };

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-blue">◆</span> POLYMARKET — SENTIMENT MARKETS
          <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">{markets?.length || 0}</span>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto stagger-children">
        {(markets || []).map((market, i) => {
          const { yes, no } = getPrices(market);
          return (
            <a
              key={market.id || i}
              href={market.url}
              target="_blank"
              rel="noopener noreferrer"
              className="grid grid-cols-[1fr_auto_80px] sm:grid-cols-[1fr_160px_100px] items-center px-4 py-2.5 border-b border-border hover:bg-bg-2/70 cursor-pointer no-underline transition-colors group"
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-[11px] font-mono text-text-0 truncate group-hover:text-text-0">{market.question}</span>
                <span className="text-text-2 text-[10px] shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">↗</span>
              </div>
              <div className="flex items-center gap-2 justify-center">
                <span className="bg-green/15 text-green text-[10px] font-mono font-semibold px-2 py-0.5 rounded border border-green/20 tabular-nums">
                  YES {Math.round(yes)}¢
                </span>
                <span className="bg-red/15 text-red text-[10px] font-mono font-semibold px-2 py-0.5 rounded border border-red/20 tabular-nums hidden sm:inline">
                  NO {Math.round(no)}¢
                </span>
              </div>
              <div className="text-[10px] font-mono text-text-2 text-right tabular-nums">
                {formatVolume(market.volume)}
              </div>
            </a>
          );
        })}
      </div>
    </div>
  );
}
