"""Backups from the dashboard.

The same arrangement as updates.py: this container can only drop a request file in the shared control folder
and read the status file the host writes. `solis-monitor backup --from-dashboard` on the host makes the archive
(the database, the saved settings, .env) in its backups/ folder, which is mounted read-only here so the page can
list and download it. An archive holds every password and token: the download sits behind the Settings login,
and the page says what it contains. Nothing read from either folder is trusted.
"""
from __future__ import annotations  # the method named list() must not shadow the list[...] annotations

import os
import re
from datetime import datetime, timezone

from .updates import Bridge, _text

REQUEST, STATUS, CONFIG, INDEX = "backup-request", "backup-status.json", "backup-config.json", "backups.json"
REMOTE = "backup-remote.json"            # where the host copies the archives (Nextcloud/WebDAV); never holds the password
STATES = {"running", "ok", "failed"}
COPY_STATES = {"ok", "failed"}
# What the host names its archives; anything else in the folder is not offered (so nothing can be fetched by path tricks)
NAME = re.compile(r"solis-monitor(-[A-Za-z0-9][A-Za-z0-9-]{0,60})?-\d{8}-\d{6}\.tar\.gz")
# A folder the page may ask for: absolute, printable, no "..". The host decides whether it is allowed (deploy/solis-monitor).
FOLDER = re.compile(r"/[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*/?")
KEEP_MIN, KEEP_MAX = 1, 50
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
# A copy on a server. The host checks every value again (deploy/solis-monitor set_remote) and tries the connection.
KINDS = ("nextcloud", "webdav")
URL = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~%/+-]*)?")
REMOTE_KEEP_MIN, REMOTE_KEEP_MAX, REMOTE_KEEP_DEFAULT = 1, 100, 14
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# What a browser shows after a Nextcloud server's address: cut, so pasting the address of any Nextcloud page works.
NEXTCLOUD_PAGES = re.compile(r"/(index\.php|apps|remote\.php|login|s|f|settings)(/.*)?$")


def nextcloud_base(url: str) -> str:
    """https://cloud.example.com/index.php/apps/files/?dir=/ -> https://cloud.example.com (a Nextcloud in a
    subfolder, like https://example.com/nextcloud/apps/files, keeps its subfolder)."""
    base = url.strip().split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return NEXTCLOUD_PAGES.sub("", base).rstrip("/")


def remote_error(kind, url, user, password, folder, keep, password_saved: bool) -> str | None:
    """Why these settings cannot be sent to the host, in the words the page shows (lib/backup.ts has the same rules)."""
    if kind not in KINDS:
        return "Choose Nextcloud or another WebDAV server."
    if not isinstance(url, str) or len(url) > 300 or not URL.fullmatch(url.rstrip("/")):
        return "The address must start with http:// or https://."
    if not isinstance(user, str) or not user or len(user) > 200 or ":" in user or CONTROL.search(user):
        return "Enter the user name."
    if password is not None and (not isinstance(password, str) or len(password) > 500 or CONTROL.search(password)):
        return "Enter the password."
    if not password and not password_saved:
        return "Enter the password."
    if (not isinstance(folder, str) or len(folder) > 200 or CONTROL.search(folder) or '"' in folder or "\\" in folder
            or any(seg in (".", "..") for seg in folder.split("/"))):
        return "That folder name is not allowed."
    if isinstance(keep, bool) or not isinstance(keep, int) or not REMOTE_KEEP_MIN <= keep <= REMOTE_KEEP_MAX:
        return f"Keep must be a whole number from {REMOTE_KEEP_MIN} to {REMOTE_KEEP_MAX}."
    return None


