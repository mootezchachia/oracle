import PriceChart from "./PriceChart";

function extractOdds(oddsArray) {
  if (!Array.isArray(oddsArray) || oddsArray.length === 0) return { yes: 50, no: 50 };
  const nums = oddsArray.map((s) => {
    const m = String(s).match(/(\d+)%/);
    return m ? parseInt(m[1], 10) : 50;
  });
  return { yes: nums[0] ?? 50, no: nums[1] ?? (100 - (nums[0] ?? 50)) };
}

function extractCurrentPrice(str) {
  if (!str) return { price: 0, side: "Yes" };
  const m = String(str).match(/(\d+)/);
  const price = m ? parseInt(m[1], 10) : 0;
  const side = /no/i.test(str) ? "No" : "Yes";
  return { price, side };
}

function extractCall(primaryCall) {
  if (!primaryCall) return { direction: "YES", confidence: 50 };
  const dirMatch = String(primaryCall).match(/BUY\s+(YES|NO)/i);
  const confMatch = String(primaryCall).match(/Confidence:\s*(\d+)/i);
  return {
    direction: dirMatch ? dirMatch[1].toUpperCase() : "YES",
    confidence: confMatch ? parseInt(confMatch[1], 10) : 50,
  };
}

export default function PredictionCard({ prediction, priceHistory }) {
  const p = prediction || {};
  const ph = priceHistory || {};

  const odds = extractOdds(p.our_odds);
  const current = extractCurrentPrice(p.current_price);
  const { direction, confidence } = extractCall(p.primary_call);

  const isYes = direction === "YES";
  const yesPrice = current.side === "Yes" ? current.price : 100 - current.price;
  const noPrice = 100 - yesPrice;
  const oracleVal = odds.yes;
  const edge = Math.abs(oracleVal - yesPrice);

  const targetMatch = p.target_price ? String(p.target_price).match(/(\d+)/) : null;
  const targetNum = targetMatch ? parseInt(targetMatch[1], 10) : oracleVal;

  return (
    <div className="bg-bg-1 border border-border rounded-xl overflow-hidden font-mono">
      {/* Row 1 — Action Banner */}
      <div className="flex items-center justify-between px-5 py-4 bg-bg-0 border-b border-border">
        {/* Left: Action badge */}
        <div className="flex items-center gap-3">
          <span
            className={`px-4 py-1.5 rounded font-bold text-sm tracking-wide uppercase ${
              isYes
                ? "bg-green text-bg-0"
                : "bg-red text-bg-0"
            }`}
          >
            BUY {direction}
          </span>
          <span className="text-text-2 text-sm">
            {confidence}% confidence
          </span>
        </div>

        {/* Center: Prediction info */}
        <div className="text-center flex-1 px-4">
          <a
            href={p.url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="text-text-0 hover:text-gold transition-colors text-sm font-semibold"
          >
            <span className="text-text-2 mr-2">#{p.number}</span>
            {p.market || "Unknown Market"}
            <span className="text-text-2 ml-1 text-xs align-top">↗</span>
          </a>
        </div>

        {/* Right: Edge */}
        <div className="text-right">
          <span className="text-gold text-2xl font-bold">+{edge}%</span>
          <span className="text-text-2 text-xs block">EDGE</span>
        </div>
      </div>

      {/* Row 2 — Three columns */}
      <div className="grid grid-cols-3 divide-x divide-border border-b border-border">
        {/* Col 1: Polymarket Now */}
        <div className="p-5">
          <div className="text-text-2 text-xs tracking-widest uppercase mb-3">
            Polymarket Now
          </div>
          <div className="space-y-2">
            <div className="flex items-baseline justify-between">
              <span className="text-text-2 text-sm">Yes</span>
              <span className="text-green text-2xl font-bold">{yesPrice}¢</span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-text-2 text-sm">No</span>
              <span className="text-red text-lg font-semibold">{noPrice}¢</span>
            </div>
            {/* Progress bar */}
            <div className="w-full h-2 bg-bg-3 rounded-full mt-3 overflow-hidden">
              <div
                className="h-full bg-green rounded-full transition-all duration-500"
                style={{ width: `${yesPrice}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-text-2 mt-1">
              <span>YES {yesPrice}%</span>
              <span>NO {noPrice}%</span>
            </div>
          </div>
        </div>

        {/* Col 2: Oracle Says */}
        <div className="p-5">
          <div className="text-text-2 text-xs tracking-widest uppercase mb-3">
            Oracle Says
          </div>
          <div className="text-gold text-3xl font-bold mb-2">{oracleVal}%</div>
          <div className="text-text-2 text-xs leading-relaxed">
            {p.narrative || "No narrative available."}
          </div>
          {p.dominance && (
            <div className="mt-3 text-xs text-text-2">
              <span className="text-purple">Dominance:</span> {p.dominance}
            </div>
          )}
        </div>

        {/* Col 3: Trade */}
        <div className="p-5 flex flex-col justify-between">
          <div>
            <div className="text-text-2 text-xs tracking-widest uppercase mb-3">
              Trade
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-text-2">Entry</span>
                <span className="text-text-0 font-semibold">{yesPrice}¢</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-2">Target</span>
                <span className="text-gold font-semibold">{targetNum}¢</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-2">Edge</span>
                <span className="text-green font-semibold">+{edge}%</span>
              </div>
            </div>
          </div>
          <a
            href={p.url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 block w-full text-center px-4 py-3 bg-gold text-bg-0 font-bold text-sm rounded-lg hover:brightness-110 transition-all tracking-wide uppercase"
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
            oracleValue={ph.oracle ?? oracleVal}
            label={ph.label || p.market || ""}
          />
        ) : (
          <div className="h-[300px] flex items-center justify-center text-text-2 text-sm">
            No chart data available
          </div>
        )}
      </div>

      {/* Footer meta */}
      {p.date && (
        <div className="px-5 py-2 border-t border-border text-xs text-text-2 flex justify-between">
          <span>{p.date}</span>
          {p.call && <span className="text-text-2">{p.call}</span>}
        </div>
      )}
    </div>
  );
}
