"""Order execution — demo accounts by default, XAUUSD only.

Disabled unless ``execution.enabled`` is turned on. When enabled, the MT5
broker refuses to arm on anything but a demo account unless you explicitly
disable that check, and it will only trade a symbol on the allowlist.
"""

from ..config import Config
from .broker import (  # noqa: F401
    AccountInfo,
    AccountType,
    Broker,
    OrderResult,
    Position,
    SafetyError,
    SymbolSpec,
)
from .manager import ExecutionDecision, ExecutionManager  # noqa: F401
from .paper_broker import PaperBroker  # noqa: F401

__all__ = [
    "AccountInfo",
    "AccountType",
    "Broker",
    "ExecutionDecision",
    "ExecutionManager",
    "OrderResult",
    "PaperBroker",
    "Position",
    "SafetyError",
    "SymbolSpec",
    "build_broker",
]


def build_broker(config: Config) -> Broker:
    """Construct the configured broker.

    ``mt5`` is imported lazily so that the package remains importable on
    platforms where MetaTrader5 cannot be installed at all.
    """
    mode = str(config.get("execution.mode", "paper")).lower()
    if mode == "mt5":
        from .mt5_broker import MT5Broker

        return MT5Broker(config)
    if mode == "paper":
        return PaperBroker(config)
    raise ValueError(f"unknown execution mode {mode!r}; expected 'paper' or 'mt5'")