class BackupBridge(Bridge):
    REQUEST, STATUS = REQUEST, STATUS

    def __init__(self, control_dir: str, backups_dir: str):
        super().__init__(control_dir)
        self.backups_dir = backups_dir

    def status(self) -> dict | None:
        raw = self._read(self.STATUS)
        if not raw or raw.get("state") not in STATES:
            return None
        file, copy = raw.get("file"), raw.get("copy")
        return {"state": raw["state"], "message": _text(raw.get("message")),
                "file": file if isinstance(file, str) and NAME.fullmatch(file) else None,
                "started": _text(raw.get("started"), 40), "finished": _text(raw.get("finished"), 40),
                "scheduled": raw.get("scheduled") is True,
                "copy": copy if copy in COPY_STATES else None, "copy_message": _text(raw.get("copy_message")) if copy in COPY_STATES else None}

    def ready(self) -> bool:
        """bridge.json alone is not enough: `dashboard-updates on` before v0.4.0 set up updating only, without the
        backup watcher. The one that installs it also writes backup-config.json, so that file says it is there."""
        return super().ready() and self._read(CONFIG) is not None

    def mounted(self) -> bool:
        return os.path.isdir(self.backups_dir)

    def config(self) -> dict:
        """Where the host puts the archives (written by the host: dashboard-updates on, or a folder set from the page)."""
        raw = self._read(CONFIG) or {}
        d, default, keep, at = raw.get("dir"), raw.get("default_dir"), raw.get("keep"), raw.get("schedule")
        return {"dir": d if isinstance(d, str) and FOLDER.fullmatch(d) else None,
                "default_dir": default if isinstance(default, str) and FOLDER.fullmatch(default) else None,
                "keep": keep if isinstance(keep, int) and not isinstance(keep, bool) and KEEP_MIN <= keep <= KEEP_MAX else 5,
                "schedule": at if isinstance(at, str) and TIME.fullmatch(at) else None,
                "scheduler": raw.get("scheduler") is True}      # the host's daily-backup timer is installed

    def remote(self) -> dict | None:
        """The server the host copies each backup to, and how the last copy went; None when there is none."""
        raw = self._read(REMOTE)
        if not raw or raw.get("kind") not in KINDS or not isinstance(raw.get("url"), str) or not URL.fullmatch(raw["url"].rstrip("/")):
            return None
        keep, last = raw.get("keep"), raw.get("last")
        out = {"kind": raw["kind"], "url": raw["url"], "user": _text(raw.get("user"), 200) or "",
               "folder": _text(raw.get("folder"), 200) or "",
               "keep": keep if isinstance(keep, int) and not isinstance(keep, bool) and REMOTE_KEEP_MIN <= keep <= REMOTE_KEEP_MAX else REMOTE_KEEP_DEFAULT,
               "last": None}
        if isinstance(last, dict) and last.get("state") in COPY_STATES:
            file = last.get("file")
            out["last"] = {"state": last["state"], "message": _text(last.get("message")), "at": _text(last.get("at"), 40),
                           "file": file if isinstance(file, str) and NAME.fullmatch(file) else None}
        return out

    def list(self) -> list[dict]:
        """The archives in the backup folder, newest first: from the index the host writes after each backup (it can
        see a folder this container cannot), else from the folder mounted here. `downloadable` = present here."""
        here = self._scan()
        index = self._read(INDEX)
        if not index or not isinstance(index.get("files"), list):
            return [{**b, "downloadable": True} for b in here]
        have = {b["name"] for b in here}
        out = []
        for f in index["files"][:200]:
            if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not NAME.fullmatch(f["name"]):
                continue
            size, created = f.get("size"), f.get("created")
            out.append({"name": f["name"], "size": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
                        "created": _text(created, 40) or "", "downloadable": f["name"] in have})
        return sorted(out, key=lambda b: b["created"], reverse=True)

    def _scan(self) -> list[dict]:
        out = []
        try:
            names = os.listdir(self.backups_dir)
        except OSError:
            return out
        for name in names:
            if not NAME.fullmatch(name):
                continue
            try:
                st = os.stat(os.path.join(self.backups_dir, name))
            except OSError:
                continue
            out.append({"name": name, "size": st.st_size,
                        "created": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
        return sorted(out, key=lambda b: b["created"], reverse=True)

    def path(self, name: str) -> str | None:
        """The file to download, or None for a name the host would never have written or that is not there."""
        if not isinstance(name, str) or not NAME.fullmatch(name):
            return None
        p = os.path.join(self.backups_dir, name)
        return p if os.path.isfile(p) else None
