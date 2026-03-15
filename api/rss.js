export default function handler(req, res) {
  const predictions = [
    {n:1, market:"US x Iran Ceasefire by March 31", url:"https://polymarket.com/market/us-x-iran-ceasefire-by-march-31", our:8, mkt:15, dir:"BUY NO", edge:7, conf:78, tweet:"ORACLE #001: US-Iran ceasefire by March 31? Polymarket says 15%. We say 8%. No diplomatic signals. No back-channels. 16 days isn't enough. BUY NO at 85c. Target: 88-90c."},
    {n:2, market:"Will Crude Oil (CL) hit $120 by end of March?", url:"https://polymarket.com/market/will-crude-oil-cl-hit-high-120-by-end-of-march-766-813-597", our:52, mkt:45, dir:"BUY YES", edge:7, conf:62, tweet:"ORACLE #002: Oil $120 by March 31? Polymarket says 45%. We say 52%. Kharg Island offline. Saudi can't surge fast enough. BUY YES at 45c. Target: 55-60c."},
    {n:3, market:"US x Iran ceasefire by May 31", url:"https://polymarket.com/market/us-x-iran-ceasefire-by-may-31-313", our:38, mkt:51.5, dir:"BUY NO", edge:13.5, conf:70, tweet:"ORACLE #003: Iran ceasefire by May 31? Polymarket says 51.5%. We say 38%. No mediator exists. BUY NO at 48.5c."},
    {n:4, market:"Will Crude Oil (CL) hit $140 by end of March?", url:"https://polymarket.com/market/will-crude-oil-cl-hit-high-140-by-end-of-march-934-621", our:14, mkt:20.5, dir:"BUY NO", edge:6.5, conf:72, tweet:"ORACLE #004: Oil $140 by March 31? Polymarket says 20.5%. We say 14%. $140 needs Hormuz CLOSED. BUY NO at 79.5c."},
    {n:5, market:"Will the Iranian regime fall before 2027?", url:"https://polymarket.com/market/will-the-iranian-regime-fall-by-the-end-of-2026", our:18, mkt:38.5, dir:"BUY NO", edge:20.5, conf:75, tweet:"ORACLE #005: Iranian regime fall before 2027? Polymarket says 38.5%. We say 18%. No regime with loyal security forces falls from bombing alone. BUY NO at 61.5c."},
    {n:6, market:"US x Iran Ceasefire by June 30", url:"https://polymarket.com/market/us-x-iran-ceasefire-by-june-30-752", our:48, mkt:59.5, dir:"SELL YES", edge:11.5, conf:58, tweet:"ORACLE #006: Iran ceasefire by June 30? Polymarket says 59.5%. We say 48%. Hope curve, not probability curve. SELL YES at 59.5c."},
    {n:7, market:"Will Russia enter Druzhkivka by June 30?", url:"https://polymarket.com/market/will-russia-enter-druzkhivka-by-june-30-933-897", our:14, mkt:25.5, dir:"BUY NO", edge:11.5, conf:72, tweet:"ORACLE #007: Russia enter Druzhkivka? Polymarket says 25.5%. We say 14%. 1-2km/month advance vs 20km distance. BUY NO at 74.5c."},
  ];

  const items = predictions.map(p => `
    <item>
      <title>ORACLE #${String(p.n).padStart(3,'0')}: ${p.market}</title>
      <link>${p.url}</link>
      <description><![CDATA[${p.tweet}]]></description>
      <pubDate>Sat, 15 Mar 2026 03:00:00 GMT</pubDate>
      <guid isPermaLink="false">oracle-${String(p.n).padStart(3,'0')}</guid>
    </item>`).join('');

  const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>ORACLE Predictions by @TheSwarmCall</title>
    <link>https://polymarket.com</link>
    <description>AI-powered prediction market analysis. Narrative arbitrage engine.</description>
    <language>en-us</language>${items}
  </channel>
</rss>`;

  res.setHeader('Content-Type', 'application/rss+xml; charset=utf-8');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate');
  res.status(200).send(rss);
}
