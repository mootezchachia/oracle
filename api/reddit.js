const SUBREDDITS = ["politics", "worldnews", "geopolitics", "economics"];
const USER_AGENT =
  "Mozilla/5.0 (compatible; ORACLE/1.0; +https://oracle-psi-orpin.vercel.app)";

const FETCH_HEADERS = {
  "User-Agent": USER_AGENT,
  Accept: "application/json",
  "Accept-Language": "en-US,en;q=0.9",
};

const FALLBACK_MESSAGE = [
  {
    title: "Reddit temporarily unavailable — narrative data may be delayed",
    subreddit: "status",
    score: 0,
    comments: 0,
    velocity: 0,
    age_hours: 0,
    url: "https://reddit.com",
    created: Date.now() / 1000,
  },
];

async function fetchSubreddit(sub) {
  const urls = [
    `https://www.reddit.com/r/${sub}/hot.json?limit=15`,
    `https://old.reddit.com/r/${sub}/hot.json?limit=15`,
  ];

  for (const url of urls) {
    try {
      const r = await fetch(url, {
        headers: FETCH_HEADERS,
        signal: AbortSignal.timeout(10000),
      });

      if (r.status === 403 || r.status === 429) {
        // Blocked or rate-limited, try next URL
        continue;
      }

      if (!r.ok) continue;

      const json = await r.json();
      const children = json?.data?.children;
      if (children && children.length > 0) {
        return children;
      }
    } catch {
      // Network error or timeout, try next URL
      continue;
    }
  }

  return null;
}

export default async function handler(req, res) {
  try {
    const results = [];
    const now = Date.now() / 1000;

    const fetches = SUBREDDITS.map((sub) => fetchSubreddit(sub));
    const settled = await Promise.allSettled(fetches);

    for (let i = 0; i < SUBREDDITS.length; i++) {
      const sub = SUBREDDITS[i];
      const result = settled[i];

      if (result.status !== "fulfilled" || !result.value) continue;

      for (const post of result.value) {
        const d = post.data;
        if (d.stickied) continue;

        const created = d.created_utc || now;
        const ageHours = Math.max((now - created) / 3600, 0.1);
        const score = d.score || 0;
        const comments = d.num_comments || 0;
        const velocity = score / ageHours + (comments / ageHours) * 2;

        results.push({
          title: d.title || "",
          subreddit: sub,
          score,
          comments,
          velocity: Math.round(velocity * 10) / 10,
          age_hours: Math.round(ageHours * 10) / 10,
          url: `https://reddit.com${d.permalink || ""}`,
          created,
        });
      }
    }

    results.sort((a, b) => b.velocity - a.velocity);
    const top = results.slice(0, 25);

    res.setHeader(
      "Cache-Control",
      "s-maxage=120, stale-while-revalidate=300"
    );

    if (top.length === 0) {
      return res.status(200).json(FALLBACK_MESSAGE);
    }

    res.status(200).json(top);
  } catch (err) {
    res.status(500).json({
      error: err.message,
      fallback: FALLBACK_MESSAGE,
    });
  }
}
