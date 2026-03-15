const RSS_FEEDS = {
  "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
  "NPR News": "https://feeds.npr.org/1001/rss.xml",
  "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
  "The Guardian": "https://www.theguardian.com/world/rss",
};

function parseItems(xml, source) {
  const items = [];
  const itemRegex = /<item[\s>]([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];

    const titleMatch = block.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i);
    const linkMatch = block.match(/<link[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/link>/i);
    const pubDateMatch = block.match(/<pubDate[^>]*>([\s\S]*?)<\/pubDate>/i);
    const descMatch = block.match(/<description[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/description>/i);

    const title = (titleMatch ? titleMatch[1] : "").trim();
    if (!title) continue;

    items.push({
      title,
      source,
      url: (linkMatch ? linkMatch[1] : "").trim(),
      published: (pubDateMatch ? pubDateMatch[1] : "").trim(),
      summary: (descMatch ? descMatch[1] : "").replace(/<[^>]+>/g, "").trim().slice(0, 200),
    });
  }
  return items;
}

export default async function handler(req, res) {
  try {
    const results = [];
    const seen = new Set();

    const feedEntries = Object.entries(RSS_FEEDS);
    const fetches = feedEntries.map(async ([source, url]) => {
      try {
        const r = await fetch(url, {
          headers: { "User-Agent": "ORACLE/1.0" },
          signal: AbortSignal.timeout(10000),
        });
        if (!r.ok) return [];
        const xml = await r.text();
        return parseItems(xml, source).slice(0, 10);
      } catch {
        return [];
      }
    });

    const allResults = await Promise.all(fetches);

    for (const items of allResults) {
      for (const item of items) {
        const hash = item.title.toLowerCase().slice(0, 50);
        if (seen.has(hash)) continue;
        seen.add(hash);
        results.push(item);
      }
    }

    res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
    res.status(200).json(results.slice(0, 40));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
