"""Economic calendar, headline monitoring and the trading news guard."""

from .calendar_feed import EconomicCalendar, parse_feed  # noqa: F401
from .classifier import classify_event, is_gold_sensitive  # noqa: F401
from .guard import NewsGuard, NewsState  # noqa: F401
from .headlines import HeadlineMonitor, parse_rss  # noqa: F401

__all__ = [
    "EconomicCalendar",
    "HeadlineMonitor",
    "NewsGuard",
    "NewsState",
    "classify_event",
    "is_gold_sensitive",
    "parse_feed",
    "parse_rss",
]
