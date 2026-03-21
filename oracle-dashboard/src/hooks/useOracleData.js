import { useState, useEffect, useCallback } from 'react'

const ENDPOINTS = ['markets', 'reddit', 'news', 'fred', 'predictions', 'status', 'price-history', 'portfolio', 'signals', 'crypto']

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
      status: results[5].value || prev.status,
      priceHistory: results[6].value || prev.priceHistory,
      portfolio: results[7].value || prev.portfolio,
      signals: results[8].value || prev.signals,
      crypto: results[9].value || prev.crypto,
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
