"""Automatic logging configuration.

A rotating file handler plus a colourised console handler. Optionally emits
structured JSON lines, which is what you want when the container ships logs to
a collector.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from .config import Config

_LEVEL_COLOURS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;196m",
    "CRITICAL": "\033[48;5;196m\033[97m",
}
_RESET = "\033[0m"


class ConsoleFormatter(logging.Formatter):
    def __init__(self, colour: bool = True) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)-28s %(message)s", "%H:%M:%S")
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.colour:
            return text
        colour = _LEVEL_COLOURS.get(record.levelname, "")
        return f"{colour}{text}{_RESET}" if colour else text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def setup_logging(config: Config | None = None) -> logging.Logger:
    """Configure the root logger. Safe to call more than once."""
    cfg = config.section("logging") if config else Config()
    level = str(cfg.get("level", "INFO")).upper()
    directory = Path(cfg.get("directory", "logs"))
    filename = cfg.get("file", "sentinel.log")
    use_json = bool(cfg.get("json", False))

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(JsonFormatter() if use_json else ConsoleFormatter(sys.stdout.isatty()))
    root.addHandler(console)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / filename,
            maxBytes=int(cfg.get("max_bytes", 10 * 1024 * 1024)),
            backupCount=int(cfg.get("backup_count", 7)),
            encoding="utf-8",
        )
        file_handler.setFormatter(
            JsonFormatter()
            if use_json
            else logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)-28s %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )
        root.addHandler(file_handler)
    except OSError as exc:  # read-only filesystem, etc. — console still works
        root.warning("file logging disabled: %s", exc)

    # Third-party noise reduction.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"xauusd.{name}")
