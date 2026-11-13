"""The Overview's card layout: read by every visitor, changed only behind the Settings login."""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import Context
from ..layout import LayoutError

log = logging.getLogger("solis-web")


def build(ctx: Context) -> APIRouter:
    r = APIRouter()

    def view() -> dict:
        return {"layout": ctx.layout.get(), "web": ctx.web_can_change()}

    @r.get("/api/overview-layout")
    async def api_layout():
        """Open like the rest of the page: which cards the Overview shows, in which column (null = the default)."""
        return view()

    @r.get("/api/settings/overview-layout")
    async def api_layout_edit():
        """The same, behind the Settings login: the page asks for it when Customize is pressed, so the browser
        asks for the password before the changes, not after."""
        return view()

    @r.post("/api/settings/overview-layout")
    async def api_layout_change(request: Request):
        """{layout: {left, right} | null}: null goes back to the default."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) != {"layout"}:
            return JSONResponse({"detail": "body must be {layout}"}, status_code=400)
        try:
            ctx.layout.set(body["layout"])
        except LayoutError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except OSError as exc:
            log.warning("could not save the Overview layout: %s", exc)
            return JSONResponse({"detail": "Could not save the layout on the server."}, status_code=500)
        log.warning("Overview layout changed: %s", body["layout"])
        return view()

    return r
