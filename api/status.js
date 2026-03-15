export default function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
  res.status(200).json({
    status: "online",
    counts: {
      markets: 30,
      reddit: 25,
      news: 40,
      fred: 6,
      predictions: 7,
    },
  });
}
