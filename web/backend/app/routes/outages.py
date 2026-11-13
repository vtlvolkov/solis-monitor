"""The planned outage schedule (Yasno): the Overview card, and the Settings card that sets the source and group.
The poller fetches and keeps it (poller/outages.py); these routes only pass requests on."""
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ..context import Context

log = logging.getLogger("solis-web")


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    control, poller = ctx.control, ctx.poller

    @r.get("/api/outage-schedule")
    async def api_outage_schedule():
        """Read-only and open like the rest of the data: the schedule of the saved group and the battery verdict."""
        status, body = await poller(control.outages)
        return JSONResponse(body, status_code=status)

    @r.get("/api/settings/outage-schedule")
    async def api_outage_config():
        status, body = await poller(control.outage_config)
        if status == 200:
            body["web"] = ctx.web_can_change()
        return JSONResponse(body, status_code=status)

    @r.post("/api/settings/outage-schedule")
    async def api_outage_config_change(request: Request):
        """{source, group, alerts, overview?}: source null = off; overview = show the card on the Overview.
        Only the group is kept, never the address."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or not {"source", "group", "alerts"} <= set(body) <= {"source", "group", "alerts", "overview"}:
            return JSONResponse({"detail": "body must be {source, group, alerts, overview?}"}, status_code=400)
        log.warning("outage schedule change requested: %s %s", body.get("source"), body.get("group"))
        status, result = await poller(control.set_outage_config, body["source"], body["group"], body["alerts"], body.get("overview"))
        if status == 200:
            result["web"] = ctx.web_can_change()
        return JSONResponse(result, status_code=status)

    # The address lookup goes through the server (and the poller), so the browser never talks to Yasno and the
    # address is never stored: it is used for these three questions only.
    @r.get("/api/settings/outage-schedule/streets")
    async def api_outage_streets(source: str = Query(...), q: str = Query(...)):
        status, body = await poller(lambda: control.outage_lookup("streets", source=source, q=q))
        return JSONResponse(body, status_code=status)

    @r.get("/api/settings/outage-schedule/houses")
    async def api_outage_houses(source: str = Query(...), street: int = Query(...), q: str = Query(...)):
        status, body = await poller(lambda: control.outage_lookup("houses", source=source, street=street, q=q))
        return JSONResponse(body, status_code=status)

    @r.get("/api/settings/outage-schedule/group")
    async def api_outage_group(source: str = Query(...), street: int = Query(...), house: int = Query(...)):
        status, body = await poller(lambda: control.outage_lookup("group", source=source, street=street, house=house))
        return JSONResponse(body, status_code=status)

    return r
