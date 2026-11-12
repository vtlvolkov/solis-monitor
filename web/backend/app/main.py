"""Read-only web dashboard API. Talks to InfluxDB only, never to the inverter.

create_app wires the pieces: the shared Context (context.py), the auth middlewares, the routers in routes/
(data, settings, updates), the optional MCP server and the built frontend."""
import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .context import Context, InfluxUnavailable
from .control import ControlClient
from .routes import backups, data, settings as settings_routes, updates

log = logging.getLogger("solis-web")

PUBLIC_ICONS = {"/favicon.svg", "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest"}


def _now():
    return datetime.now(timezone.utc)


def create_app(repo=None, settings: Settings | None = None, now=_now, control=None, releases=None) -> FastAPI:
    s = settings or Settings()
    if repo is None:
        from .influx import InfluxRepo
        repo = InfluxRepo(s)
    if control is None:
        control = ControlClient(s.poller_url, s.control_token)
    ctx = Context(s, repo, control, now)

    data_router, api = data.build(ctx)
    updates_router, periodic_check = updates.build(ctx, releases)
    mcp_state: dict = {}

    @asynccontextmanager
    async def lifespan(_app):
        # Look for a new release now and then (release installs only), so the badge needs no click.
        checker = asyncio.create_task(periodic_check()) if s.solis_version and s.update_check_hours > 0 and not s.updates_managed_by else None
        try:
            manager = mcp_state.get("manager")
            if manager is None:
                yield
            else:
                async with manager.run():             # the MCP transport needs its task group
                    yield
        finally:
            if checker:
                checker.cancel()

    app = FastAPI(title="Solis web", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(InfluxUnavailable)
    async def _influx_down(_req, exc):
        return JSONResponse({"detail": "InfluxDB unavailable", "error": str(exc)[:200]}, status_code=503)

    # ------------------------------------------------------------------ auth
    # /mcp has its own bearer token (MCP_TOKEN) so an agent never holds the dashboard password,
    # which can change inverter settings. Everything else uses optional Basic auth, either on every
    # route or (WEB_AUTH_SETTINGS_ONLY) only on /api/settings*, which makes the browser ask for the
    # password when the Settings page loads and nowhere else.
    @app.middleware("http")
    async def mcp_auth(request: Request, call_next):
        if request.url.path.rstrip("/") == "/mcp":
            token = request.headers.get("authorization", "")
            # bytes: compare_digest raises TypeError on non-ASCII str (headers are decoded as latin-1)
            if not s.mcp_token or not secrets.compare_digest(token.encode("latin-1"), f"Bearer {s.mcp_token}".encode()):
                return Response("unauthorized", status_code=401)
        return await call_next(request)

    if s.basic_auth:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            path = request.url.path
            # Icons stay public: iOS fetches the home-screen icon without the Basic auth credentials.
            if path in ("/healthz", "/mcp", "/mcp/") or path in PUBLIC_ICONS:
                return await call_next(request)
            if s.auth_settings_only and path != "/api/settings" and not path.startswith("/api/settings/"):
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if not await asyncio.to_thread(ctx.login.verify_header, header):      # a hash check takes ~15 ms, remembered afterwards
                return Response("unauthorized", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="solis", charset="UTF-8"'})
            return await call_next(request)

    # ------------------------------------------------------------------ routes
    app.include_router(data_router)
    app.include_router(settings_routes.build(ctx))
    app.include_router(updates_router)
    app.include_router(backups.build(ctx))

    # ------------------------------------------------------------------ MCP (read-only, for agents)
    if s.mcp_token:
        from .mcp_server import DataApi, build_mcp

        async def mcp_history(fields, rng, every, agg):
            return await api["history"](",".join(fields), rng, every, agg)

        mcp = build_mcp(DataApi(
            live=api["live"], history=mcp_history, events=lambda d: api["events"](d),
            energy_summary=api["energy_summary"], cost=api["cost"],
            devices=lambda: ctx.poller(control.devices), schedule=lambda: ctx.poller(control.cached_settings)))
        app.router.routes.extend(mcp.streamable_http_app().routes)   # before the static mount below
        mcp_state["manager"] = mcp.session_manager

    # ------------------------------------------------------------------ frontend
    if os.path.isdir(s.static_dir):
        app.mount("/", StaticFiles(directory=s.static_dir, html=True), name="static")

    return app


def app_factory():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return create_app()
