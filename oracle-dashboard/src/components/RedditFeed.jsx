export default function RedditFeed({ posts }) {
  const maxVelocity = Math.max(...(posts || []).map((p) => p.velocity || 0), 1);

  const formatVelocity = (v) => {
    if (v >= 1000) return `${(v / 1000).toFixed(1)}K`;
    return String(v);
  };

  const getBarColor = (v) => {
    const ratio = v / maxVelocity;
    if (ratio > 0.7) return 'bg-red';
    if (ratio > 0.4) return 'bg-orange';
    return 'bg-blue';
  };

  return (
    <div className="bg-bg-1 border border-border rounded-lg overflow-hidden card-hover">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-gradient-to-r from-bg-2 to-bg-1">
        <div className="flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-2">
          <span className="text-red">◆</span> REDDIT — VELOCITY TRACKER
          <span className="bg-bg-3 text-text-1 text-[9px] px-2 py-0.5 rounded-full">{posts?.length || 0}</span>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {(posts || []).map((post, i) => (
          <div
            key={post.id || i}
            className="grid grid-cols-[60px_1fr_80px_50px] sm:grid-cols-[70px_1fr_80px_60px] items-center px-4 py-2.5 border-b border-border hover:bg-bg-2/70 transition-colors group"
          >
            <div className="relative h-5 flex items-center">
              <div
                className={`${getBarColor(post.velocity || 0)} h-full rounded-sm opacity-80 transition-all duration-500`}
                style={{ width: `${Math.max(((post.velocity || 0) / maxVelocity) * 100, 8)}%` }}
              />
              <span className="absolute inset-0 flex items-center justify-center text-[9px] font-mono font-semibold text-text-0 tabular-nums">
                {formatVelocity(post.velocity || 0)}
              </span>
            </div>
            <div className="text-[11px] font-mono text-text-1 truncate px-2 group-hover:text-text-0 transition-colors">
              {post.title}
            </div>
            <div className="text-[10px] font-mono text-text-2 truncate">
              r/{post.subreddit}
            </div>
            <div className="text-[10px] font-mono text-text-1 text-right flex items-center justify-end gap-0.5 tabular-nums">
              <span className="text-green">▲</span> {post.score}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
