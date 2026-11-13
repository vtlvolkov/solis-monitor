"""Everything the Settings page can change, and what it reads to show the forms. Every POST passes
Context.refuse_write first (a configured password, same-origin JSON). Inverter settings go to the poller,
which validates them against the inverter and owns the safety model; the capacity, the logger's address and
the tariff are the app's own values and work with CONTROL_ENABLED=false."""
import asyncio
import ipaddress
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import shaping
from ..auth import LoginError, TooManyAttempts
from ..context import Context
from ..tariff import TariffError, next_boundary, validate, version_dict

log = logging.getLogger("solis-web")

SETTING_TYPES = {"grid_charging": bool, "time_of_use_mode": bool, "reserve_mode": bool,
                 "max_charge_current_a": float, "max_discharge_current_a": float,
                 "overdischarge_soc": int, "backup_soc": int, "max_charge_soc": int, "force_charge_soc": int,
                 "tou_slot": dict, "clock_sync": True}
TOU_KEYS = {"kind", "slot", "enabled", "start", "end", "current_a", "soc"}
INVERTER_KEYS = {"host", "port", "unit"}
TARIFF_KEYS = {"day", "night", "night_start", "night_end", "from"}


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


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    s, now, call, poller, control, login, tariffs = ctx.s, ctx.now, ctx.call, ctx.poller, ctx.control, ctx.login, ctx.tariffs

    async def change_body(request: Request):
        """(body, None) for a POST that may change something, or (None, the response refusing it)."""
        if (refused := ctx.refuse_write(request)) is not None:
            return None, refused
        return await ctx.json_body(request)

    # ------------------------------------------------------------------ inverter settings (via the poller)
    @r.get("/api/settings")
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

    @r.post("/api/settings")
    async def api_settings_change(request: Request):
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        setting = body.get("setting") if isinstance(body, dict) else None
        value = body.get("value") if isinstance(body, dict) else None
        if not valid_setting(setting, value):
            return JSONResponse({"detail": "body must be {setting, value} for a known setting"}, status_code=400)
        log.warning("settings change requested: %s=%s", setting, value)
        status, result = await poller(control.apply, setting, value)
        return JSONResponse(result, status_code=status)

    @r.get("/api/settings/log")
    async def api_settings_log(limit: int = Query(20)):
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=400, detail="limit must be 1..100")
        return {"changes": await call(ctx.repo.control_log, limit)}

    # ------------------------------------------------------------------ the dashboard password
    @r.get("/api/settings/login")
    async def api_login():
        return {**login.info(), "platform": s.updates_managed_by or None, "web": ctx.web_can_change()}

    @r.post("/api/settings/login")
    async def api_login_change(request: Request):
        """Change the dashboard password: {current, new}; new = null goes back to the WEB_BASIC_AUTH one.
        Needs the current password again, and an existing login (an open dashboard cannot be locked by a stranger)."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
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
        return {**result, "platform": s.updates_managed_by or None, "web": ctx.web_can_change()}

    # ------------------------------------------------------------------ battery capacity (the app's own value, kept by the poller)
    @r.get("/api/settings/battery-capacity")
    async def api_battery_capacity():
        status, body = await poller(control.battery_capacity)
        if status == 200:
            body["web"] = ctx.web_can_change()
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/battery-capacity")
    async def api_battery_capacity_change(request: Request):
        """Usable battery capacity for the runtime estimate and outage alerts; null = back to .env / the inverter.
        Nothing is written to the inverter, so this works with CONTROL_ENABLED=false."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        kwh = body.get("kwh") if isinstance(body, dict) else None
        if (not isinstance(body, dict) or set(body) != {"kwh"} or isinstance(kwh, bool)
                or not (kwh is None or isinstance(kwh, (int, float)))):
            return JSONResponse({"detail": "body must be {kwh: number or null}"}, status_code=400)
        log.warning("battery capacity change requested: %s", kwh)
        status, result = await poller(control.set_battery_capacity, kwh)
        if status == 200:
            result["web"] = ctx.web_can_change()
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ how long the 10-second readings are kept (kept by the poller)
    @r.get("/api/settings/retention")
    async def api_retention():
        status, body = await poller(control.retention)
        if status == 200:
            body["web"] = ctx.web_can_change()
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/retention")
    async def api_retention_change(request: Request):
        """Days of 10-second readings to keep (0 = all, null = back to RAW_RETENTION_DAYS). Fewer days delete the older
        readings at the poller's next daily run; the 1-minute averages, outages and settings log are kept regardless."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        days = body.get("days") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) != {"days"} or isinstance(days, bool) or not (days is None or isinstance(days, int)):
            return JSONResponse({"detail": "body must be {days: whole number or null}"}, status_code=400)
        log.warning("raw retention change requested: %s", days)
        status, result = await poller(control.set_retention, days)
        ctx.forget_retention()
        if status == 200:
            result["web"] = ctx.web_can_change()
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ where the inverter's data logger is (kept by the poller)
    @r.get("/api/settings/inverter")
    async def api_inverter(request: Request):
        status, body = await poller(control.inverter_address)
        if status == 200:
            body["web"] = ctx.web_can_change()
            body["scan_hint"] = network_hint(body.get("host", ""), request)
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/inverter")
    async def api_inverter_change(request: Request):
        """The logger's address (null host = back to INVERTER_HOST). Nothing is written to the inverter, so this works
        with CONTROL_ENABLED=false; the poller follows the new address within seconds, no restart."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or "host" not in body or set(body) - INVERTER_KEYS:
            return JSONResponse({"detail": "body must be {host, port, unit}"}, status_code=400)
        log.warning("inverter address change requested: %s", body.get("host"))
        status, result = await poller(control.set_inverter_address, body["host"], body.get("port", 502), body.get("unit", 1))
        if status == 200:
            result["web"] = ctx.web_can_change()
            result["scan_hint"] = network_hint(result.get("host", ""), request)
        return JSONResponse(result, status_code=status)

    @r.post("/api/settings/inverter/scan")
    async def api_inverter_scan(request: Request):
        """Look for the data logger on a home network (a /24). Briefly connects to devices with Modbus port 502 open."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) != {"network"} or not isinstance(body["network"], str):
            return JSONResponse({"detail": "body must be {network}"}, status_code=400)
        log.warning("network scan requested: %s", body["network"])
        status, result = await poller(control.scan_for_inverter, body["network"])
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ Debug tab: manual register reads (never writes)
    @r.post("/api/settings/debug/read")
    async def api_debug_read(request: Request):
        """One raw Modbus read (function 3 or 4) through the poller, which owns the only connection. Needs the same
        password and same-origin checks as a change, because it makes the inverter answer a request of the caller's choosing."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) != {"function", "address", "count"}:
            return JSONResponse({"detail": "body must be {function, address, count}"}, status_code=400)
        status, result = await poller(control.debug_read, body["function"], body["address"], body["count"])
        return JSONResponse(result, status_code=status)

    # ------------------------------------------------------------------ tariff settings (the web app's own, not the inverter's)
    def tariff_payload(t):
        cur = tariffs.at(t)
        return {"currency": s.tariff_currency, "tz": s.tz, "configured": cur is not None,
                "current": cur.as_dict() if cur else None, "source": tariffs.source,
                "versions": [version_dict(st, tf) for st, tf in tariffs.versions],
                "upcoming": [version_dict(st, tf) for st, tf in tariffs.upcoming(t)],
                "next_change_at": next_boundary(t).isoformat(),
                **ctx.web_can_change()}

    @r.get("/api/settings/tariff")
    async def api_tariff():
        return tariff_payload(now())

    @r.post("/api/settings/tariff")
    async def api_tariff_change(request: Request):
        """New prices, valid from the next quarter-hour or from a later date. Earlier consumption keeps its price."""
        body, bad = await change_body(request)
        if bad is not None:
            return bad
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
        ctx.cost_cache.clear()                    # the cost endpoints (routes/data.py) show the new price at once
        ctx.history_cache.clear()
        log.warning("tariff changed from %s: %s", effective.isoformat(), tariff.as_dict())
        return {"effective_from": effective.isoformat(), **tariff_payload(t)}

    return r
