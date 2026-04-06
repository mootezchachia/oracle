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
import HistoryPanel from './components/HistoryPanel'
import ResearchPanel from './components/ResearchPanel'
import ForecastPanel from './components/ForecastPanel'
import StatusBar from './components/StatusBar'

export default function App() {
  const [activeTab, setActiveTab] = useState('DASHBOARD')
  const {
    markets, reddit, news, fred, predictions, priceHistory,
    portfolio, signals, strategy100, history, research, forecast, status, loading, refresh
  } = useOracleData(60000)

  const activePreds = predictions.filter(p => p.market)

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <div className="relative">
          <div className="w-12 h-12 rounded-full border-2 border-border border-t-gold animate-spin" />
          <div className="absolute inset-0 w-12 h-12 rounded-full border-2 border-transparent border-b-gold/30 animate-spin" style={{ animationDirection: 'reverse', animationDuration: '1.5s' }} />
        </div>
        <div className="text-[10px] tracking-[3px] uppercase text-text-2 animate-pulse">
          Loading ORACLE data feeds...
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen pb-10">
      <Header status={status} onScan={refresh} activeTab={activeTab} onTabChange={setActiveTab} />
      <Ticker markets={markets} reddit={reddit} />

      <main className="p-3 sm:p-4 max-w-[1800px] mx-auto">

        {/* ─── DASHBOARD TAB ─── */}
        {activeTab === 'DASHBOARD' && (
          <div key="dashboard" className="tab-content grid grid-cols-1 lg:grid-cols-3 gap-3">
            {portfolio && (
              <div className="col-span-1 lg:col-span-3">
                <PortfolioPanel portfolio={portfolio} />
              </div>
            )}

            {strategy100 && (
              <div className="col-span-1 lg:col-span-3">
                <Strategy100Panel data={strategy100} />
              </div>
            )}

            <div className="col-span-1 lg:col-span-3 space-y-3">
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
                <div className="bg-bg-1 border border-border rounded-lg p-8 text-center animate-fade-in">
                  <div className="text-text-2 text-sm">
                    No active predictions. Run a simulation to generate one.
                  </div>
                </div>
              )}
            </div>

            <div className="col-span-1 lg:col-span-2">
              <MarketsTable markets={markets} />
            </div>
            <div className="col-span-1">
              <EconPanel indicators={fred} />
            </div>

            {signals && (
              <div className="col-span-1 lg:col-span-2">
                <SignalPanel signals={signals} />
              </div>
            )}

            <div className="col-span-1">
              <NewsFeed items={news} />
            </div>

            <div className="col-span-1 lg:col-span-2">
              <RedditFeed posts={reddit} />
            </div>
          </div>
        )}

        {/* ─── MARKETS TAB ─── */}
        {activeTab === 'MARKETS' && (
          <div key="markets" className="tab-content grid grid-cols-1 lg:grid-cols-3 gap-3">
            <div className="col-span-1 lg:col-span-3">
              <MarketsTable markets={markets} />
            </div>
            <div className="col-span-1 lg:col-span-2">
              <EconPanel indicators={fred} />
            </div>
            <div className="col-span-1">
              <NewsFeed items={news} />
            </div>
            <div className="col-span-1 lg:col-span-2">
              <RedditFeed posts={reddit} />
            </div>
          </div>
        )}

        {/* ─── SIGNALS TAB ─── */}
        {activeTab === 'SIGNALS' && (
          <div key="signals" className="tab-content grid grid-cols-1 lg:grid-cols-3 gap-3">
            {signals && (
              <div className="col-span-1 lg:col-span-3">
                <SignalPanel signals={signals} />
              </div>
            )}
          </div>
        )}

        {/* ─── STRATEGY TAB ─── */}
        {activeTab === 'STRATEGY' && (
          <div key="strategy" className="tab-content grid grid-cols-1 lg:grid-cols-3 gap-3">
            {strategy100 && (
              <div className="col-span-1 lg:col-span-3">
                <Strategy100Panel data={strategy100} />
              </div>
            )}
            {portfolio && (
              <div className="col-span-1 lg:col-span-3">
                <PortfolioPanel portfolio={portfolio} />
              </div>
            )}
            <div className="col-span-1 lg:col-span-3">
              <HistoryPanel history={history} />
            </div>
          </div>
        )}

        {/* ─── PREDICTIONS TAB ─── */}
        {activeTab === 'PREDICTIONS' && (
          <div key="predictions" className="tab-content grid grid-cols-1 lg:grid-cols-3 gap-3">
            {portfolio && (
              <div className="col-span-1 lg:col-span-3">
                <PortfolioPanel portfolio={portfolio} />
              </div>
            )}
            <div className="col-span-1 lg:col-span-3 space-y-3">
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
                <div className="bg-bg-1 border border-border rounded-lg p-8 text-center animate-fade-in">
                  <div className="text-text-2 text-sm">
                    No active predictions yet.
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ─── FORECAST TAB ─── */}
        {activeTab === 'FORECAST' && (
          <div key="forecast" className="tab-content">
            <ForecastPanel data={forecast} />
          </div>
        )}

        {/* ─── RESEARCH TAB ─── */}
        {activeTab === 'RESEARCH' && (
          <div key="research" className="tab-content">
            <ResearchPanel data={research} />
          </div>
        )}

      </main>

      <StatusBar status={status} />
    </div>
  )
}
