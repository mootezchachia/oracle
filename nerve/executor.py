"""ORACLE — Trade Executor with Risk Management
Inspired by aulekator's risk management system and discountry's flash-crash strategy.

Features:
  1. Position sizing (Kelly criterion + fixed fractional)
  2. Stop-loss / take-profit management
  3. Confidence-gated execution (only trade when signals agree)
  4. Paper trading mode (default) — logs trades without executing
  5. Execution log with P&L tracking
  6. Rate limiting and cooldown between trades

NOTE: Live execution requires py-clob-client for Polymarket.
      This module works in paper-trade mode by default.
"""

import json
import math
import time
from datetime import datetime, timezone
from config import *

TRADES_LOG = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "positions.json"
EXECUTOR_CONFIG = DATA_DIR / "executor_config.json"


# ─── Default Configuration ────────────────────────────────────────

DEFAULT_CONFIG = {
    "mode": "paper",  # "paper" or "live"
    "max_position_usd": 10.0,      # max $ per position
    "max_daily_loss_usd": 20.0,    # stop trading after this daily loss
    "min_confidence": 0.50,         # minimum fusion confidence to trade
    "min_edge_pct": 5.0,           # minimum edge % to trade
    "min_agreement": 0.5,          # minimum signal agreement
    "take_profit_pct": 15.0,       # take profit at +15%
    "stop_loss_pct": 8.0,          # stop loss at -8%
    "cooldown_seconds": 300,       # 5 min between trades on same market
    "kelly_fraction": 0.25,        # quarter-Kelly for safety
}


