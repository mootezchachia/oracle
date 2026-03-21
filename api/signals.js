import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

export default async function handler(req, res) {
  try {
    const dataDir = join(process.cwd(), 'nerve', 'data');

    // Read latest fusion results
    const fusionPath = join(dataDir, 'signal_fusion.jsonl');
    let fusionResults = [];
    if (existsSync(fusionPath)) {
      const lines = readFileSync(fusionPath, 'utf8').trim().split('\n').filter(Boolean);
      fusionResults = lines.slice(-10).map(l => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    }

    // Read latest ensemble predictions
    const ensemblePath = join(dataDir, 'ensemble_predictions.jsonl');
    let ensembleResults = [];
    if (existsSync(ensemblePath)) {
      const lines = readFileSync(ensemblePath, 'utf8').trim().split('\n').filter(Boolean);
      ensembleResults = lines.slice(-10).map(l => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    }

    // Read latest TA signals
    const taPath = join(dataDir, 'technical_signals.jsonl');
    let taSignals = [];
    if (existsSync(taPath)) {
      const lines = readFileSync(taPath, 'utf8').trim().split('\n').filter(Boolean);
      taSignals = lines.slice(-20).map(l => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    }

    // Read executor status
    const posPath = join(dataDir, 'positions.json');
    let executor = null;
    if (existsSync(posPath)) {
      executor = JSON.parse(readFileSync(posPath, 'utf8'));
    }

    // Read 15m crypto analysis
    const cryptoPath = join(dataDir, 'crypto_15m.jsonl');
    let cryptoAnalysis = [];
    if (existsSync(cryptoPath)) {
      const lines = readFileSync(cryptoPath, 'utf8').trim().split('\n').filter(Boolean);
      cryptoAnalysis = lines.slice(-10).map(l => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    }

    res.setHeader('Cache-Control', 's-maxage=30, stale-while-revalidate=60');
    res.status(200).json({
      fusion: fusionResults,
      ensemble: ensembleResults,
      ta_signals: taSignals,
      executor,
      crypto_15m: cryptoAnalysis,
      updated: new Date().toISOString(),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
