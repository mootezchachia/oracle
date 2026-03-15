import React from 'react';

export default function NewsFeed({ items }) {
  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-bg-2">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span style={{ color: 'var(--color-purple)' }}>◆</span> NEWS FEED
          <span className="bg-bg-3 text-text-1 text-[9px] px-1.5 rounded-full">{items?.length || 0}</span>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {(items || []).map((item, i) => (
          <a
            key={item.id || i}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block hover:bg-bg-2 cursor-pointer no-underline border-b border-border"
            style={{ padding: '10px 16px' }}
          >
            <div className="text-[9px] font-mono font-semibold uppercase tracking-widest text-text-2 mb-1">
              {item.source}
            </div>
            <div className="text-[11px] font-mono text-text-0 leading-snug">
              {item.title}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
