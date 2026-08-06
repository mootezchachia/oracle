"""External platform integrations."""

from .tradingview import PINE_ALERT_TEMPLATE, ExternalSignal, TradingViewBridge  # noqa: F401

__all__ = ["PINE_ALERT_TEMPLATE", "ExternalSignal", "TradingViewBridge"]
