"""Read-only web dashboard API. Talks to InfluxDB only, never to the inverter."""
import asyncio
import ipaddress
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import shaping
from .tariff import Tariff, TariffError, TariffStore, next_boundary, validate, version_dict
from .auth import LoginError, LoginStore, TooManyAttempts
from .config import Settings
from .control import ControlClient, PollerUnavailable
import random
from .updates import Bridge, RateLimited, ReleaseFeed, ReleaseLookupError, delay_after_limit, is_newer, next_delay, parse_version
from .validation import (ENERGY_FIELDS, WINDOWS, parse_agg, parse_days, parse_every,
                         parse_fields, parse_range)

log = logging.getLogger("solis-web")

SETTING_TYPES = {"grid_charging": bool, "time_of_use_mode": bool, "reserve_mode": bool,
                 "max_charge_current_a": float, "max_discharge_current_a": float,
                 "overdischarge_soc": int, "backup_soc": int, "max_charge_soc": int, "force_charge_soc": int,
                  "tou_slot": dict, "clock_sync": True}
TOU_KEYS = {"kind", "slot", "enabled", "start", "end", "current_a", "soc"}


def valid_setting(setting, value) -> bool:
    """Shape check only; the poller validates ranges against the inverter's current values."""
    kind = SETTING_TYPES.get(setting)
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind is bool:
        return isinstance(value, bool)
    if kind is float:
        return number
    if kind is int:
        return number and value == int(value)
    if kind is dict:
        return isinstance(value, dict) and set(value) == TOU_KEYS
    if kind is True:
        return value is True
    return False


