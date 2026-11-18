"""The solar forecast: the Overview card, the dashed line on Charts, and the Settings card that says where the
panels are. The poller fetches it from Open-Meteo and keeps it (poller/forecast.py); these routes only pass requests on."""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import Context

log = logging.getLogger("solis-web")
KEYS = {"lat", "lon", "arrays", "losses", "correct", "telegram", "below_pct", "overview"}


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    control, poller = ctx.control, ctx.poller

    @r.get("/api/forecast")
    async def api_forecast():
        """Read-only and open like the rest of the data: today, tomorrow and the day after, and how good it has been."""
        status, body = await poller(control.forecast)
        return JSONResponse(body, status_code=status)

    @r.get("/api/forecast/hours")
    async def api_forecast_hours():
        """Every hour known (the last weeks as forecast at the time, then the days ahead): the line on Charts."""
        status, body = await poller(control.forecast_hours)
        return JSONResponse(body, status_code=status)

    @r.get("/api/settings/forecast")
    async def api_forecast_config():
        status, body = await poller(control.forecast_config)
        if status == 200:
            body["web"] = ctx.web_can_change()
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/forecast")
    async def api_forecast_config_change(request: Request):
        """{lat, lon, arrays: [{kwp, tilt, azimuth}], losses, correct, telegram, below_pct, overview}: lat null = off.
        The poller checks the values; only a location rounded to about 1 km is ever sent to Open-Meteo."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) - KEYS or not {"lat", "lon", "arrays"} <= set(body):
            return JSONResponse({"detail": "body must be {lat, lon, arrays, losses, correct, telegram, below_pct, overview}"}, status_code=400)
        log.warning("solar forecast change requested: %s", "off" if body.get("lat") is None else "on")
        status, result = await poller(control.set_forecast_config, body)
        if status == 200:
            result["web"] = ctx.web_can_change()
        return JSONResponse(result, status_code=status)

    return r
