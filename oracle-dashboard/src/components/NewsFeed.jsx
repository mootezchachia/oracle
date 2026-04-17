export default function NewsFeed({ items }) {
  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-purple">◆</span> NEWS FEED
          <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">{items?.length || 0}</span>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {(items || []).map((item, i) => (
          <a
            key={item.id || i}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block hover:bg-bg-2/70 cursor-pointer no-underline border-b border-border px-4 py-3 transition-colors group"
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[9px] font-mono font-semibold uppercase tracking-widest text-purple/80 bg-purple/10 px-1.5 py-0.5 rounded">
                {item.source}
              </span>
            </div>
            <div className="text-[11px] font-mono text-text-1 leading-snug group-hover:text-text-0 transition-colors">
              {item.title}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
