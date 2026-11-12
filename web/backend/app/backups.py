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
STATES = {"running", "ok", "failed"}
# What the host names its archives; anything else in the folder is not offered (so nothing can be fetched by path tricks)
NAME = re.compile(r"solis-monitor(-[A-Za-z0-9][A-Za-z0-9-]{0,60})?-\d{8}-\d{6}\.tar\.gz")
# A folder the page may ask for: absolute, printable, no "..". The host decides whether it is allowed (deploy/solis-monitor).
FOLDER = re.compile(r"/[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*/?")
KEEP_MIN, KEEP_MAX = 1, 50


class BackupBridge(Bridge):
    REQUEST, STATUS = REQUEST, STATUS

    def __init__(self, control_dir: str, backups_dir: str):
        super().__init__(control_dir)
        self.backups_dir = backups_dir

    def status(self) -> dict | None:
        raw = self._read(self.STATUS)
        if not raw or raw.get("state") not in STATES:
            return None
        file = raw.get("file")
        return {"state": raw["state"], "message": _text(raw.get("message")),
                "file": file if isinstance(file, str) and NAME.fullmatch(file) else None,
                "started": _text(raw.get("started"), 40), "finished": _text(raw.get("finished"), 40)}

    def mounted(self) -> bool:
        return os.path.isdir(self.backups_dir)

    def config(self) -> dict:
        """Where the host puts the archives (written by the host: dashboard-updates on, or a folder set from the page)."""
        raw = self._read(CONFIG) or {}
        d, default, keep = raw.get("dir"), raw.get("default_dir"), raw.get("keep")
        return {"dir": d if isinstance(d, str) and FOLDER.fullmatch(d) else None,
                "default_dir": default if isinstance(default, str) and FOLDER.fullmatch(default) else None,
                "keep": keep if isinstance(keep, int) and not isinstance(keep, bool) and KEEP_MIN <= keep <= KEEP_MAX else 5}

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
