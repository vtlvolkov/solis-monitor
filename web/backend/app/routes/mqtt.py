"""MQTT (Home Assistant, Node-RED, relays): where the poller publishes. The poller keeps the settings and does the
publishing (poller/mqtt.py); these routes pass requests on, behind the Settings login. Publishing only: nothing that
arrives over MQTT reaches the inverter, so CONTROL_ENABLED does not apply."""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import Context

log = logging.getLogger("solis-web")

KEYS = {"enabled", "host", "port", "username", "password", "tls", "prefix", "interval_s", "discovery"}


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    control, poller = ctx.control, ctx.poller

    async def body_of(request: Request):
        if (refused := ctx.refuse_write(request)) is not None:
            return None, refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return None, bad
        if not isinstance(body, dict) or not set(body) <= KEYS or "enabled" not in body:
            return None, JSONResponse({"detail": "body must be {enabled, host, port, username, password, tls, prefix, interval_s, discovery}"},
                                      status_code=400)
        return body, None

    @r.get("/api/settings/mqtt")
    async def api_mqtt():
        """The broker, topics and connection status; the password only as password_set."""
        status, body = await poller(control.mqtt)
        if status == 200:
            body["web"] = ctx.web_can_change()
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/mqtt")
    async def api_mqtt_change(request: Request):
        """{enabled: false} turns it off; otherwise the whole form (password null = keep the saved one)."""
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        log.warning("MQTT change requested: %s", {k: v for k, v in body.items() if k != "password"})
        status, result = await poller(control.set_mqtt, body)
        if status == 200:
            result["web"] = ctx.web_can_change()
        return JSONResponse(result, status_code=status)

    @r.post("/api/settings/mqtt/test")
    async def api_mqtt_test(request: Request):
        """Connects to the broker once with these settings, without saving them: {ok, error, ms}."""
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        status, result = await poller(control.test_mqtt, body)
        return JSONResponse(result, status_code=status)

    return r