class InfluxUnavailable(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def create_app(repo=None, settings: Settings | None = None, now=_now, control=None, releases=None) -> FastAPI:
    s = settings or Settings()
    if repo is None:
        from .influx import InfluxRepo
        repo = InfluxRepo(s)
    if control is None:
        control = ControlClient(s.poller_url, s.influx_token)

    mcp_state: dict = {}

    # The tariff is a list of versions (see tariff.py): .env gives the first one, saved changes add later ones.
    tariffs = TariffStore(
        Tariff(day=s.tariff_day, night=s.tariff_night,
               night_start=s.tariff_night_start if s.tariff_night is not None else None,
               night_end=s.tariff_night_end if s.tariff_night is not None else None) if s.tariff_day is not None else None,
        s.tariff_file)

    def tariff_view(tf):
        """The tariff as the cost endpoints and the cards show it (None = nothing configured)."""
        return {"currency": s.tariff_currency, **(tf.as_dict() if tf else {"day": None, "night": None, "night_start": None, "night_end": None})}

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

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:          # connection refused, timeout, auth, Flux error ...
            log.warning("InfluxDB query failed: %s", exc)
            raise InfluxUnavailable(f"InfluxDB unavailable ({type(exc).__name__})") from exc

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

    # The login: WEB_BASIC_AUTH, or a password changed on the Settings page (kept as a hash, see auth.py).
    login = LoginStore(s.basic_auth, s.auth_file)

    if s.basic_auth:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            path = request.url.path
            if path in ("/healthz", "/mcp", "/mcp/"):
                return await call_next(request)
            if s.auth_settings_only and path != "/api/settings" and not path.startswith("/api/settings/"):
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if not await asyncio.to_thread(login.verify_header, header):      # a hash check takes ~15 ms, remembered afterwards
                return Response("unauthorized", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="solis", charset="UTF-8"'})
            return await call_next(request)

    # ------------------------------------------------------------------ routes
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/api/site")
    async def api_site():
        return {"name": s.site_name}

    async def live():
        return shaping.live_payload(await call(repo.latest), now())

    @app.get("/api/live")
    async def api_live():
        return await live()

    @app.get("/api/stream")
    async def api_stream(request: Request):
        async def events():
            yield f"retry: 5000\n\n"
            while not await request.is_disconnected():
                try:
                    yield f"data: {json.dumps(await live())}\n\n"
                except InfluxUnavailable as exc:
                    yield f"event: influx-error\ndata: {json.dumps({'detail': 'InfluxDB unavailable', 'error': str(exc)})}\n\n"
                await asyncio.sleep(s.stream_interval_s)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/history")
    async def api_history(fields: str = Query(...), range: str = Query("6h"),
                          every: str = Query("auto"), agg: str = Query("mean")):
        names = parse_fields(fields)
        rng, range_s = parse_range(range)
        win = parse_every(every, range_s)
        fn = parse_agg(agg)
        times, series = await call(repo.history, names, range_s, win, fn)
        times, series = shaping.insert_gaps(times, series, WINDOWS[win])
        return {"range": rng, "every": win, "agg": fn,
                "t": [round(t.timestamp()) for t in times],
                "series": {k: [shaping._num(v) for v in vs] for k, vs in series.items()}}

    @app.get("/api/energy/daily")
    async def api_energy_daily(days: int = Query(30)):
        n = parse_days(days)
        t = now()
        rows = await call(repo.window_max, list(ENERGY_FIELDS), shaping.daily_start(t, n, s.tz), "15m")
        return {"tz": s.tz, "unit": "kWh", "days": shaping.aggregate_daily(rows, ENERGY_FIELDS, n, t, s.tz)}

    # ------------------------------------------------------------------ settings (via the poller)
    async def poller(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except PollerUnavailable as exc:
            log.warning("poller control API unreachable: %s", exc)
            return 502, {"detail": "Poller unreachable"}

    @app.get("/api/settings")
    async def api_settings():
        status, body = await poller(control.get)
        if status == 200:
            ctl = body.get("control", {})
            reason = None
            if not ctl.get("enabled"):
                reason = "Changes are disabled. Set CONTROL_ENABLED=true in .env and restart the poller."
            elif not s.basic_auth:
                reason = "Changes need a password. Set WEB_BASIC_AUTH=user:pass in .env and restart the web service."
            body["web"] = {"auth_enabled": bool(s.basic_auth), "can_change": reason is None, "reason": reason}
        return JSONResponse(body, status_code=status)

    def refuse_write(request: Request):
        """Common checks for every POST that changes something: a password must be configured, and the request
        must be same-origin JSON (browsers send Basic credentials with cross-site form posts, but a form
        cannot send JSON, and Origin/Sec-Fetch-Site reveal the caller). Returns an error response, or None."""
        if not s.basic_auth:
            return JSONResponse({"detail": "Changes need WEB_BASIC_AUTH to be set."}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "Content-Type must be application/json"}, status_code=415)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return None

    @app.post("/api/settings")
    async def api_settings_change(request: Request):
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        setting = body.get("setting") if isinstance(body, dict) else None
        value = body.get("value") if isinstance(body, dict) else None
        if not valid_setting(setting, value):
            return JSONResponse({"detail": "body must be {setting, value} for a known setting"}, status_code=400)
        log.warning("settings change requested: %s=%s", setting, value)
        status, result = await poller(control.apply, setting, value)
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ the dashboard password
    @app.get("/api/settings/login")
    async def api_login():
        return {**login.info(), "platform": s.updates_managed_by or None, "web": capacity_web()}

    @app.post("/api/settings/login")
    async def api_login_change(request: Request):
        """Change the dashboard password: {current, new}; new = null goes back to the WEB_BASIC_AUTH one.
        Needs the current password again, and an existing login (an open dashboard cannot be locked by a stranger)."""
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict) or set(body) != {"current", "new"}:
            return JSONResponse({"detail": "body must be {current, new}"}, status_code=400)
        try:
            result = await asyncio.to_thread(login.change, body["current"], body["new"])
        except TooManyAttempts as exc:
            return JSONResponse({"detail": str(exc), "retry_after_s": exc.retry_s}, status_code=429, headers={"Retry-After": str(exc.retry_s)})
        except LoginError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)          # 400, not 401: a 401 would make the browser ask again
        except OSError as exc:
            log.error("could not save the password: %s", exc)
            return JSONResponse({"detail": "Could not save the new password on the server."}, status_code=500)
        return {**result, "platform": s.updates_managed_by or None, "web": capacity_web()}

    # ------------------------------------------------------------------ battery capacity (the app's own value, kept by the poller)
    def capacity_web():
        return {"can_change": bool(s.basic_auth),
                "reason": None if s.basic_auth else "Changes need a password. Set WEB_BASIC_AUTH=user:pass in .env and restart the web service."}

    @app.get("/api/settings/battery-capacity")
    async def api_battery_capacity():
        status, body = await poller(control.battery_capacity)
        if status == 200:
            body["web"] = capacity_web()
        return JSONResponse(body, status_code=status)

    @app.post("/api/settings/battery-capacity")
    async def api_battery_capacity_change(request: Request):
        """Usable battery capacity for the runtime estimate and outage alerts; null = back to .env / the inverter.
        Nothing is written to the inverter, so this works with CONTROL_ENABLED=false."""
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        kwh = body.get("kwh") if isinstance(body, dict) else None
        if (not isinstance(body, dict) or set(body) != {"kwh"} or isinstance(kwh, bool)
                or not (kwh is None or isinstance(kwh, (int, float)))):
            return JSONResponse({"detail": "body must be {kwh: number or null}"}, status_code=400)
        log.warning("battery capacity change requested: %s", kwh)
        status, result = await poller(control.set_battery_capacity, kwh)
        if status == 200:
            result["web"] = capacity_web()
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ where the inverter's data logger is (kept by the poller)
    def network_hint(host: str, request: Request):
        """A network to suggest for a scan: the configured logger's, else the browser's (X-Forwarded-For, when a proxy
        sets it). The proxy's own address is never used: inside Docker it says nothing about the home network."""
        candidates = [host] + [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        for c in candidates:
            try:
                ip = ipaddress.IPv4Address(c)
            except ValueError:
                continue
            if ip.is_private and not (ip.is_loopback or ip.is_link_local):
                return ".".join(str(ip).split(".")[:3])
        return None

    @app.get("/api/settings/inverter")
    async def api_inverter(request: Request):
        status, body = await poller(control.inverter_address)
        if status == 200:
            body["web"] = capacity_web()
            body["scan_hint"] = network_hint(body.get("host", ""), request)
        return JSONResponse(body, status_code=status)

    INVERTER_KEYS = {"host", "port", "unit"}

    @app.post("/api/settings/inverter")
    async def api_inverter_change(request: Request):
        """The logger's address (null host = back to INVERTER_HOST). Nothing is written to the inverter, so this works
        with CONTROL_ENABLED=false; the poller follows the new address within seconds, no restart."""
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict) or "host" not in body or set(body) - INVERTER_KEYS:
            return JSONResponse({"detail": "body must be {host, port, unit}"}, status_code=400)
        log.warning("inverter address change requested: %s", body.get("host"))
        status, result = await poller(control.set_inverter_address, body["host"], body.get("port", 502), body.get("unit", 1))
        if status == 200:
            result["web"] = capacity_web()
            result["scan_hint"] = network_hint(result.get("host", ""), request)
        return JSONResponse(result, status_code=status)

    @app.post("/api/settings/inverter/scan")
    async def api_inverter_scan(request: Request):
        """Look for the data logger on a home network (a /24). Briefly connects to devices with Modbus port 502 open."""
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict) or set(body) != {"network"} or not isinstance(body["network"], str):
            return JSONResponse({"detail": "body must be {network}"}, status_code=400)
        log.warning("network scan requested: %s", body["network"])
        status, result = await poller(control.scan_for_inverter, body["network"])
        return JSONResponse(result, status_code=status)

    @app.get("/api/events")
    async def api_events(days: int = Query(30)):
        if not 1 <= days <= 366:
            raise HTTPException(status_code=400, detail="days must be 1..366")
        return {"days": days, **shaping.pair_events(await call(repo.events, days), now())}

    summary_cache: dict = {}

    @app.get("/api/energy/summary")
    async def api_energy_summary():
        """Month/year to date from our own daily maxima (the inverter only keeps month/year for PV)."""
        t = now()
        if summary_cache.get("at") and (t - summary_cache["at"]).total_seconds() < 300:
            return summary_cache["body"]
        zone = ZoneInfo(s.tz)
        today = t.astimezone(zone).date()
        n = (today - today.replace(month=1, day=1)).days + 1
        rows = await call(repo.window_max, list(ENERGY_FIELDS), shaping.daily_start(t, n, s.tz), "1h")
        days = shaping.aggregate_daily(rows, ENERGY_FIELDS, n, t, s.tz)
        body = {"tz": s.tz, "unit": "kWh", **shaping.period_sums(days, ENERGY_FIELDS, today)}
        summary_cache.update(at=t, body=body)
        return body

    @app.get("/api/schedule")
    async def api_schedule():
        """Cached inverter settings (Time-of-Use slots, modes, SOC limits) for the Overview."""
        status, body = await poller(control.cached_settings)
        return JSONResponse(body, status_code=status)

    cost_cache: dict = {}

    @app.get("/api/cost")
    async def api_cost():
        t = now()
        if tariffs.at(t) is None:
            return {"configured": False, "tariff": tariff_view(None)}
        # the active zone follows the clock, so it is computed on every request, outside the 5-minute cache
        def with_rate(body):
            return {**body, "tariff": tariff_view(tariffs.at(t)), "now": shaping.rate_now(t, s.tz, tariffs.at(t))}
        if cost_cache.get("at") and (t - cost_cache["at"]).total_seconds() < 300:
            return with_rate(cost_cache["body"])
        today = t.astimezone(ZoneInfo(s.tz)).date()
        rows = await call(repo.window_max, ["grid_import_today"], shaping.daily_start(t, today.day, s.tz), "15m")
        zones = shaping.import_costs([(r[0], r[2]) for r in rows], s.tz, tariffs.at)
        body = {"configured": True, **shaping.cost_summary(zones, today)}
        cost_cache.update(at=t, body=body)
        return with_rate(body)

    history_cache: dict = {}
    first_cache: dict = {}

    async def first_day(t):
        """Local date of the oldest stored reading (cached: it only changes if old data is deleted)."""
        if "at" not in first_cache or (t - first_cache["at"]).total_seconds() > 6 * 3600:
            ft = await call(repo.first_time)
            first_cache.update(at=t, day=ft.astimezone(ZoneInfo(s.tz)).date() if ft else None)
        return first_cache["day"]

    @app.get("/api/cost/history")
    async def api_cost_history(month: str | None = Query(None)):
        """Day-by-day import, cost (day/night zones) and consumption for one month.
        Every day is priced with the tariff that was in force at the time, so later price changes don't alter it."""
        t = now()
        if tariffs.at(t) is None:
            return {"configured": False, "tariff": tariff_view(None)}
        today = t.astimezone(ZoneInfo(s.tz)).date()
        ym = shaping.parse_month(month) if month else (today.year, today.month)
        if ym is None or ym > (today.year, today.month):
            raise HTTPException(status_code=400, detail="month must be YYYY-MM, not in the future")
        first = await first_day(t)
        key = f"{ym[0]}-{ym[1]:02d}"
        is_now = ym == (today.year, today.month)
        hit = history_cache.get(key)
        if hit and (t - hit["at"]).total_seconds() < (300 if is_now else 3600):
            return hit["body"]
        dates = shaping.month_dates(ym[0], ym[1], today)
        start, stop = shaping.local_range(dates[0], dates[-1], s.tz)
        rows = await call(repo.window_max, list(ENERGY_FIELDS), start, "15m", stop)
        zones = shaping.import_costs([(r[0], r[2]) for r in rows if r[1] == "grid_import_today"], s.tz, tariffs.at)
        energy = shaping.aggregate_dates(rows, list(ENERGY_FIELDS), dates, s.tz)
        body = {"configured": True, "tariff": tariff_view(tariffs.at(t)), "month": key,
                "current_month": f"{today.year}-{today.month:02d}", "tz": s.tz,
                "first_day": first.isoformat() if first else None,
                "tariff_changes": [version_dict(st, tf) for st, tf in tariffs.between(start, stop)],
                **shaping.cost_days(energy, zones)}
        history_cache[key] = {"at": t, "body": body}
        return body

    # ------------------------------------------------------------------ tariff settings (the web app's own, not the inverter's)
    def tariff_payload(t):
        cur = tariffs.at(t)
        return {"currency": s.tariff_currency, "tz": s.tz, "configured": cur is not None,
                "current": cur.as_dict() if cur else None, "source": tariffs.source,
                "versions": [version_dict(st, tf) for st, tf in tariffs.versions],
                "upcoming": [version_dict(st, tf) for st, tf in tariffs.upcoming(t)],
                "next_change_at": next_boundary(t).isoformat(),
                "can_change": bool(s.basic_auth),
                "reason": None if s.basic_auth else "Changes need a password. Set WEB_BASIC_AUTH=user:pass in .env and restart the web service."}

    @app.get("/api/settings/tariff")
    async def api_tariff():
        return tariff_payload(now())

    TARIFF_KEYS = {"day", "night", "night_start", "night_end", "from"}

    @app.post("/api/settings/tariff")
    async def api_tariff_change(request: Request):
        """New prices, valid from the next quarter-hour or from a later date. Earlier consumption keeps its price."""
        if (refused := refuse_write(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict) or set(body) - TARIFF_KEYS:
            return JSONResponse({"detail": "body must be {day, night, night_start, night_end, from}"}, status_code=400)
        requested = None
        if body.get("from") is not None:
            try:
                d = datetime.strptime(str(body["from"]), "%Y-%m-%d").date()
            except ValueError:
                return JSONResponse({"detail": "from must be a date like 2026-11-01"}, status_code=400)
            requested = shaping.local_range(d, d, s.tz)[0]
        t = now()
        try:
            tariff = validate(body.get("day"), body.get("night"), body.get("night_start"), body.get("night_end"))
            effective = tariffs.set(tariff, t, requested)
        except TariffError as exc:
            return JSONResponse({"detail": str(exc)[:1].upper() + str(exc)[1:] + "."}, status_code=400)
        except OSError as exc:
            log.error("could not save the tariff: %s", exc)
            return JSONResponse({"detail": "Could not save the tariff on the server."}, status_code=500)
        cost_cache.clear()
        history_cache.clear()
        log.warning("tariff changed from %s: %s", effective.isoformat(), tariff.as_dict())
        return {"effective_from": effective.isoformat(), **tariff_payload(t)}

    # ------------------------------------------------------------------ updating from the dashboard
    bridge = Bridge(s.update_dir)
    lookup = releases or ReleaseFeed(s.solis_repo, s.github_api).latest
    checked: dict = {}

    @app.get("/api/version")
    async def api_version():
        """Public: the version, and whether the last (background or manual) check found a newer release."""
        latest = checked.get("release")
        newer = bool(latest) and is_newer(latest["tag"], s.solis_version)
        return {"version": s.solis_version or None, "installed": bool(s.solis_version), "managed_by": s.updates_managed_by or None,
                "update": {"available": newer, "tag": latest["tag"] if newer else None}}

    def update_state(t):
        latest = checked.get("release")
        available = bool(latest) and is_newer(latest["tag"], s.solis_version)
        reason = None
        if s.updates_managed_by:
            reason = f"Updates are handled by {s.updates_managed_by}."
        elif not s.basic_auth:
            reason = "Changes need a password. Set WEB_BASIC_AUTH=user:pass in .env and restart the web service."
        elif not s.solis_version or parse_version(s.solis_version) is None:
            reason = "This copy was built from source, so it cannot update itself. Update it with git."
        elif not bridge.ready():
            reason = "Updating from the dashboard is not set up. On the server run: solis-monitor dashboard-updates on"
        status = bridge.status()
        if status and status["state"] == "running":       # a crashed updater must not leave the page waiting forever
            try:
                started = datetime.fromisoformat((status["started"] or "").replace("Z", "+00:00"))
            except ValueError:
                started = None
            if started is None or (t - started).total_seconds() > 1800:
                status = {**status, "state": "failed", "message": "The updater stopped without reporting. On the server, see: solis-monitor logs"}
        busy = bridge.pending() or bool(status and status["state"] == "running")
        return {"version": s.solis_version or None, "can_update": reason is None, "reason": reason, "busy": busy,
                "managed_by": s.updates_managed_by or None,
                "latest": {**latest, "checked_at": checked["at"].isoformat(), "available": available} if latest else None,
                "status": status}

    def limited_response(until):
        at = until.astimezone(ZoneInfo(s.tz)).strftime("%H:%M")
        return JSONResponse({"detail": f"GitHub's limit for your network is used up. Try again after {at}.",
                             "retry_at": until.isoformat()}, status_code=429)

    async def check_release(t, max_age_s):
        until = checked.get("blocked_until")
        if until and t < until:                    # GitHub already said no: do not ask again before it lifts
            return limited_response(until)
        if not checked.get("at") or (t - checked["at"]).total_seconds() > max_age_s:
            try:
                release = await asyncio.to_thread(lookup)
            except RateLimited as exc:
                log.warning("release lookup refused: GitHub rate limit until %s", exc.reset_at.isoformat())
                checked["blocked_until"] = exc.reset_at
                return limited_response(exc.reset_at)
            except ReleaseLookupError as exc:
                log.warning("release lookup failed: %s", exc)
                return JSONResponse({"detail": "Could not reach GitHub to look for a new version."}, status_code=502)
            checked.update(at=t, release=release)
            checked.pop("blocked_until", None)
        return None

    async def periodic_check():
        failures = 0
        delay = s.update_check_first_delay_s
        while True:
            await asyncio.sleep(delay)
            failed = await check_release(now(), -1)
            failures = failures + 1 if failed is not None else 0
            if failed is not None and checked.get("blocked_until"):
                delay = delay_after_limit(checked["blocked_until"], now(), s.update_check_hours)    # just after the limit lifts
            else:
                delay = next_delay(s.update_check_hours, failures, random.random())

    @app.get("/api/settings/update")
    async def api_update():
        return update_state(now())

    @app.post("/api/settings/update/check")
    async def api_update_check(request: Request):
        if (refused := refuse_write(request)) is not None:
            return refused
        if s.updates_managed_by:
            return JSONResponse({"detail": f"Updates are handled by {s.updates_managed_by}."}, status_code=400)
        if not s.solis_version:
            return JSONResponse({"detail": "This copy was built from source, so it has no releases to compare with."}, status_code=400)
        t = now()
        if (failed := await check_release(t, 30)) is not None:       # at most one GitHub request per 30 s
            return failed
        return update_state(t)

    @app.post("/api/settings/update")
    async def api_update_start(request: Request):
        """Drop a request for the host. What gets installed is decided there: the newest release, with rollback."""
        if (refused := refuse_write(request)) is not None:
            return refused
        t = now()
        state = update_state(t)
        if not state["can_update"]:
            return JSONResponse({"detail": state["reason"]}, status_code=409)
        if state["busy"]:
            return JSONResponse({"detail": "An update is already running."}, status_code=409)
        if (failed := await check_release(t, 300)) is not None:
            return failed
        state = update_state(t)
        if not state["latest"] or not state["latest"]["available"]:
            return JSONResponse({"detail": "You already have the newest version."}, status_code=409)
        try:
            bridge.request(state["latest"]["tag"], t)
        except OSError as exc:
            log.error("could not write the update request: %s", exc)
            return JSONResponse({"detail": "Could not reach the updater on the server."}, status_code=500)
        log.warning("update to %s requested from the dashboard", state["latest"]["tag"])
        return JSONResponse({**update_state(t), "busy": True}, status_code=202)

    @app.get("/api/devices")
    async def api_devices():
        status, body = await poller(control.devices)
        return JSONResponse(body, status_code=status)

    @app.get("/api/settings/log")
    async def api_settings_log(limit: int = Query(20)):
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="limit must be 1..100")
        return {"changes": await call(repo.control_log, limit)}

    # ------------------------------------------------------------------ MCP (read-only, for agents)
    if s.mcp_token:
        from .mcp_server import DataApi, build_mcp

        async def mcp_history(fields, rng, every, agg):
            return await api_history(",".join(fields), rng, every, agg)

        mcp = build_mcp(DataApi(
            live=live, history=mcp_history, events=lambda d: api_events(d),
            energy_summary=api_energy_summary, cost=api_cost,
            devices=lambda: poller(control.devices), schedule=lambda: poller(control.cached_settings)))
        app.router.routes.extend(mcp.streamable_http_app().routes)   # before the static mount below
        mcp_state["manager"] = mcp.session_manager

    # ------------------------------------------------------------------ frontend
    if os.path.isdir(s.static_dir):
        app.mount("/", StaticFiles(directory=s.static_dir, html=True), name="static")

    return app


def app_factory():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return create_app()
