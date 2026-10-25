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
from datetime import datetime, timedelta, timezone

TAG = re.compile(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,100}")
STATES = {"running", "ok", "rolled_back", "failed"}
REQUEST, STATUS, BRIDGE = "update-request", "update-status.json", "bridge.json"


class ReleaseLookupError(Exception):
    pass


class RateLimited(ReleaseLookupError):
    """GitHub refused because the anonymous limit (60 requests an hour per public IP) is used up."""

    def __init__(self, reset_at: datetime):
        super().__init__(f"GitHub rate limit, resets at {reset_at.isoformat()}")
        self.reset_at = reset_at


def delay_after_limit(reset_at: datetime, now: datetime, hours: float) -> float:
    """Seconds until the background check should try again after a rate limit: just after the reset (never
    sooner than a minute, never later than the normal interval)."""
    return min(max(60.0, (reset_at - now).total_seconds() + 60), hours * 3600)


def _rate_limit_reset(exc: urllib.error.HTTPError, now: datetime | None = None) -> datetime | None:
    """When GitHub says the limit lifts, if this error is a rate limit at all (403/429 with the usual headers)."""
    if exc.code not in (403, 429) or not getattr(exc, "headers", None):
        return None
    h = exc.headers
    try:
        if h.get("Retry-After"):
            return (now or datetime.now(timezone.utc)) + timedelta(seconds=min(int(h["Retry-After"]), 3600 * 2))
        if str(h.get("X-RateLimit-Remaining", "")).strip() == "0" and h.get("X-RateLimit-Reset"):
            return datetime.fromtimestamp(int(h["X-RateLimit-Reset"]), timezone.utc)
    except (ValueError, OverflowError):
        return None
    return None


def parse_version(tag) -> tuple[int, int, int] | None:
    m = TAG.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(int(g) for g in m.groups()) if m else None


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def _request(repo: str, api_base: str, timeout: float, etag: str | None) -> tuple[dict | None, str | None]:
    """(release, etag), or (None, etag) when GitHub says nothing changed (HTTP 304, free against its rate limit)."""
    if not REPO.fullmatch(repo or ""):
        raise ReleaseLookupError("no repository configured")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "solis-monitor"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(f"{api_base.rstrip('/')}/repos/{repo}/releases/latest", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read(200_000))
            new_etag = r.headers.get("ETag") if getattr(r, "headers", None) else None
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag
        if (reset := _rate_limit_reset(exc)) is not None:
            raise RateLimited(reset) from exc
        raise ReleaseLookupError(f"GitHub answered {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseLookupError(str(exc)) from exc
    tag = body.get("tag_name") if isinstance(body, dict) else None
    if parse_version(tag) is None:
        raise ReleaseLookupError("the latest release has no version tag")
    url = body.get("html_url")
    published = body.get("published_at")
    return ({"tag": tag,
             "url": url if isinstance(url, str) and url.startswith("https://github.com/") else None,
             "published": published if isinstance(published, str) and len(published) <= 40 else None},
            new_etag if isinstance(new_etag, str) and len(new_etag) <= 200 else None)


def fetch_latest_release(repo: str, api_base: str = "https://api.github.com", timeout: float = 8) -> dict:
    """The newest published (not draft, not pre-release) release of `repo`: {tag, url, published}."""
    release, _ = _request(repo, api_base, timeout, None)
    return release


class ReleaseFeed:
    """Asks for the newest release again and again, sending the last ETag so an unchanged answer costs nothing."""

    def __init__(self, repo: str, api_base: str = "https://api.github.com", timeout: float = 8):
        self.repo, self.api_base, self.timeout = repo, api_base, timeout
        self.etag: str | None = None
        self.release: dict | None = None

    def latest(self) -> dict:
        release, self.etag = _request(self.repo, self.api_base, self.timeout, self.etag if self.release else None)
        if release is not None:
            self.release = release
        return self.release


def next_delay(hours: float, failures: int, rand: float) -> float:
    """Seconds until the next background check. `rand` is 0..1. After a failure retry sooner, backing off
    (1 h, 2 h, 4 h ...) but never later than the normal interval; normally +-10 % so installs do not all ask at once."""
    base = hours * 3600
    if failures:
        return min(base, 3600 * 2 ** (failures - 1))
    return base * (0.9 + 0.2 * rand)


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
        percent = raw.get("percent")
        percent = max(0, min(100, percent)) if isinstance(percent, int) and not isinstance(percent, bool) else None
        return {"state": raw["state"], "from": _text(raw.get("from"), 40), "to": _text(raw.get("to"), 40),
                "message": _text(raw.get("message")), "percent": percent, "started": _text(raw.get("started"), 40),
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
