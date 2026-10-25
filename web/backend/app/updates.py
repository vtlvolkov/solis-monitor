"""Updating from the dashboard.

The web container never touches Docker. It shares a folder (`/control`, `./control` on the host) with a small
systemd unit on the host that runs `solis-monitor update`. The web side can only drop a request file; the
host decides what an update is (the newest release of the configured repository, with rollback) and writes
its progress to `update-status.json`, which this module only reads. Nothing in either file is trusted.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

TAG = re.compile(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,100}")
STATES = {"running", "ok", "rolled_back", "failed"}
REQUEST, STATUS, BRIDGE = "update-request", "update-status.json", "bridge.json"


class ReleaseLookupError(Exception):
    pass


def parse_version(tag) -> tuple[int, int, int] | None:
    m = TAG.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(int(g) for g in m.groups()) if m else None


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest_release(repo: str, api_base: str = "https://api.github.com", timeout: float = 8) -> dict:
    """The newest published (not draft, not pre-release) release of `repo`: {tag, url, published}."""
    if not REPO.fullmatch(repo or ""):
        raise ReleaseLookupError("no repository configured")
    req = urllib.request.Request(f"{api_base.rstrip('/')}/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "solis-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read(200_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseLookupError(str(exc)) from exc
    tag = body.get("tag_name") if isinstance(body, dict) else None
    if parse_version(tag) is None:
        raise ReleaseLookupError("the latest release has no version tag")
    url = body.get("html_url")
    published = body.get("published_at")
    return {"tag": tag,
            "url": url if isinstance(url, str) and url.startswith("https://github.com/") else None,
            "published": published if isinstance(published, str) and len(published) <= 40 else None}


def _text(v, limit=300):
    return v[:limit] if isinstance(v, str) else None


class Bridge:
    """The shared folder with the host. All reads tolerate missing or garbage files."""

    def __init__(self, directory: str):
        self.dir = directory

    def _read(self, name: str):
        try:
            with open(os.path.join(self.dir, name)) as f:
                data = json.loads(f.read(20_000))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def ready(self) -> bool:
        info = self._read(BRIDGE)
        return bool(info and info.get("enabled") is True and os.access(self.dir, os.W_OK))

    def status(self) -> dict | None:
        raw = self._read(STATUS)
        if not raw or raw.get("state") not in STATES:
            return None
        return {"state": raw["state"], "from": _text(raw.get("from"), 40), "to": _text(raw.get("to"), 40),
                "message": _text(raw.get("message")), "started": _text(raw.get("started"), 40),
                "finished": _text(raw.get("finished"), 40)}

    def pending(self) -> bool:
        return os.path.exists(os.path.join(self.dir, REQUEST))

    def request(self, to: str, now: datetime | None = None) -> None:
        """Ask the host to update. The content is informational only: the host works out the target itself."""
        body = json.dumps({"requested_at": (now or datetime.now(timezone.utc)).isoformat(), "to": to})
        tmp = os.path.join(self.dir, f".{REQUEST}.tmp")
        with open(tmp, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o666)                              # the host user must be able to delete it
        os.replace(tmp, os.path.join(self.dir, REQUEST))  # one atomic event for the host's path unit