def load_config() -> dict:
    if EXECUTOR_CONFIG.exists():
        with open(EXECUTOR_CONFIG) as f:
            saved = json.load(f)
        return {**DEFAULT_CONFIG, **saved}
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    with open(EXECUTOR_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


# ─── Position Tracking ───────────────────────────────────────────

def load_positions() -> dict:
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {"positions": [], "daily_pnl": 0.0, "total_trades": 0, "last_reset": now_iso()}


def save_positions(positions: dict):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


# ─── Kelly Criterion ─────────────────────────────────────────────

def kelly_size(win_prob: float, win_payout: float, loss_payout: float,
               fraction: float = 0.25) -> float:
    """Calculate position size using fractional Kelly criterion.

    Args:
        win_prob: probability of winning (0-1)
        win_payout: profit if win (as multiple of bet, e.g. 0.5 = 50% return)
        loss_payout: loss if lose (as multiple of bet, e.g. 1.0 = lose full bet)
        fraction: Kelly fraction (0.25 = quarter Kelly, conservative)

    Returns:
        fraction of bankroll to bet (0-1)
    """
    if win_payout <= 0 or loss_payout <= 0:
        return 0.0

    # Kelly formula: f* = (bp - q) / b
    # where b = win_payout/loss_payout, p = win_prob, q = 1 - p
    b = win_payout / loss_payout
    p = win_prob
    q = 1 - p

    kelly = (b * p - q) / b
    kelly = max(0.0, kelly)  # never bet negative

    return kelly * fraction


# ─── Trade Execution ──────────────────────────────────────────────

class TradeExecutor:
    """Manages trade decisions, sizing, and execution."""

    def __init__(self):
        self.config = load_config()
        self.positions = load_positions()
        self.last_trade_time = {}  # market_id -> timestamp

    def should_trade(self, fusion_result: dict) -> tuple:
        """Check if fusion result meets trading criteria.

        Returns (should_trade: bool, reason: str)
        """
        conf = fusion_result.get("confidence", 0)
        edge = fusion_result.get("edge_pct", 0)
        agreement = fusion_result.get("agreement", 0)
        market_id = fusion_result.get("market_id", "")

        # Confidence gate
        if conf < self.config["min_confidence"]:
            return False, f"Confidence {conf:.1%} < {self.config['min_confidence']:.1%}"

        # Edge gate
        if edge < self.config["min_edge_pct"]:
            return False, f"Edge {edge:.1f}% < {self.config['min_edge_pct']}%"

        # Agreement gate
        if agreement < self.config["min_agreement"]:
            return False, f"Agreement {agreement:.1%} < {self.config['min_agreement']:.1%}"

        # Daily loss limit
        if self.positions.get("daily_pnl", 0) < -self.config["max_daily_loss_usd"]:
            return False, f"Daily loss limit reached: ${self.positions['daily_pnl']:.2f}"

        # Cooldown
        if market_id in self.last_trade_time:
            elapsed = time.time() - self.last_trade_time[market_id]
            if elapsed < self.config["cooldown_seconds"]:
                remaining = self.config["cooldown_seconds"] - elapsed
                return False, f"Cooldown: {remaining:.0f}s remaining for {market_id}"

        # Check for existing position
        existing = [p for p in self.positions.get("positions", [])
                    if p["market_id"] == market_id and p["status"] == "open"]
        if existing:
            return False, f"Already have open position on {market_id}"

        return True, "All criteria met"

    def calculate_size(self, fusion_result: dict) -> float:
        """Calculate position size in USD."""
        conf = fusion_result.get("confidence", 0)
        edge = fusion_result.get("edge_pct", 0)

        # Estimate win probability from confidence
        win_prob = 0.5 + (conf * 0.3)  # 50-80% win rate based on confidence

        # Estimate payout from edge
        win_payout = edge / 100  # e.g. 10% edge -> 0.10 payout
        loss_payout = 1.0  # can lose entire bet on binary market

        # Kelly sizing
        kelly_pct = kelly_size(win_prob, win_payout, loss_payout,
                               self.config["kelly_fraction"])

        # Cap at max position size
        size = min(self.config["max_position_usd"], kelly_pct * 100)
        size = max(0.0, size)

        return round(size, 2)

    def execute(self, fusion_result: dict) -> dict:
        """Execute a trade based on fusion result.

        In paper mode, logs the trade. In live mode, would call py-clob-client.
        """
        should, reason = self.should_trade(fusion_result)
        if not should:
            return {
                "executed": False,
                "reason": reason,
                "market_id": fusion_result.get("market_id", ""),
            }

        market_id = fusion_result.get("market_id", "")
        direction = fusion_result.get("direction", 0)
        recommendation = fusion_result.get("recommendation", "HOLD")
        size = self.calculate_size(fusion_result)

        if size < 0.50:
            return {
                "executed": False,
                "reason": f"Position size too small: ${size:.2f}",
                "market_id": market_id,
            }

        # Build trade record
        trade = {
            "trade_id": f"T-{int(time.time())}",
            "market_id": market_id,
            "side": "YES" if direction > 0 else "NO",
            "size_usd": size,
            "entry_confidence": fusion_result.get("confidence", 0),
            "entry_edge_pct": fusion_result.get("edge_pct", 0),
            "entry_agreement": fusion_result.get("agreement", 0),
            "recommendation": recommendation,
            "mode": self.config["mode"],
            "status": "open",
            "take_profit_pct": self.config["take_profit_pct"],
            "stop_loss_pct": self.config["stop_loss_pct"],
            "opened_at": now_iso(),
            "closed_at": None,
            "pnl": None,
            "signal_breakdown": fusion_result.get("breakdown", [])[:5],
        }

        if self.config["mode"] == "paper":
            trade["note"] = "Paper trade — no real execution"
        else:
            # Live execution would go here
            # from py_clob_client.client import ClobClient
            # client = ClobClient(host, key=key, chain_id=137)
            # order = client.create_and_post_order(...)
            trade["note"] = "Live execution not yet configured"

        # Record trade
        append_jsonl(TRADES_LOG, trade)
        self.positions.setdefault("positions", []).append(trade)
        self.positions["total_trades"] = self.positions.get("total_trades", 0) + 1
        save_positions(self.positions)

        # Set cooldown
        self.last_trade_time[market_id] = time.time()

        return {
            "executed": True,
            "trade": trade,
            "market_id": market_id,
        }

    def check_exits(self, current_prices: dict) -> list:
        """Check open positions for take-profit or stop-loss.

        Args:
            current_prices: {market_id: current_price}

        Returns:
            list of closed trades
        """
        closed = []
        for pos in self.positions.get("positions", []):
            if pos["status"] != "open":
                continue

            market_id = pos["market_id"]
            if market_id not in current_prices:
                continue

            current = current_prices[market_id]
            # For binary markets, entry price is implied by confidence
            entry = pos.get("entry_confidence", 0.5)

            if pos["side"] == "YES":
                pnl_pct = ((current - entry) / max(entry, 0.01)) * 100
            else:
                pnl_pct = ((entry - current) / max(1 - entry, 0.01)) * 100

            should_close = False
            close_reason = ""

            if pnl_pct >= pos.get("take_profit_pct", 15):
                should_close = True
                close_reason = f"Take profit: {pnl_pct:.1f}%"
            elif pnl_pct <= -pos.get("stop_loss_pct", 8):
                should_close = True
                close_reason = f"Stop loss: {pnl_pct:.1f}%"

            if should_close:
                pos["status"] = "closed"
                pos["closed_at"] = now_iso()
                pos["pnl"] = round(pos["size_usd"] * pnl_pct / 100, 2)
                pos["close_reason"] = close_reason

                self.positions["daily_pnl"] = (
                    self.positions.get("daily_pnl", 0) + pos["pnl"]
                )
                closed.append(pos)
                append_jsonl(TRADES_LOG, {**pos, "event": "close"})

        if closed:
            save_positions(self.positions)

        return closed

    def summary(self) -> dict:
        """Get current portfolio summary."""
        positions = self.positions.get("positions", [])
        open_pos = [p for p in positions if p["status"] == "open"]
        closed_pos = [p for p in positions if p["status"] == "closed"]

        total_pnl = sum(p.get("pnl", 0) for p in closed_pos if p.get("pnl"))
        wins = sum(1 for p in closed_pos if p.get("pnl", 0) > 0)
        losses = sum(1 for p in closed_pos if p.get("pnl", 0) < 0)

        return {
            "mode": self.config["mode"],
            "open_positions": len(open_pos),
            "closed_trades": len(closed_pos),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(wins / max(wins + losses, 1), 3),
            "wins": wins,
            "losses": losses,
            "daily_pnl": round(self.positions.get("daily_pnl", 0), 2),
            "total_trades": self.positions.get("total_trades", 0),
            "max_position_usd": self.config["max_position_usd"],
            "open_exposure_usd": round(sum(p["size_usd"] for p in open_pos), 2),
        }


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("Trade Executor")

    executor = TradeExecutor()
    summary = executor.summary()

    print(f"  ═══ EXECUTOR STATUS ═══")
    print(f"  Mode:            {summary['mode'].upper()}")
    print(f"  Open Positions:  {summary['open_positions']}")
    print(f"  Closed Trades:   {summary['closed_trades']}")
    print(f"  Total P&L:       ${summary['total_pnl']:+.2f}")
    print(f"  Win Rate:        {summary['win_rate']:.1%}")
    print(f"  Daily P&L:       ${summary['daily_pnl']:+.2f}")
    print(f"  Open Exposure:   ${summary['open_exposure_usd']:.2f}")
    print(f"  Max Position:    ${summary['max_position_usd']:.2f}")

    # Demo trade
    demo_fusion = {
        "market_id": "demo-iran-ceasefire",
        "direction": -0.6,
        "confidence": 0.72,
        "agreement": 0.85,
        "edge_pct": 7.0,
        "recommendation": "BUY NO",
        "breakdown": [],
    }

    print(f"\n  ═══ DEMO TRADE ═══")
    print(f"  Market: {demo_fusion['market_id']}")
    print(f"  Signal: {demo_fusion['recommendation']} "
          f"(conf: {demo_fusion['confidence']:.1%}, edge: {demo_fusion['edge_pct']}%)")

    result = executor.execute(demo_fusion)
    if result["executed"]:
        t = result["trade"]
        print(f"  EXECUTED: {t['side']} ${t['size_usd']:.2f} ({t['mode']})")
    else:
        print(f"  NOT EXECUTED: {result['reason']}")

    print()


if __name__ == "__main__":
    main()
