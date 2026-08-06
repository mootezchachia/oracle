"""Alert formatting for Telegram, Discord and the dashboard.

An alert has to be readable on a phone screen, in a hurry, while price is
moving. That means: direction first, levels second, reasoning third, and never
more than a screen's worth. Everything else belongs on the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import Decision, Direction, Signal, humanize_delta
from ..sessions.calendar import SessionState

_ARROW = {Direction.BUY: "🟢", Direction.SELL: "🔴", Direction.NEUTRAL: "⚪"}


def _levels_block(signal: Signal) -> list[str]:
    plan = signal.risk
    lines = [
        f"Entry: {plan.entry:.2f}",
        f"SL:    {plan.stop_loss:.2f}   (${plan.risk_per_unit:.2f})",
    ]
    for i, (price, rr) in enumerate(zip(plan.take_profits, plan.rr_targets), start=1):
        lines.append(f"TP{i}:   {price:.2f}   ({rr:.1f}R)")
    return lines


def format_signal_text(signal: Signal) -> str:
    """Plain-text alert. Used by Telegram (as HTML) and as the Discord fallback."""
    plan = signal.risk
    icon = _ARROW[signal.direction]
    lines = [
        f"{icon} {signal.direction.value} {signal.symbol}",
        "",
        *_levels_block(signal),
        "",
        f"Confidence: {signal.confidence:.0f}%   (P(TP1) ≈ {signal.probability:.0f}%)",
        f"Risk:       {plan.risk_percent:.2f}%   {plan.lot_size:.2f} lots",
        f"Session:    {signal.context.session}",
        "",
        "Reasons:",
    ]
    lines.extend(f"  ✔ {reason}" for reason in signal.reasons[:10])

    if signal.notes:
        lines.append("")
        lines.append("Management:")
        lines.extend(f"  • {note}" for note in signal.notes[:6])

    if signal.context.next_event:
        event = signal.context.next_event
        lines.append("")
        lines.append(f"Next event: {event.get('title')} ({event.get('currency')}) at {event.get('ts', '')[11:16]} UTC")

    lines.append("")
    lines.append(f"id {signal.id} · {signal.ts:%Y-%m-%d %H:%M} UTC")
    return "\n".join(lines)


def format_signal_html(signal: Signal) -> str:
    """Telegram HTML — the only markup Telegram parses reliably."""
    plan = signal.risk
    icon = _ARROW[signal.direction]
    reasons = "\n".join(f"✔ {reason}" for reason in signal.reasons[:10])
    targets = "\n".join(
        f"<b>TP{i}</b>  <code>{price:.2f}</code>  ({rr:.1f}R)"
        for i, (price, rr) in enumerate(zip(plan.take_profits, plan.rr_targets), start=1)
    )
    return (
        f"{icon} <b>{signal.direction.value} {signal.symbol}</b>\n\n"
        f"<b>Entry</b> <code>{plan.entry:.2f}</code>\n"
        f"<b>SL</b>    <code>{plan.stop_loss:.2f}</code>  (${plan.risk_per_unit:.2f})\n"
        f"{targets}\n\n"
        f"<b>Confidence</b> {signal.confidence:.0f}%  ·  P(TP1) ≈ {signal.probability:.0f}%\n"
        f"<b>Risk</b> {plan.risk_percent:.2f}%  ·  {plan.lot_size:.2f} lots\n"
        f"<b>Session</b> {signal.context.session}\n\n"
        f"<b>Reasons</b>\n{reasons}\n\n"
        f"<i>id {signal.id} · {signal.ts:%H:%M} UTC</i>"
    )


def format_signal_discord(signal: Signal) -> dict[str, Any]:
    """A Discord embed — richer layout, same information hierarchy."""
    plan = signal.risk
    colour = 0x2ECC71 if signal.direction is Direction.BUY else 0xE74C3C
    targets = "\n".join(
        f"**TP{i}** `{price:.2f}` ({rr:.1f}R)"
        for i, (price, rr) in enumerate(zip(plan.take_profits, plan.rr_targets), start=1)
    )
    reasons = "\n".join(f"✔ {reason}" for reason in signal.reasons[:10]) or "—"

    return {
        "embeds": [
            {
                "title": f"{_ARROW[signal.direction]} {signal.direction.value} {signal.symbol}",
                "color": colour,
                "description": f"**Confidence {signal.confidence:.0f}%** · P(TP1) ≈ {signal.probability:.0f}%",
                "fields": [
                    {
                        "name": "Levels",
                        "value": f"**Entry** `{plan.entry:.2f}`\n**SL** `{plan.stop_loss:.2f}` (${plan.risk_per_unit:.2f})\n{targets}",
                        "inline": True,
                    },
                    {
                        "name": "Risk",
                        "value": (
                            f"{plan.risk_percent:.2f}% · {plan.lot_size:.2f} lots\n"
                            f"BE at `{plan.break_even_price:.2f}`\n"
                            f"Trail after `{plan.trail_trigger_price:.2f}`"
                        ),
                        "inline": True,
                    },
                    {"name": "Confluence", "value": reasons, "inline": False},
                    {
                        "name": "Context",
                        "value": (
                            f"Session: {signal.context.session}\n"
                            f"Volatility: {signal.context.volatility_regime.value}\n"
                            f"News: {signal.context.news_severity.value}"
                        ),
                        "inline": False,
                    },
                ],
                "footer": {"text": f"XAUUSD Sentinel · id {signal.id}"},
                "timestamp": signal.ts.isoformat(),
            }
        ]
    }


def format_heartbeat(
    decision: Decision, session: SessionState, uptime_seconds: float
) -> str:
    """Periodic 'still watching' digest — no trade, but proof of life."""
    context = decision.context
    lines = [
        "🩺 XAUUSD Sentinel — status",
        "",
        f"Price:      {context.price:.2f}",
        f"Session:    {session.primary}",
        f"Bias:       {decision.direction.value}",
        f"Confidence: {decision.confidence:.0f}%",
        f"Volatility: {context.volatility_regime.value}",
        f"Uptime:     {humanize_delta_seconds(uptime_seconds)}",
    ]
    if decision.vetoes:
        lines.append(f"Standing aside: {decision.vetoes[0].reason}")
    elif decision.reason:
        lines.append(decision.reason)

    if session.next_kill_zone:
        lines.append(f"Next kill zone: {session.next_kill_zone.name} in {session.next_kill_zone.human}")
    if context.next_event:
        lines.append(f"Next event: {context.next_event.get('title')} ({context.next_event.get('currency')})")
    return "\n".join(lines)


def humanize_delta_seconds(seconds: float) -> str:
    from datetime import timedelta

    return humanize_delta(timedelta(seconds=seconds))


def format_tradingview_alert(signal: Signal) -> dict[str, Any]:
    """Compact JSON payload for TradingView / broker webhook consumers."""
    plan = signal.risk
    return {
        "ticker": signal.symbol,
        "action": signal.direction.value.lower(),
        "entry": round(plan.entry, 2),
        "stop_loss": round(plan.stop_loss, 2),
        "take_profits": [round(tp, 2) for tp in plan.take_profits],
        "risk_percent": round(plan.risk_percent, 2),
        "lots": round(plan.lot_size, 2),
        "confidence": round(signal.confidence, 1),
        "probability": round(signal.probability, 1),
        "reasons": signal.reasons[:10],
        "session": signal.context.session,
        "id": signal.id,
        "timestamp": signal.ts.isoformat(),
    }
