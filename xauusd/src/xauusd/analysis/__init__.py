"""Analysis layer: indicators, market structure, SMC, price action, volatility,
correlation and the multi-timeframe alignment engine.

Every module here is pure — same inputs always produce the same outputs, no
I/O, no clocks. That is what makes the backtester and the live engine share
exactly the same code path.
"""

from .correlation import CorrelationReport, analyse_correlations  # noqa: F401
from .mtf import (  # noqa: F401
    IndicatorSnapshot,
    MTFResult,
    TimeframeAnalysis,
    analyse_all,
    analyse_timeframe,
    resolve_alignment,
)
from .price_action import net_pattern_bias, scan_patterns  # noqa: F401
from .smc import SMCReport, analyse_smc, optimal_trade_entry, power_of_three  # noqa: F401
from .structure import StructureReport, analyse_structure, find_swings  # noqa: F401
from .volatility import VolatilityState, classify_volatility  # noqa: F401

__all__ = [
    "CorrelationReport",
    "IndicatorSnapshot",
    "MTFResult",
    "SMCReport",
    "StructureReport",
    "TimeframeAnalysis",
    "VolatilityState",
    "analyse_all",
    "analyse_correlations",
    "analyse_smc",
    "analyse_structure",
    "analyse_timeframe",
    "classify_volatility",
    "find_swings",
    "net_pattern_bias",
    "optimal_trade_entry",
    "power_of_three",
    "resolve_alignment",
    "scan_patterns",
]
