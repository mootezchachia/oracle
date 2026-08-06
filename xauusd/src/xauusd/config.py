"""Configuration loading.

Configuration is a plain nested ``dict`` wrapped in :class:`Config`, which adds
dotted-path access with defaults. Values may be overridden by environment
variables using the ``XAUUSD_`` prefix and ``__`` as the nesting separator::

    XAUUSD_SIGNALS__MIN_CONFIDENCE=92
    XAUUSD_NOTIFY__TELEGRAM__BOT_TOKEN=123:abc

Secrets should always be supplied this way rather than committed to YAML.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

ENV_PREFIX = "XAUUSD_"
_MISSING = object()

DEFAULT_CONFIG_PATHS = (
    Path("config/config.yaml"),
    Path(__file__).resolve().parents[2] / "config" / "config.yaml",
)


def _coerce(raw: str) -> Any:
    """Turn an environment string into the most plausible Python value."""
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.startswith(("[", "{")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return raw


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    return base


def _apply_env(data: dict[str, Any]) -> dict[str, Any]:
    for env_key, raw in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        path = env_key[len(ENV_PREFIX):].lower().split("__")
        if not path or not path[0]:
            continue
        cursor: dict[str, Any] = data
        for part in path[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = _coerce(raw)
    return data


class Config:
    """Immutable-ish view over the merged configuration tree."""

    __slots__ = ("_data", "source")

    def __init__(self, data: Mapping[str, Any] | None = None, source: str = "<memory>") -> None:
        self._data: dict[str, Any] = copy.deepcopy(dict(data or {}))
        self.source = source

    # -- access -------------------------------------------------------------
    def get(self, path: str, default: Any = _MISSING) -> Any:
        """Fetch a dotted path, e.g. ``cfg.get("risk.max_risk_percent", 1.0)``."""
        cursor: Any = self._data
        for part in path.split("."):
            if isinstance(cursor, Mapping) and part in cursor:
                cursor = cursor[part]
            else:
                if default is _MISSING:
                    raise KeyError(f"config key not found: {path!r}")
                return default
        return cursor

    def section(self, path: str) -> "Config":
        value = self.get(path, {})
        if not isinstance(value, Mapping):
            value = {}
        return Config(value, source=f"{self.source}#{path}")

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __contains__(self, path: str) -> bool:
        return self.get(path, _MISSING) is not _MISSING

    def __getitem__(self, path: str) -> Any:
        return self.get(path)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Config(source={self.source!r}, keys={sorted(self._data)})"

    # -- mutation (used by the optimiser to push learned weights) -----------
    def override(self, overlay: Mapping[str, Any]) -> "Config":
        merged = _deep_merge(self.as_dict(), overlay)
        return Config(merged, source=f"{self.source}+override")


def load_config(path: str | Path | None = None, *, apply_env: bool = True) -> Config:
    """Load YAML config, then layer environment overrides on top."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        env_path = os.environ.get("XAUUSD_CONFIG")
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend(DEFAULT_CONFIG_PATHS)

    data: dict[str, Any] = {}
    source = "<defaults>"
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"config root must be a mapping: {candidate}")
            data = loaded
            source = str(candidate)
            break
    else:
        if path is not None:
            raise FileNotFoundError(f"config file not found: {path}")

    if apply_env:
        data = _apply_env(data)
    return Config(data, source=source)
