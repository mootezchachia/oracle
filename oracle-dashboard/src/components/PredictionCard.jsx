import PriceChart from "./PriceChart";

export default function PredictionCard({ prediction, priceHistory }) {
  const p = prediction || {};
  const ph = priceHistory || {};

  const oracleYes = typeof p.our_odds === "number"
    ? p.our_odds
    : Array.isArray(p.our_odds) && p.our_odds.length > 0
      ? parseInt(String(p.our_odds[0]).match(/(\d+)/)?.[1] || "50", 10)
      : 50;

  const oracleNo = 100 - oracleYes;

  let marketYes = 50;
  if (typeof p.market_yes === "number") {
    marketYes = p.market_yes;
  } else if (p.current_price) {
    const m = String(p.current_price).match(/(\d+)/);
    marketYes = m ? parseInt(m[1], 10) : 50;
  }
  const marketNo = 100 - marketYes;

  let direction = "YES";
  let confidence = 50;
  if (p.direction) {
    direction = p.direction.replace(/^(BUY|SELL)\s*/i, "").toUpperCase();
  } else if (p.primary_call) {
    const dirMatch = String(p.primary_call).match(/BUY\s+(YES|NO)/i);
    if (dirMatch) direction = dirMatch[1].toUpperCase();
  }
  if (typeof p.confidence === "number") {
    confidence = p.confidence;
  } else if (p.primary_call) {
    const confMatch = String(p.primary_call).match(/Confidence:\s*(\d+)/i);
    if (confMatch) confidence = parseInt(confMatch[1], 10);
  }

  const isYes = direction === "YES";
  const edge = typeof p.edge === "number" ? p.edge : Math.abs(oracleYes - marketYes);

  let targetNum = oracleYes;
  if (p.target_price) {
    const targetMatch = String(p.target_price).match(/(\d+)/);
    if (targetMatch) targetNum = parseInt(targetMatch[1], 10);
  }

  const dirColor = isYes ? "green" : "red";

  return (
    <div className="bg-bg-1 border border-border rounded-xl overflow-hidden font-mono card-hover animate-slide-up">
      {/* Row 1 — Action Banner */}
      <div className="flex items-center justify-between px-5 py-4 bg-gradient-to-r from-bg-0 to-bg-1 border-b border-border">
        <div className="flex items-center gap-3">
          <span
            className={`px-4 py-1.5 rounded-lg font-bold text-sm tracking-wide uppercase shadow-lg ${
              isYes
                ? "bg-green text-bg-0 shadow-green/20"
                : "bg-red text-bg-0 shadow-red/20"
            }`}
          >
            BUY {direction}
          </span>
          <div className="flex flex-col">
            <span className="text-text-1 text-sm font-semibold">
              {confidence}%
            </span>
            <span className="text-text-2 text-[9px] uppercase tracking-wider">confidence</span>
          </div>
        </div>

        <div className="text-center flex-1 px-4 hidden sm:block">
          <a
            href={p.url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="text-text-0 hover:text-gold transition-colors text-sm font-semibold group"
          >
            <span className="text-text-2 mr-2">#{p.number}</span>
            {p.market || "Unknown Market"}
            <span className="text-text-2 ml-1 text-xs group-hover:text-gold transition-colors">↗</span>
          </a>
        </div>

        <div className="text-right">
          <span className="text-gold text-2xl font-bold">+{edge}%</span>
          <span className="text-text-2 text-[9px] block uppercase tracking-widest">edge</span>
        </div>
      </div>

      {/* Mobile market title */}
      <div className="sm:hidden px-5 py-2 border-b border-border">
        <a
          href={p.url || "#"}
          target="_blank"
          rel="noopener noreferrer"
          className="text-text-0 hover:text-gold transition-colors text-xs font-semibold"
        >
          <span className="text-text-2 mr-1">#{p.number}</span>
          {p.market || "Unknown Market"} ↗
        </a>
      </div>

      {/* Row 2 — Three columns */}
      <div className="grid grid-cols-1 sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-border border-b border-border">
        {/* Col 1: Polymarket Now */}
        <div className="p-5">
          <div className="text-text-2 text-[10px] tracking-widest uppercase mb-3 flex items-center gap-2">
            <span className="h-1 w-1 rounded-full bg-blue" />
            Polymarket Now
          </div>
          <div className="space-y-2">
            <div className="flex items-baseline justify-between">
              <span className="text-text-2 text-sm">Yes</span>
              <span className="text-green text-2xl font-bold tabular-nums">{marketYes}¢</span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-text-2 text-sm">No</span>
              <span className="text-red text-lg font-semibold tabular-nums">{marketNo}¢</span>
            </div>
            <div className="w-full h-2.5 bg-bg-3 rounded-full mt-3 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-green to-green/70 rounded-full transition-all duration-700 ease-out"
                style={{ width: `${marketYes}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] text-text-2 mt-1">
              <span>YES {marketYes}%</span>
              <span>NO {marketNo}%</span>
            </div>
          </div>
        </div>

        {/* Col 2: Oracle Says */}
        <div className="p-5">
          <div className="text-text-2 text-[10px] tracking-widest uppercase mb-3 flex items-center gap-2">
            <span className="h-1 w-1 rounded-full bg-gold" />
            Oracle Says
          </div>
          <div className="text-gold text-3xl font-bold mb-2 tabular-nums">{oracleYes}%</div>
          <div className="text-text-2 text-xs leading-relaxed line-clamp-3">
            {p.narrative || p.primary_call || "No narrative available."}
          </div>
          {p.dominance && (
            <div className="mt-3 text-xs text-text-2 flex items-center gap-1.5">
              <span className="text-purple">◈</span>
              <span className="text-purple">Dominance:</span> {p.dominance}
            </div>
          )}
        </div>

        {/* Col 3: Trade */}
        <div className="p-5 flex flex-col justify-between">
          <div>
            <div className="text-text-2 text-[10px] tracking-widest uppercase mb-3 flex items-center gap-2">
              <span className={`h-1 w-1 rounded-full bg-${dirColor}`} />
              Trade
            </div>
            <div className="space-y-2.5 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-text-2">Entry</span>
                <span className="text-text-0 font-semibold tabular-nums">{marketYes}¢</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-text-2">Target</span>
                <span className="text-gold font-semibold tabular-nums">{targetNum}¢</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-text-2">Edge</span>
                <span className="text-green font-semibold tabular-nums">+{edge}%</span>
              </div>
            </div>
          </div>
          <a
            href={p.url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 block w-full text-center px-4 py-3 bg-gradient-to-r from-gold to-gold-dim text-bg-0 font-bold text-sm rounded-lg hover:brightness-110 hover:shadow-lg hover:shadow-gold/20 transition-all duration-200 tracking-wide uppercase active:scale-[0.98]"
          >
            Trade on Polymarket ↗
          </a>
        </div>
      </div>

      {/* Row 3 — Chart */}
      <div className="p-4">
        {ph.history && ph.history.length > 0 ? (
          <PriceChart
            history={ph.history}
            oracleValue={ph.oracle ?? oracleYes}
            label={ph.label || p.market || ""}
          />
        ) : (
          <div className="h-[300px] flex flex-col items-center justify-center text-text-2 text-sm gap-2">
            <span className="text-2xl opacity-30">📊</span>
            No chart data available
          </div>
        )}
      </div>

      {/* Footer meta */}
      {p.date && (
        <div className="px-5 py-2.5 border-t border-border text-[10px] text-text-2 flex justify-between bg-bg-0/50">
          <span>{p.date}</span>
          {p.call && <span className="text-text-2 truncate ml-4">{p.call}</span>}
        </div>
      )}
    </div>
  );
}
