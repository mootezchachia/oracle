"""CSV-backed provider for backtesting, replay and deterministic tests.

Accepts the two layouts you actually encounter: the MT5 history-centre export
(``<DATE>\\t<TIME>\\t<OPEN>...``) and generic ``timestamp,open,high,low,close,
volume`` files. Column names are matched case-insensitively.

Only the base timeframe needs a file; anything higher is aggregated, which
guarantees the backtest and the live engine see identically constructed higher
timeframe bars.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..logging_setup import get_logger
from ..models import UTC, Candle, Timeframe
from .base import DataProvider, aggregate

log = get_logger("data.csv")

_TS_KEYS = ("timestamp", "time", "datetime", "date_time", "<date>", "date")
_OHLC = {
    "open": ("open", "<open>", "o"),
    "high": ("high", "<high>", "h"),
    "low": ("low", "<low>", "l"),
    "close": ("close", "<close>", "c"),
}
_VOLUME_KEYS = ("volume", "tickvol", "tick_volume", "<tickvol>", "<vol>", "vol")

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
)


def _parse_timestamp(raw: str) -> datetime | None:
    raw = raw.strip().replace("\t", " ")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    if raw.isdigit():
        value = int(raw)
        if value > 10_000_000_000:      # milliseconds
            value //= 1000
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _pick(row: Mapping[str, str], keys: Sequence[str]) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return None


def load_csv(path: str | Path) -> list[Candle]:
    """Read a CSV/TSV file into candles, skipping malformed rows."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"candle file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        rows = list(reader)

    candles: list[Candle] = []
    skipped = 0
    for row in rows:
        raw_ts = _pick(row, _TS_KEYS)
        if raw_ts is None:
            skipped += 1
            continue
        time_part = _pick(row, ("<time>", "time"))
        # MT5 exports split date and time into separate columns.
        if time_part and time_part != raw_ts and ":" not in raw_ts:
            raw_ts = f"{raw_ts} {time_part}"

        ts = _parse_timestamp(raw_ts)
        if ts is None:
            skipped += 1
            continue
        try:
            values = {field: float(_pick(row, keys) or "nan") for field, keys in _OHLC.items()}
        except (TypeError, ValueError):
            skipped += 1
            continue
        if any(v != v for v in values.values()):    # NaN check
            skipped += 1
            continue

        raw_volume = _pick(row, _VOLUME_KEYS)
        try:
            volume = float(raw_volume) if raw_volume else 0.0
        except ValueError:
            volume = 0.0

        candles.append(
            Candle(ts=ts, open=values["open"], high=values["high"], low=values["low"],
                   close=values["close"], volume=volume, real_volume=volume)
        )

    candles.sort(key=lambda c: c.ts)
    if skipped:
        log.warning("%s: skipped %d malformed rows", path.name, skipped)
    log.info("%s: loaded %d candles", path.name, len(candles))
    return candles


class CSVProvider(DataProvider):
    """Serves candles from memory, optionally advancing a replay cursor.

    In replay mode (``cursor`` set), ``fetch`` only returns bars at or before
    the cursor timestamp — that is what makes the backtester free of lookahead
    bias without the analysis code needing to know it is being replayed.
    """

    name = "csv"

    def __init__(
        self,
        candles: Iterable[Candle],
        base_timeframe: Timeframe = Timeframe.M1,
        symbol_series: Mapping[str, Sequence[Candle]] | None = None,
    ) -> None:
        self._base = base_timeframe
        self._candles = sorted(candles, key=lambda c: c.ts)
        self._symbols = {k: sorted(v, key=lambda c: c.ts) for k, v in (symbol_series or {}).items()}
        self._cache: dict[Timeframe, list[Candle]] = {}
        self.cursor: datetime | None = None

    @classmethod
    def from_file(cls, path: str | Path, base_timeframe: Timeframe = Timeframe.M1) -> "CSVProvider":
        return cls(load_csv(path), base_timeframe)

    async def connect(self) -> bool:
        return bool(self._candles)

    @property
    def supports_symbols(self) -> bool:
        return bool(self._symbols)

    def _series(self, timeframe: Timeframe) -> list[Candle]:
        if timeframe is self._base:
            return self._candles
        if timeframe.seconds < self._base.seconds:
            # You cannot synthesise finer bars from coarser ones. Free data is
            # usually M5 or M15, so the M1 precision layer is simply absent —
            # returning empty lets the engine analyse what actually exists
            # rather than inventing candles that never traded.
            return []
        if timeframe not in self._cache:
            self._cache[timeframe] = aggregate(self._candles, self._base, timeframe)
        return self._cache[timeframe]

    async def fetch(self, timeframe: Timeframe, count: int) -> list[Candle]:
        return self.window(timeframe, count)

    def window(self, timeframe: Timeframe, count: int) -> list[Candle]:
        """Synchronous accessor used directly by the backtester."""
        series = self._series(timeframe)
        if self.cursor is not None:
            series = [c for c in series if c.ts <= self.cursor]
        return series[-count:] if count else list(series)

    async def fetch_symbol(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        series = self._symbols.get(symbol, [])
        if timeframe.seconds < self._base.seconds:
            return []
        if timeframe is not self._base and series:
            series = aggregate(series, self._base, timeframe)
        if self.cursor is not None:
            series = [c for c in series if c.ts <= self.cursor]
        return list(series[-count:]) if count else list(series)

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if not self._candles:
            return None
        return self._candles[0].ts, self._candles[-1].ts

    def base_series(self) -> list[Candle]:
        return list(self._candles)
