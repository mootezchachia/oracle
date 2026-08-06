"""Translate a calendar entry into a severity Gold actually cares about.

The feed's own impact rating is a generic FX rating. Gold has its own
hierarchy: anything that moves the dollar, real yields or the Fed's expected
path is a first-order event, while a regional manufacturing survey is noise
even when the calendar paints it red.

Severity ladder
---------------
``CRITICAL``  FOMC, rate decisions, NFP, CPI — stand aside entirely
``HIGH``      PPI, claims, retail sales, GDP, PCE, ISM, Treasury auctions
``MEDIUM``    second-tier USD data and major non-USD releases
``LOW``       everything else on the calendar
``NONE``      holidays and unrated entries
"""

from __future__ import annotations

from typing import Sequence

from ..config import Config
from ..models import NewsSeverity

# Releases that reprice the dollar and the front end of the curve outright.
_DEFAULT_CRITICAL = (
    "fomc",
    "federal funds rate",
    "fed chair",
    "non-farm employment",
    "nonfarm",
    "core cpi",
    "cpi m/m",
    "interest rate",
    "rate statement",
    "press conference",
)

_DEFAULT_HIGH = (
    "ppi",
    "unemployment claims",
    "retail sales",
    "gdp",
    "core pce",
    "ism",
    "treasury",
    "bond auction",
    "note auction",
    "jobless",
    "employment change",
    "pmi",
)


def _matches(title: str, keywords: Sequence[str]) -> bool:
    lowered = title.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def classify_event(
    title: str, impact: str, currency: str, config: Config | None = None
) -> NewsSeverity:
    """Map a calendar entry to its severity for XAUUSD."""
    if not title:
        return NewsSeverity.NONE

    impact_normalised = (impact or "").strip().lower()
    if impact_normalised == "holiday":
        return NewsSeverity.NONE

    critical = _DEFAULT_CRITICAL
    high = _DEFAULT_HIGH
    if config is not None:
        critical = tuple(config.get("news.critical_keywords", _DEFAULT_CRITICAL))
        high = tuple(config.get("news.high_keywords", _DEFAULT_HIGH))

    currency_upper = (currency or "").upper()
    dollar_driven = currency_upper in {"USD", "ALL"}

    if _matches(title, critical):
        # A CPI print out of the euro area matters, but not the way a US one
        # does. Non-USD criticals are demoted one rung.
        return NewsSeverity.CRITICAL if dollar_driven else NewsSeverity.HIGH

    if _matches(title, high):
        return NewsSeverity.HIGH if dollar_driven else NewsSeverity.MEDIUM

    if impact_normalised == "high":
        return NewsSeverity.HIGH if dollar_driven else NewsSeverity.MEDIUM
    if impact_normalised == "medium":
        return NewsSeverity.MEDIUM if dollar_driven else NewsSeverity.LOW
    return NewsSeverity.LOW


def is_gold_sensitive(title: str) -> bool:
    """Does this release speak directly to Gold's drivers?

    Gold is a real-yield and dollar instrument first, a haven second. These
    keywords cover both channels.
    """
    keywords = (
        "gold",
        "dollar",
        "inflation",
        "cpi",
        "pce",
        "fed",
        "fomc",
        "rate",
        "yield",
        "treasury",
        "employment",
        "payroll",
        "gdp",
    )
    return _matches(title, keywords)
