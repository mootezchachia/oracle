import { useState, useEffect, useCallback } from 'react'

const ENDPOINTS = ['markets', 'reddit', 'news', 'fred', 'predictions', 'price-history', 'portfolio', 'signals', 'crypto', 'strategy100', 'strategy100?view=history', 'auto-research']

async function fetchJSON(endpoint) {
  try {
    const r = await fetch(`/api/${endpoint}`)
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export function useOracleData(interval = 60000) {
  const [data, setData] = useState({
    markets: [],
    reddit: [],
    news: [],
    fred: [],
    predictions: [],
    priceHistory: {},
    portfolio: null,
    status: null,
    signals: null,
    crypto: null,
    strategy100: null,
    history: null,
    research: null,
    loading: true,
    lastUpdate: null,
  })

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled(ENDPOINTS.map(fetchJSON))

    setData(prev => ({
      markets: results[0].value || prev.markets,
      reddit: results[1].value || prev.reddit,
      news: results[2].value || prev.news,
      fred: results[3].value || prev.fred,
      predictions: results[4].value || prev.predictions,
      priceHistory: results[5].value || prev.priceHistory,
      portfolio: results[6].value || prev.portfolio,
      signals: results[7].value || prev.signals,
      crypto: results[8].value || prev.crypto,
      strategy100: results[9].value || prev.strategy100,
      history: results[10].value || prev.history,
      research: results[11].value || prev.research,
      loading: false,
      lastUpdate: Date.now(),
    }))
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, interval)
    return () => clearInterval(id)
  }, [refresh, interval])

  return { ...data, refresh }
}
