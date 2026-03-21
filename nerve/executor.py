"""ORACLE — Trade Executor for Virtual Polymarket Account
Manages the virtual $10K portfolio. All trades are tracked in virtual_portfolio.json
which is shared with the dashboard API.

Features:
  1. Position sizing (Kelly criterion + fixed fractional)
  2. Stop-loss / take-profit management
  3. Confidence-gated execution (only trade when signals agree)
  4. Virtual account with cash tracking (shared with dashboard)
  5. Execution log with P&L tracking
  6. Rate limiting and cooldown between trades
"""

import json
import math
import time
import requests
from datetime import datetime, timezone
from config import *

TRADES_LOG = DATA_DIR / "trades.jsonl"
VIRTUAL_PORTFOLIO = DATA_DIR / "virtual_portfolio.json"
EXECUTOR_CONFIG = DATA_DIR / "executor_config.json"

GAMMA_API = "https://gamma-api.polymarket.com"


# ─── Default Configuration ────────────────────────────────────────

DEFAULT_CONFIG = {
    "max_position_usd": 500,       # max $ per position (matching existing trades)
    "max_daily_loss_usd": 1000,    # stop trading after this daily loss
    "min_confidence": 0.50,         # minimum fusion confidence to trade
    "min_edge_pct": 5.0,           # minimum edge % to trade
    "min_agreement": 0.5,          # minimum signal agreement
    "take_profit_pct": 15.0,       # take profit at +15%
    "stop_loss_pct": 10.0,         # stop loss at -10%
    "cooldown_seconds": 300,       # 5 min between trades on same market
    "kelly_fraction": 0.25,        # quarter-Kelly for safety
    "default_position_usd": 500,   # default position size
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


# ─── Virtual Portfolio ────────────────────────────────────────────

def load_portfolio() -> dict:
    """Load the shared virtual portfolio."""
    if VIRTUAL_PORTFOLIO.exists():
        with open(VIRTUAL_PORTFOLIO) as f:
            return json.load(f)
    return {
        "account": {"starting_balance": 10000, "cash": 10000},
        "trades": [],
    }


def save_portfolio(portfolio: dict):
    """Save the shared virtual portfolio (read by dashboard API)."""
    with open(VIRTUAL_PORTFOLIO, "w") as f:
        json.dump(portfolio, f, indent=2)


# ─── Kelly Criterion ─────────────────────────────────────────────

def kelly_size(win_prob: float, win_payout: float, loss_payout: float,
               fraction: float = 0.25) -> float:
    """Calculate position size using fractional Kelly criterion."""
    if win_payout <= 0 or loss_payout <= 0:
        return 0.0
    b = win_payout / loss_payout
    p = win_prob
    q = 1 - p
    kelly = (b * p - q) / b
    kelly = max(0.0, kelly)
    return kelly * fraction


# ─── Price Fetching ───────────────────────────────────────────────

def fetch_market_price(slug: str) -> dict:
    """Fetch current price for a Polymarket market by slug."""
    try:
        r = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
        r.raise_for_status()
        markets = r.json()
        if markets and len(markets) > 0:
            m = markets[0]
            prices = json.loads(m.get("outcomePrices", "[]"))
            return {
                "yes": float(prices[0]) if len(prices) > 0 else None,
                "no": float(prices[1]) if len(prices) > 1 else None,
                "volume": float(m.get("volume", 0) or 0),
                "question": m.get("question", ""),
            }
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Market not found"}


# ─── Trade Execution ──────────────────────────────────────────────

class TradeExecutor:
    """Manages virtual Polymarket trades."""

    def __init__(self):
        self.config = load_config()
        self.portfolio = load_portfolio()
        self.last_trade_time = {}

    @property
    def cash(self) -> float:
        return self.portfolio["account"]["cash"]

    @property
    def trades(self) -> list:
        return self.portfolio.get("trades", [])

    @property
    def open_trades(self) -> list:
        return [t for t in self.trades if t.get("status", "open") == "open"]

    @property
    def closed_trades(self) -> list:
        return [t for t in self.trades if t.get("status") == "closed"]

    def next_trade_number(self) -> int:
        if not self.trades:
            return 1
        return max(t.get("number", 0) for t in self.trades) + 1

    def should_trade(self, fusion_result: dict) -> tuple:
        """Check if fusion result meets trading criteria."""
        conf = fusion_result.get("confidence", 0)
        edge = fusion_result.get("edge_pct", 0)
        agreement = fusion_result.get("agreement", 0)
        slug = fusion_result.get("slug", fusion_result.get("market_id", ""))

        if conf < self.config["min_confidence"]:
            return False, f"Confidence {conf:.1%} < {self.config['min_confidence']:.1%}"

        if edge < self.config["min_edge_pct"]:
            return False, f"Edge {edge:.1f}% < {self.config['min_edge_pct']}%"

        if agreement < self.config["min_agreement"]:
            return False, f"Agreement {agreement:.1%} < {self.config['min_agreement']:.1%}"

        # Check cash
        size = self.calculate_size(fusion_result)
        if size > self.cash:
            return False, f"Insufficient cash: need ${size:.2f}, have ${self.cash:.2f}"

        # Check for existing position on same market
        existing = [t for t in self.open_trades if t.get("slug") == slug]
        if existing:
            return False, f"Already have open position on {slug}"

        # Cooldown
        if slug in self.last_trade_time:
            elapsed = time.time() - self.last_trade_time[slug]
            if elapsed < self.config["cooldown_seconds"]:
                return False, f"Cooldown: {self.config['cooldown_seconds'] - elapsed:.0f}s remaining"

        return True, "All criteria met"

    def calculate_size(self, fusion_result: dict) -> float:
        """Calculate position size in USD using Kelly + account sizing."""
        conf = fusion_result.get("confidence", 0)
        edge = fusion_result.get("edge_pct", 0)

        win_prob = 0.5 + (conf * 0.3)
        win_payout = edge / 100
        loss_payout = 1.0

        kelly_pct = kelly_size(win_prob, win_payout, loss_payout,
                               self.config["kelly_fraction"])

        # Scale Kelly by available cash
        kelly_dollars = kelly_pct * self.cash

        # Clamp between minimum useful and max position
        size = min(self.config["max_position_usd"], kelly_dollars)
        size = max(0.0, size)

        # Use default position size if Kelly gives something reasonable
        if size > 50:
            size = min(size, self.config["default_position_usd"])

        return round(size, 2)

    def execute(self, slug: str, question: str, side: str, size_usd: float = None,
                fusion_result: dict = None) -> dict:
        """Execute a virtual trade on Polymarket.

        Args:
            slug: Polymarket market slug
            question: market question text
            side: "yes" or "no"
            size_usd: position size (or auto-calculate from fusion)
            fusion_result: signal fusion output (optional, for logging)
        """
        # Fetch current price
        price_data = fetch_market_price(slug)
        if "error" in price_data:
            return {"executed": False, "reason": f"Price fetch failed: {price_data['error']}"}

        entry_price = price_data.get(side.lower())
        if entry_price is None or entry_price <= 0:
            return {"executed": False, "reason": f"Invalid {side} price: {entry_price}"}

        # Auto-size from fusion or use provided/default
        if size_usd is None:
            if fusion_result:
                size_usd = self.calculate_size(fusion_result)
            else:
                size_usd = self.config["default_position_usd"]

        # Check cash
        if size_usd > self.cash:
            return {"executed": False, "reason": f"Insufficient cash: need ${size_usd:.2f}, have ${self.cash:.2f}"}

        if size_usd < 1:
            return {"executed": False, "reason": f"Position too small: ${size_usd:.2f}"}

        # Calculate shares
        shares = round(size_usd / entry_price, 2)

        # Build trade record (compatible with existing portfolio format)
        trade_number = self.next_trade_number()
        trade = {
            "id": trade_number,
            "number": trade_number,
            "slug": slug,
            "question": question,
            "side": side.lower(),
            "entry_price": round(entry_price, 4),
            "shares": shares,
            "invested": round(size_usd, 2),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "status": "open",
            "source": "oracle_v2",
            "take_profit_pct": self.config["take_profit_pct"],
            "stop_loss_pct": self.config["stop_loss_pct"],
        }

        if fusion_result:
            trade["entry_confidence"] = fusion_result.get("confidence", 0)
            trade["entry_edge_pct"] = fusion_result.get("edge_pct", 0)
            trade["entry_agreement"] = fusion_result.get("agreement", 0)
            trade["recommendation"] = fusion_result.get("recommendation", "")

        # Deduct cash
        self.portfolio["account"]["cash"] = round(self.cash - size_usd, 2)

        # Add to trades
        self.portfolio.setdefault("trades", []).append(trade)

        # Save (this is what the dashboard reads)
        save_portfolio(self.portfolio)

        # Also log to trades.jsonl for history
        append_jsonl(TRADES_LOG, {**trade, "event": "open", "timestamp": now_iso()})

        # Set cooldown
        self.last_trade_time[slug] = time.time()

        return {
            "executed": True,
            "trade": trade,
            "cash_remaining": self.portfolio["account"]["cash"],
        }

    def close_trade(self, trade_number: int, reason: str = "manual") -> dict:
        """Close a trade by number, fetching current price."""
        trade = None
        for t in self.trades:
            if t.get("number") == trade_number and t.get("status", "open") == "open":
                trade = t
                break

        if not trade:
            return {"closed": False, "reason": f"Trade #{trade_number} not found or already closed"}

        # Fetch current price
        price_data = fetch_market_price(trade["slug"])
        if "error" in price_data:
            return {"closed": False, "reason": f"Price fetch failed: {price_data['error']}"}

        current_price = price_data.get(trade["side"])
        if current_price is None:
            return {"closed": False, "reason": "Could not get current price"}

        current_value = round(trade["shares"] * current_price, 2)
        pnl = round(current_value - trade["invested"], 2)
        pnl_pct = round((current_value / trade["invested"] - 1) * 100, 2) if trade["invested"] > 0 else 0

        # Update trade
        trade["status"] = "closed"
        trade["closed_at"] = now_iso()
        trade["exit_price"] = round(current_price, 4)
        trade["current_value"] = current_value
        trade["pnl"] = pnl
        trade["pnl_pct"] = pnl_pct
        trade["close_reason"] = reason

        # Return cash (proceeds from closing)
        self.portfolio["account"]["cash"] = round(self.cash + current_value, 2)

        # Save
        save_portfolio(self.portfolio)
        append_jsonl(TRADES_LOG, {**trade, "event": "close", "timestamp": now_iso()})

        return {
            "closed": True,
            "trade": trade,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "cash_remaining": self.portfolio["account"]["cash"],
        }

    def check_exits(self) -> list:
        """Check all open positions for take-profit or stop-loss triggers."""
        closed = []
        for trade in self.open_trades:
            price_data = fetch_market_price(trade["slug"])
            if "error" in price_data:
                continue

            current_price = price_data.get(trade["side"])
            if current_price is None:
                continue

            current_value = trade["shares"] * current_price
            pnl_pct = ((current_value / trade["invested"]) - 1) * 100 if trade["invested"] > 0 else 0

            tp = trade.get("take_profit_pct", self.config["take_profit_pct"])
            sl = trade.get("stop_loss_pct", self.config["stop_loss_pct"])

            if pnl_pct >= tp:
                result = self.close_trade(trade["number"], f"Take profit: {pnl_pct:.1f}%")
                if result.get("closed"):
                    closed.append(result)
            elif pnl_pct <= -sl:
                result = self.close_trade(trade["number"], f"Stop loss: {pnl_pct:.1f}%")
                if result.get("closed"):
                    closed.append(result)

        return closed

    def execute_from_fusion(self, fusion_result: dict, slug: str, question: str) -> dict:
        """Execute a trade from signal fusion output (the standard pipeline entry point)."""
        should, reason = self.should_trade({**fusion_result, "slug": slug})
        if not should:
            return {"executed": False, "reason": reason}

        direction = fusion_result.get("direction", 0)
        side = "yes" if direction > 0 else "no"

        return self.execute(
            slug=slug,
            question=question,
            side=side,
            fusion_result=fusion_result,
        )

    def summary(self) -> dict:
        """Account summary for display."""
        open_pos = self.open_trades
        closed_pos = self.closed_trades

        total_invested = sum(t["invested"] for t in open_pos)
        total_pnl = sum(t.get("pnl", 0) for t in closed_pos)
        wins = sum(1 for t in closed_pos if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed_pos if t.get("pnl", 0) < 0)

        return {
            "starting_balance": self.portfolio["account"]["starting_balance"],
            "cash": self.cash,
            "invested": round(total_invested, 2),
            "open_positions": len(open_pos),
            "closed_trades": len(closed_pos),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(wins / max(wins + losses, 1), 3),
            "wins": wins,
            "losses": losses,
            "total_trades": len(self.trades),
        }


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    print_banner("Virtual Trade Executor")

    executor = TradeExecutor()
    s = executor.summary()

    print(f"  ═══ VIRTUAL POLYMARKET ACCOUNT ═══")
    print(f"  Starting Balance: ${s['starting_balance']:,.2f}")
    print(f"  Cash:             ${s['cash']:,.2f}")
    print(f"  Invested:         ${s['invested']:,.2f}")
    print(f"  Open Positions:   {s['open_positions']}")
    print(f"  Closed Trades:    {s['closed_trades']}")
    print(f"  Total P&L:        ${s['total_pnl']:+,.2f}")
    print(f"  Win Rate:         {s['win_rate']:.1%}")
    print(f"  Total Trades:     {s['total_trades']}")

    # Show open positions with live prices
    print(f"\n  ═══ OPEN POSITIONS ═══")
    for trade in executor.open_trades:
        price_data = fetch_market_price(trade["slug"])
        current = price_data.get(trade["side"])

        if current is not None:
            value = trade["shares"] * current
            pnl = value - trade["invested"]
            pnl_pct = ((value / trade["invested"]) - 1) * 100
            color = "+" if pnl >= 0 else ""
            print(f"  #{trade['number']:<3} {trade['side'].upper():<4} "
                  f"Entry: {trade['entry_price']:.2f}  Now: {current:.2f}  "
                  f"Value: ${value:,.0f}  P&L: {color}${pnl:,.0f} ({color}{pnl_pct:.1f}%)")
            print(f"        {trade['question'][:55]}...")
        else:
            print(f"  #{trade['number']:<3} {trade['side'].upper():<4} "
                  f"${trade['invested']:,.0f} — price unavailable")
            print(f"        {trade['question'][:55]}...")

    # Check for TP/SL triggers
    print(f"\n  ═══ CHECKING TP/SL ═══")
    closed = executor.check_exits()
    if closed:
        for c in closed:
            t = c["trade"]
            print(f"  CLOSED #{t['number']}: {t['close_reason']} "
                  f"P&L: ${t['pnl']:+,.2f} ({t['pnl_pct']:+.1f}%)")
    else:
        print(f"  No positions hit TP/SL thresholds")

    print()


if __name__ == "__main__":
    main()
