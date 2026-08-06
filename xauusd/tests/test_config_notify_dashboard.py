"""Configuration, alert formatting, TradingView bridge and the dashboard API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from xauusd.config import Config, load_config
from xauusd.integrations.tradingview import TradingViewBridge
from xauusd.models import (
    UTC,
    Direction,
    Evidence,
    MarketContext,
    RiskPlan,
    Signal,
)
from xauusd.notify.channels import DiscordChannel, TelegramChannel
from xauusd.notify.formatter import (
    format_signal_discord,
    format_signal_html,
    format_signal_text,
    format_tradingview_alert,
)

TS = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)


@pytest.fixture
def signal() -> Signal:
    plan = RiskPlan(
        entry=4262.10, stop_loss=4256.40, take_profits=[4270.0, 4278.0, 4292.0],
        risk_per_unit=5.70, rr_targets=[1.39, 2.79, 5.25], lot_size=0.17,
        risk_amount=97.0, risk_percent=0.97, break_even_price=4267.80,
        trail_trigger_price=4270.65, trail_distance=3.4, atr=2.9,
        partial_percents=[50, 30, 20],
    )
    return Signal(
        id="abc123", ts=TS, symbol="XAUUSD", direction=Direction.BUY,
        confidence=93.4, raw_score=86.0, probability=66.0, risk=plan,
        evidence=[
            Evidence("HTF_TREND", "H4 bullish", Direction.BUY, 10.0),
            Evidence("BOS", "H1 break of structure", Direction.BUY, 8.0),
            Evidence("KILL_ZONE", "London Open", Direction.BUY, 6.0),
            Evidence("CORRELATION", "DXY falling", Direction.BUY, 6.0),
            Evidence("FVG", "Bullish FVG", Direction.BUY, 7.0),
            Evidence("ORDER_BLOCK", "Order block respected", Direction.BUY, 8.0),
        ],
        context=MarketContext(ts=TS, price=4262.10, session="LONDON/NY OVERLAP"),
        notes=["Risk 0.97% — 0.17 lots"],
    )


class TestConfig:
    def test_dotted_access_with_defaults(self, config):
        assert config.get("signals.min_confidence") == 90.0
        assert config.get("does.not.exist", "fallback") == "fallback"

    def test_missing_key_without_default_raises(self, config):
        with pytest.raises(KeyError):
            config.get("nope.at.all")

    def test_section_returns_a_scoped_view(self, config):
        risk = config.section("risk")
        assert risk.get("max_risk_percent") == 1.0

    def test_override_is_deep_and_non_destructive(self, config):
        strict = config.override({"signals": {"min_confidence": 97.0}})
        assert strict.get("signals.min_confidence") == 97.0
        assert strict.get("signals.min_rr") == config.get("signals.min_rr")
        assert config.get("signals.min_confidence") == 90.0

    def test_environment_overrides_are_typed(self, monkeypatch, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text("signals:\n  min_confidence: 90\n  require_kill_zone: true\n", encoding="utf-8")
        monkeypatch.setenv("XAUUSD_SIGNALS__MIN_CONFIDENCE", "95.5")
        monkeypatch.setenv("XAUUSD_SIGNALS__REQUIRE_KILL_ZONE", "false")
        monkeypatch.setenv("XAUUSD_NOTIFY__TELEGRAM__BOT_TOKEN", "123:abc")
        cfg = load_config(path)
        assert cfg.get("signals.min_confidence") == 95.5
        assert cfg.get("signals.require_kill_zone") is False
        assert cfg.get("notify.telegram.bot_token") == "123:abc"

    def test_shipped_config_is_internally_consistent(self, config):
        """A TP1 that can never satisfy min_rr would silence the system forever."""
        multiples = config.get("risk.tp_r_multiples")
        shares = config.get("risk.partial_percents")
        blended = sum(m * s for m, s in zip(multiples, shares)) / sum(shares)
        assert blended >= config.get("signals.min_rr")
        assert len(multiples) == len(shares) == 3


class TestFormatting:
    def test_text_alert_leads_with_direction_and_levels(self, signal):
        text = format_signal_text(signal)
        assert "BUY XAUUSD" in text
        assert "4262.10" in text
        assert "4256.40" in text
        assert "TP1" in text and "TP3" in text
        assert "93%" in text
        assert "H4 bullish" in text

    def test_html_alert_is_valid_telegram_markup(self, signal):
        html = format_signal_html(signal)
        assert html.count("<b>") == html.count("</b>")
        assert html.count("<code>") == html.count("</code>")
        assert "BUY XAUUSD" in html

    def test_discord_embed_shape(self, signal):
        payload = format_signal_discord(signal)
        embed = payload["embeds"][0]
        assert embed["color"] == 0x2ECC71
        assert {f["name"] for f in embed["fields"]} >= {"Levels", "Risk", "Confluence"}
        json.dumps(payload)          # must be serialisable for the webhook

    def test_sell_embed_is_red(self, signal):
        signal.direction = Direction.SELL
        assert format_signal_discord(signal)["embeds"][0]["color"] == 0xE74C3C

    def test_tradingview_payload_is_machine_readable(self, signal):
        payload = format_tradingview_alert(signal)
        assert payload["action"] == "buy"
        assert payload["entry"] == 4262.10
        assert payload["take_profits"] == [4270.0, 4278.0, 4292.0]
        json.dumps(payload)


class TestChannels:
    def test_channels_disable_themselves_without_credentials(self, config):
        enabled = config.override({
            "notify": {"telegram": {"enabled": True}, "discord": {"enabled": True}}
        })
        assert TelegramChannel(enabled).enabled is False
        assert DiscordChannel(enabled).enabled is False

    def test_channels_enable_with_credentials(self, config):
        ready = config.override({
            "notify": {
                "telegram": {"enabled": True, "bot_token": "1:x", "chat_id": "42"},
                "discord": {"enabled": True, "webhook_url": "https://example.invalid/hook"},
            }
        })
        assert TelegramChannel(ready).enabled is True
        assert DiscordChannel(ready).enabled is True


class TestTradingViewBridge:
    def test_secret_is_enforced_when_configured(self, config):
        secured = config.override({"tradingview": {"webhook_secret": "s3cret"}})
        bridge = TradingViewBridge(secured)
        assert bridge.authorised("s3cret") is True
        assert bridge.authorised("wrong") is False
        assert bridge.authorised(None) is False

    def test_open_when_no_secret_is_set(self, config):
        assert TradingViewBridge(config).authorised(None) is True

    def test_ingest_normalises_direction_synonyms(self, config):
        bridge = TradingViewBridge(config)
        for raw, expected in (("long", Direction.BUY), ("SELL", Direction.SELL),
                              ("bullish", Direction.BUY)):
            result = bridge.ingest({"action": raw}, TS)
            assert result is not None and result.direction is expected

    def test_useless_payload_is_rejected(self, config):
        assert TradingViewBridge(config).ingest({"action": "???"}, TS) is None

    def test_stale_context_is_not_served(self, config):
        bridge = TradingViewBridge(config)
        bridge.ingest({"action": "buy"}, TS)
        assert bridge.context(TS + timedelta(minutes=5)) is not None
        assert bridge.context(TS + timedelta(hours=4)) is None

    def test_secret_is_never_echoed_back(self, config):
        bridge = TradingViewBridge(config)
        external = bridge.ingest({"action": "buy", "secret": "hunter2"}, TS)
        assert "secret" not in external.payload


class TestDashboardAPI:
    """The snapshot endpoint is the contract; the HTML page is one consumer."""

    @pytest.fixture
    def client(self, config, aiohttp_client=None):
        pytest.importorskip("aiohttp")
        return None

    async def test_endpoints_respond(self, config):
        from aiohttp.test_utils import TestClient, TestServer

        from xauusd.dashboard.app import build_app
        from xauusd.runner import Sentinel

        sentinel = Sentinel(config)
        app = build_app(sentinel, config)

        async with TestClient(TestServer(app)) as client:
            health = await client.get("/api/health")
            assert health.status in (200, 503)
            body = await health.json()
            assert "status" in body

            snapshot = await client.get("/api/snapshot")
            assert snapshot.status == 200
            data = await snapshot.json()
            assert data["symbol"] == "XAUUSD"
            assert "session" in data and "config" in data

            signals = await client.get("/api/signals")
            assert signals.status == 200
            assert "signals" in await signals.json()

            page = await client.get("/")
            assert page.status == 200
            assert "XAUUSD" in await page.text()

    async def test_webhook_rejects_bad_input(self, config):
        from aiohttp.test_utils import TestClient, TestServer

        from xauusd.dashboard.app import build_app
        from xauusd.runner import Sentinel

        secured = config.override({"tradingview": {"webhook_secret": "s3cret"}})
        app = build_app(Sentinel(secured), secured)

        async with TestClient(TestServer(app)) as client:
            unauthorised = await client.post("/tv/webhook", json={"action": "buy"})
            assert unauthorised.status == 401

            malformed = await client.post(
                "/tv/webhook", data="not json", headers={"Content-Type": "application/json"}
            )
            assert malformed.status == 400

            accepted = await client.post(
                "/tv/webhook", json={"action": "buy", "secret": "s3cret", "note": "H4 bias"}
            )
            assert accepted.status == 200
            assert (await accepted.json())["accepted"] is True
