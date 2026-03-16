import React from 'react';

export default function EconPanel({ indicators }) {
  const formatValue = (v) => {
    if (v == null) return '—';
    return Math.abs(v) > 100 ? v.toFixed(1) : v.toFixed(2);
  };

  const formatChange = (c) => {
    if (c == null) return '';
    const prefix = c > 0 ? '+' : '';
    return `${prefix}${Math.abs(c) > 100 ? c.toFixed(1) : c.toFixed(2)}`;
  };

  const getTrend = (ind) => {
    const dir = ind.trend || ind.direction || 'flat';
    if (dir === 'up') return { symbol: '▲', color: 'text-green' };
    if (dir === 'down') return { symbol: '▼', color: 'text-red' };
    return { symbol: '●', color: 'text-text-2' };
  };

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-bg-2">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span style={{ color: 'var(--color-orange)' }}>◆</span> ECONOMIC DATA
          <span className="bg-bg-3 text-text-1 text-[9px] px-1.5 rounded-full">{indicators?.length || 0}</span>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {(indicators || []).map((ind, i) => {
          const trend = getTrend(ind);
          return (
            <div
              key={ind.id || i}
              className="flex items-center justify-between px-4 py-2.5 border-b border-border hover:bg-bg-2"
            >
              <div>
                <div className="text-[10px] font-mono text-text-2">{ind.name}</div>
                <div className="text-[9px] font-mono text-text-2 mt-0.5">
                  {ind.date}{ind.cached ? ' (cached)' : ''}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-mono font-semibold text-text-0">
                  {formatValue(ind.value)}
                </span>
                <span className={`text-[10px] ${trend.color}`}>{trend.symbol}</span>
                <span className="text-[10px] font-mono text-text-2">
                  {formatChange(ind.change)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
