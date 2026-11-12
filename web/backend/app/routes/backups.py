"""The Settings page's Backup tab: ask the host for a backup, follow it, list and download the archives (backups.py)."""
import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from ..backups import FOLDER, KEEP_MAX, KEEP_MIN, NAME, BackupBridge
from ..context import NEEDS_PASSWORD, Context

log = logging.getLogger("solis-web")


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    s, now = ctx.s, ctx.now
    bridge = BackupBridge(s.update_dir, s.backup_dir)

    def state(t):
        reason = None
        if s.updates_managed_by:
            reason = f"Backups are handled by {s.updates_managed_by}."
        elif not s.basic_auth:
            reason = NEEDS_PASSWORD
        elif not bridge.ready():
            reason = "Backing up from the dashboard is not set up. On the server run: solis-monitor dashboard-updates on"
        status = bridge.status()
        if status and status["state"] == "running":       # a crashed backup must not leave the page waiting forever
            try:
                started = datetime.fromisoformat((status["started"] or "").replace("Z", "+00:00"))
            except ValueError:
                started = None
            if started is None or (t - started).total_seconds() > 1800:
                status = {**status, "state": "failed", "message": "The backup stopped without reporting. On the server, see: solis-monitor logs"}
        busy = bridge.pending() or bool(status and status["state"] == "running")
        if bridge.pending() and bridge.unclaimed(t):     # written, never taken: a new request may replace it
            busy, status = False, {"state": "failed", "file": None, "started": None, "finished": None,
                                   "message": "The server did not pick up the request. On the server run: solis-monitor dashboard-updates on"}
        cfg = bridge.config()
        return {"can_backup": reason is None, "reason": reason, "busy": busy, "managed_by": s.updates_managed_by or None,
                "status": status, "backups": bridge.list(), "listed": bridge.mounted(),
                "folder": {"dir": cfg["dir"] or cfg["default_dir"], "default_dir": cfg["default_dir"], "keep": cfg["keep"],
                           "keep_min": KEEP_MIN, "keep_max": KEEP_MAX}}

    @r.get("/api/settings/backups")
    async def api_backups():
        return state(now())

    @r.post("/api/settings/backups")
    async def api_backup_start(request: Request):
        """Drop a request for the host, which makes the archive and reports back."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        t = now()
        st = state(t)
        if not st["can_backup"]:
            return JSONResponse({"detail": st["reason"]}, status_code=409)
        if st["busy"]:
            return JSONResponse({"detail": "A backup is already running."}, status_code=409)
        try:
            bridge.request(now=t)
        except OSError as exc:
            log.error("could not write the backup request: %s", exc)
            return JSONResponse({"detail": "Could not reach the backup helper on the server."}, status_code=500)
        log.warning("backup requested from the dashboard")
        return JSONResponse({**state(t), "busy": True}, status_code=202)

    @r.post("/api/settings/backups/folder")
    async def api_backup_folder(request: Request):
        """Ask the host to use another folder for the archives (and how many to keep). The host checks the folder
        (an existing, writable directory under a mount-point-like root) and answers through the status file."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) != {"dir", "keep"}:
            return JSONResponse({"detail": "body must be {dir, keep}"}, status_code=400)
        d, keep = body["dir"], body["keep"]
        if not isinstance(d, str) or len(d) > 200 or not FOLDER.fullmatch(d) or "/../" in d + "/" :
            return JSONResponse({"detail": "The folder must be an absolute path on the server, like /mnt/usb/solis."}, status_code=400)
        if isinstance(keep, bool) or not isinstance(keep, int) or not KEEP_MIN <= keep <= KEEP_MAX:
            return JSONResponse({"detail": f"keep must be a whole number from {KEEP_MIN} to {KEEP_MAX}."}, status_code=400)
        t = now()
        st = state(t)
        if not st["can_backup"]:
            return JSONResponse({"detail": st["reason"]}, status_code=409)
        if st["busy"]:
            return JSONResponse({"detail": "A backup is already running."}, status_code=409)
        try:
            bridge.request(now=t, action="set-folder", dir=d.rstrip("/") or "/", keep=keep)
        except OSError as exc:
            log.error("could not write the backup request: %s", exc)
            return JSONResponse({"detail": "Could not reach the backup helper on the server."}, status_code=500)
        log.warning("backup folder change requested from the dashboard: %s (keep %s)", d, keep)
        return JSONResponse({**state(t), "busy": True}, status_code=202)

    @r.post("/api/settings/backups/remove")
    async def api_backup_remove(request: Request):
        """Ask the host to delete one archive from the backup folder (this container only sees a read-only copy)."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or set(body) != {"name"} or not isinstance(body["name"], str) or not NAME.fullmatch(body["name"]):
            return JSONResponse({"detail": "body must be {name} of a listed backup"}, status_code=400)
        t = now()
        st = state(t)
        if not st["can_backup"]:
            return JSONResponse({"detail": st["reason"]}, status_code=409)
        if st["busy"]:
            return JSONResponse({"detail": "A backup is already running."}, status_code=409)
        try:
            bridge.request(now=t, action="remove", name=body["name"])
        except OSError as exc:
            log.error("could not write the backup request: %s", exc)
            return JSONResponse({"detail": "Could not reach the backup helper on the server."}, status_code=500)
        log.warning("backup removal requested from the dashboard: %s", body["name"])
        return JSONResponse({**state(t), "busy": True}, status_code=202)

    @r.post("/api/settings/backups/restore")
    async def api_backup_restore(request: Request):
        """Ask the host to restore one of the listed archives: it backs the current state up first (unless told not
        to), then replaces the database, the saved files and .env with the archive's. The dashboard restarts meanwhile."""
        if (refused := ctx.refuse_write(request)) is not None:
            return refused
        body, bad = await ctx.json_body(request)
        if bad is not None:
            return bad
        if (not isinstance(body, dict) or set(body) != {"name", "backup_first"} or not isinstance(body["name"], str)
                or not NAME.fullmatch(body["name"]) or not isinstance(body["backup_first"], bool)):
            return JSONResponse({"detail": "body must be {name, backup_first} for a listed backup"}, status_code=400)
        t = now()
        st = state(t)
        if not st["can_backup"]:
            return JSONResponse({"detail": st["reason"]}, status_code=409)
        if st["busy"]:
            return JSONResponse({"detail": "A backup is already running."}, status_code=409)
        try:
            bridge.request(now=t, action="restore", name=body["name"], backup_first=body["backup_first"])
        except OSError as exc:
            log.error("could not write the backup request: %s", exc)
            return JSONResponse({"detail": "Could not reach the backup helper on the server."}, status_code=500)
        log.warning("RESTORE requested from the dashboard: %s (backup first: %s)", body["name"], body["backup_first"])
        return JSONResponse({**state(t), "busy": True}, status_code=202)

    @r.get("/api/settings/backups/{name}")
    async def api_backup_download(name: str):
        """One archive, as the host wrote it. It holds .env: this route is behind the Settings login like every change."""
        path = bridge.path(name)
        if path is None:
            return JSONResponse({"detail": "No such backup."}, status_code=404)
        log.warning("backup downloaded from the dashboard: %s", name)
        return FileResponse(path, media_type="application/gzip", filename=name)

    return r
