"""Updating from the dashboard (release installs only, see updates.py): the version badge, the release check
and the update request. The container only drops a request file; the host decides what to install."""
import asyncio
import logging
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import Context
from ..updates import Bridge, RateLimited, ReleaseFeed, ReleaseLookupError, delay_after_limit, is_installed_release, is_newer, next_delay

log = logging.getLogger("solis-web")


def build(ctx: Context, releases=None):
    """The router, and the background coroutine that looks for a new release now and then (main runs it)."""
    r = APIRouter()
    s, now = ctx.s, ctx.now
    bridge = Bridge(s.update_dir)
    lookup = releases or ReleaseFeed(s.solis_repo, s.github_api).latest
    checked: dict = {}

    @r.get("/api/version")
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
        elif not s.solis_version or not is_installed_release(s.solis_version):
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

    @r.get("/api/settings/update")
    async def api_update():
        return update_state(now())

    @r.post("/api/settings/update/check")
    async def api_update_check(request: Request):
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        if s.updates_managed_by:
            return JSONResponse({"detail": f"Updates are handled by {s.updates_managed_by}."}, status_code=400)
        if not s.solis_version:
            return JSONResponse({"detail": "This copy was built from source, so it has no releases to compare with."}, status_code=400)
        t = now()
        if (failed := await check_release(t, 30)) is not None:       # at most one GitHub request per 30 s
            return failed
        return update_state(t)

    @r.post("/api/settings/update")
    async def api_update_start(request: Request):
        """Drop a request for the host. What gets installed is decided there: the newest release, with rollback."""
        if (refused := ctx.refuse_write(request)) is not None:
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

    return r, periodic_check
