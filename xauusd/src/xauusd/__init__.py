"""XAUUSD Sentinel — institutional-grade Gold monitoring and signal engine.

The package is layered:

``models``      domain types shared by everything
``config``      YAML + environment configuration
``data``        async market-data providers (MT5, Yahoo, CSV)
``sessions``    DST-aware trading sessions and ICT kill zones
``news``        economic calendar guard and geopolitical headline monitor
``analysis``    indicators, market structure, SMC, price action, volatility,
                correlation and the multi-timeframe alignment engine
``engine``      confluence scoring, confidence, risk plan, orchestration
``notify``      Telegram / Discord dispatch
``dashboard``   mobile-friendly async web dashboard
``backtest``    historical replay and performance metrics
``learning``    signal journal and adaptive weight optimisation
"""

from .models import (  # noqa: F401
    ALL_TIMEFRAMES,
    Candle,
    Decision,
    Direction,
    Evidence,
    NewsSeverity,
    RiskPlan,
    Signal,
    Timeframe,
    VolatilityRegime,
)

__version__ = "1.0.0"
__all__ = [
    "ALL_TIMEFRAMES",
    "Candle",
    "Decision",
    "Direction",
    "Evidence",
    "NewsSeverity",
    "RiskPlan",
    "Signal",
    "Timeframe",
    "VolatilityRegime",
    "__version__",
]
