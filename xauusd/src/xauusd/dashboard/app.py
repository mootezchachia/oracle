"""Mobile-friendly async dashboard.

Built on ``aiohttp.web`` — the same library already used for outbound HTTP, so
the dashboard adds no dependency and shares the event loop with the monitor.

Routes
------
``GET  /``              the dashboard page
``GET  /api/snapshot``  full state as JSON
``GET  /api/signals``   recent signals from the journal
``GET  /api/health``    liveness/readiness probe
``GET  /ws``            websocket push, one message per refresh interval
``POST /tv/webhook``    TradingView inbound context

The snapshot endpoint is the contract; the page is just one consumer of it, so
you can point Grafana, a mobile app or a shell script at the same JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from aiohttp import WSMsgType, web

from ..config import Config
from ..logging_setup import get_logger
from ..integrations.tradingview import TradingViewBridge

if TYPE_CHECKING:  # pragma: no cover
    from ..runner import Sentinel

log = get_logger("dashboard")

STATIC_DIR = Path(__file__).parent / "static"

# Typed application keys — aiohttp warns on bare string keys because they can
# collide silently between middlewares.
SENTINEL_KEY: web.AppKey["Sentinel"] = web.AppKey("sentinel")
BRIDGE_KEY: web.AppKey[TradingViewBridge] = web.AppKey("bridge")
SOCKETS_KEY: web.AppKey[set] = web.AppKey("websockets")


def _json(payload: Any, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, dumps=lambda o: json.dumps(o, default=str))


def build_app(sentinel: "Sentinel", config: Config) -> web.Application:
    app = web.Application()
    bridge = TradingViewBridge(config)
    app[SENTINEL_KEY] = sentinel
    app[BRIDGE_KEY] = bridge
    app[SOCKETS_KEY] = set()
    refresh = float(config.get("dashboard.refresh_seconds", 5))

    async def index(_request: web.Request) -> web.StreamResponse:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            return web.Response(text="dashboard assets missing", status=500)
        return web.FileResponse(page)

    async def snapshot(_request: web.Request) -> web.Response:
        data = sentinel.snapshot()
        data["tradingview"] = bridge.health()
        return _json(data)

    async def signals(request: web.Request) -> web.Response:
        try:
            limit = min(int(request.query.get("limit", 25)), 200)
        except ValueError:
            limit = 25
        return _json({"signals": sentinel.journal.recent(limit)})

    async def health(_request: web.Request) -> web.Response:
        store = sentinel.collector.store
        ready = store.ready(minimum=60)
        return _json(
            {
                "status": "ok" if ready else "warming_up",
                "ready": ready,
                "cycles": sentinel.cycles,
                "signals": sentinel.signals_emitted,
                "provider": sentinel.collector.provider.name if sentinel.collector.provider else None,
                "error": sentinel.last_error,
            },
            status=200 if ready else 503,
        )

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        request.app[SOCKETS_KEY].add(ws)
        try:
            await ws.send_json(sentinel.snapshot(), dumps=lambda o: json.dumps(o, default=str))
            while not ws.closed:
                # Push on a timer; also drain inbound frames so pings work.
                with contextlib.suppress(asyncio.TimeoutError):
                    msg = await ws.receive(timeout=refresh)
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                if ws.closed:
                    break
                await ws.send_json(sentinel.snapshot(), dumps=lambda o: json.dumps(o, default=str))
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            request.app[SOCKETS_KEY].discard(ws)
            with contextlib.suppress(Exception):
                await ws.close()
        return ws

    async def tv_webhook(request: web.Request) -> web.Response:
        if not bridge.enabled:
            return _json({"error": "webhook disabled"}, status=404)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            raw = (await request.text())[:500]
            return _json({"error": "invalid JSON", "received": raw}, status=400)
        if not isinstance(payload, dict):
            return _json({"error": "payload must be a JSON object"}, status=400)

        supplied = payload.get("secret") or request.headers.get("X-Webhook-Secret")
        if not bridge.authorised(supplied):
            log.warning("rejected unauthorised TradingView webhook from %s", request.remote)
            return _json({"error": "unauthorised"}, status=401)

        external = bridge.ingest(payload)
        if external is None:
            return _json({"error": "no usable direction in payload"}, status=422)
        return _json({"accepted": True, "context": external.to_dict()})

    app.router.add_get("/", index)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/api/signals", signals)
    app.router.add_get("/api/health", health)
    app.router.add_get("/ws", websocket)
    app.router.add_post(bridge.path, tv_webhook)
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR)

    async def _close_sockets(app: web.Application) -> None:
        for ws in set(app[SOCKETS_KEY]):
            with contextlib.suppress(Exception):
                await ws.close(code=1001, message=b"server shutdown")

    app.on_shutdown.append(_close_sockets)
    return app


async def start_dashboard(sentinel: "Sentinel", config: Config) -> web.AppRunner:
    """Start the dashboard server and return its runner for later cleanup."""
    app = build_app(sentinel, config)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    host = str(config.get("dashboard.host", "0.0.0.0"))
    port = int(config.get("dashboard.port", 8787))
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("dashboard listening on http://%s:%d", host, port)
    return runner
