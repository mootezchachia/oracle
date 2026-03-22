import { useState } from 'react'
import { useOracleData } from './hooks/useOracleData'
import Header from './components/Header'
import Ticker from './components/Ticker'
import PredictionCard from './components/PredictionCard'
import PortfolioPanel from './components/PortfolioPanel'
import MarketsTable from './components/MarketsTable'
import RedditFeed from './components/RedditFeed'
import NewsFeed from './components/NewsFeed'
import EconPanel from './components/EconPanel'
import SignalPanel from './components/SignalPanel'
import Strategy100Panel from './components/Strategy100Panel'
import StatusBar from './components/StatusBar'

export default function App() {
  const [activeTab, setActiveTab] = useState('DASHBOARD')
  const {
    markets, reddit, news, fred, predictions, priceHistory,
    portfolio, signals, strategy100, status, loading, refresh
  } = useOracleData(60000)

  const activePreds = predictions.filter(p => p.market)

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <div className="w-8 h-8 border-2 border-border border-t-gold rounded-full animate-spin" />
        <div className="text-[10px] tracking-[3px] uppercase text-text-2">
          Loading ORACLE data feeds...
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen pb-10">
      <Header status={status} onScan={refresh} activeTab={activeTab} onTabChange={setActiveTab} />
      <Ticker markets={markets} reddit={reddit} />

      <main className="p-4 max-w-[1800px] mx-auto">

        {/* ─── DASHBOARD TAB ─── */}
        {activeTab === 'DASHBOARD' && (
          <div className="grid grid-cols-3 gap-3">
            {portfolio && (
              <div className="col-span-3">
                <PortfolioPanel portfolio={portfolio} />
              </div>
            )}

            {strategy100 && (
              <div className="col-span-3">
                <Strategy100Panel data={strategy100} />
              </div>
            )}

            <div className="col-span-3 space-y-3">
              {activePreds.length > 0 ? (
                activePreds.map(pred => {
                  const slug = Object.keys(priceHistory).find(s =>
                    pred.url && pred.url.includes(s)
                  )
                  const ph = slug ? priceHistory[slug] : null
                  return (
                    <PredictionCard
                      key={pred.number || pred.file}
                      prediction={pred}
                      priceHistory={ph}
                    />
                  )
                })
              ) : (
                <div className="bg-bg-1 border border-border rounded-lg p-8 text-center">
                  <div className="text-text-2 text-sm">
                    No active predictions. Run a simulation to generate one.
                  </div>
                </div>
              )}
            </div>

            <div className="col-span-2">
              <MarketsTable markets={markets} />
            </div>
            <div className="col-span-1">
              <EconPanel indicators={fred} />
            </div>

            {signals && (
              <div className="col-span-2">
                <SignalPanel signals={signals} />
              </div>
            )}

            <div className="col-span-1">
              <NewsFeed items={news} />
            </div>

            <div className="col-span-2">
              <RedditFeed posts={reddit} />
            </div>
          </div>
        )}

        {/* ─── MARKETS TAB ─── */}
        {activeTab === 'MARKETS' && (
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-3">
              <MarketsTable markets={markets} />
            </div>
            <div className="col-span-2">
              <EconPanel indicators={fred} />
            </div>
            <div className="col-span-1">
              <NewsFeed items={news} />
            </div>
            <div className="col-span-2">
              <RedditFeed posts={reddit} />
            </div>
          </div>
        )}

        {/* ─── SIGNALS TAB ─── */}
        {activeTab === 'SIGNALS' && (
          <div className="grid grid-cols-3 gap-3">
            {signals && (
              <div className="col-span-3">
                <SignalPanel signals={signals} />
              </div>
            )}
            {strategy100 && (
              <div className="col-span-3">
                <Strategy100Panel data={strategy100} />
              </div>
            )}
          </div>
        )}

        {/* ─── PREDICTIONS TAB ─── */}
        {activeTab === 'PREDICTIONS' && (
          <div className="grid grid-cols-3 gap-3">
            {portfolio && (
              <div className="col-span-3">
                <PortfolioPanel portfolio={portfolio} />
              </div>
            )}
            <div className="col-span-3 space-y-3">
              {activePreds.length > 0 ? (
                activePreds.map(pred => {
                  const slug = Object.keys(priceHistory).find(s =>
                    pred.url && pred.url.includes(s)
                  )
                  const ph = slug ? priceHistory[slug] : null
                  return (
                    <PredictionCard
                      key={pred.number || pred.file}
                      prediction={pred}
                      priceHistory={ph}
                    />
                  )
                })
              ) : (
                <div className="bg-bg-1 border border-border rounded-lg p-8 text-center">
                  <div className="text-text-2 text-sm">
                    No active predictions yet.
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

      </main>

      <StatusBar status={status} />
    </div>
  )
}
