"""What every route module shares: the settings, the data sources (InfluxDB and the poller's internal API),
the login and tariff stores, and the checks a request must pass before it may change anything."""
import asyncio
import json
import logging
import time
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import LoginStore
from .config import Settings
from .control import PollerUnavailable
from .layout import LayoutStore
from .tariff import Tariff, TariffStore

log = logging.getLogger("solis-web")

NEEDS_PASSWORD = "Changes need a password. Set WEB_BASIC_AUTH=user:pass in .env and restart the web service."


class InfluxUnavailable(Exception):
    pass


def _describe(exc: Exception) -> str:
    """One line: the exception's name, and for an InfluxDB answer its status and message (not the headers)."""
    status, body = getattr(exc, "status", None), getattr(exc, "body", None)
    if status is not None:
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        try:
            body = json.loads(body).get("message", body) if body else ""
        except (ValueError, AttributeError):
            pass
        return f"{type(exc).__name__} {status}: {str(body)[:300]}"
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"


class Context:
    def __init__(self, s: Settings, repo, control, now):
        self.s, self.repo, self.control, self.now = s, repo, control, now
        # The login: WEB_BASIC_AUTH, or a password changed on the Settings page (kept as a hash, see auth.py).
        self.login = LoginStore(s.basic_auth, s.auth_file)
        # The tariff is a list of versions (see tariff.py): .env gives the first one, saved changes add later ones.
        self.tariffs = TariffStore(
            Tariff(day=s.tariff_day, night=s.tariff_night,
                   night_start=s.tariff_night_start if s.tariff_night is not None else None,
                   night_end=s.tariff_night_end if s.tariff_night is not None else None) if s.tariff_day is not None else None,
            s.tariff_file)
        # The Overview's cards and their order (Customize on the Overview; see layout.py).
        self.layout = LayoutStore(s.layout_file)
        # Cost results, cleared when the tariff changes (routes/settings.py) so a new price shows at once.
        self.cost_cache: dict = {}
        self._retention: tuple[float, int] = (-1e9, s.raw_retention_days)
        self.history_cache: dict = {}

    async def call(self, fn, *args):
        """Run an InfluxDB query off the event loop; any failure becomes a 503 (see the handler in main.py)."""
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:          # connection refused, timeout, auth, Flux error ...
            detail = _describe(exc)
            log.warning("InfluxDB query failed: %s", detail)
            raise InfluxUnavailable(f"InfluxDB unavailable: {detail}") from exc

    async def raw_retention(self) -> int:
        """Days of 10-second readings kept (0 = all): the poller's value, which the Settings page can change, asked once
        a minute; RAW_RETENTION_DAYS from this container's environment while the poller does not answer."""
        at, days = self._retention
        if time.monotonic() - at < 60:
            return days
        get = getattr(self.control, "retention", None)
        if get is not None:
            status, body = await self.poller(get)
            if status == 200 and isinstance(body.get("days"), int):
                days = body["days"]
        self._retention = (time.monotonic(), days)
        return days

    def forget_retention(self) -> None:
        self._retention = (-1e9, self._retention[1])

    async def poller(self, fn, *args):
        """Call the poller's internal API off the event loop; (status, body), 502 when the poller is unreachable."""
        try:
            return await asyncio.to_thread(fn, *args)
        except PollerUnavailable as exc:
            log.warning("poller control API unreachable: %s", exc)
            return 502, {"detail": "Poller unreachable"}

    def refuse_write(self, request: Request):
        """Common checks for every POST that changes something: a password must be configured, and the request
        must be same-origin JSON (browsers send Basic credentials with cross-site form posts, but a form
        cannot send JSON, and Origin/Sec-Fetch-Site reveal the caller). Returns an error response, or None."""
        if not self.s.basic_auth:
            return JSONResponse({"detail": "Changes need WEB_BASIC_AUTH to be set."}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "Content-Type must be application/json"}, status_code=415)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return None

    @staticmethod
    async def json_body(request: Request):
        """(body, None), or (None, a 400 response) when the body is not JSON."""
        try:
            return await request.json(), None
        except ValueError:
            return None, JSONResponse({"detail": "body must be JSON"}, status_code=400)

    def tariff_view(self, tf):
        """The tariff as the cost endpoints and the cards show it (None = nothing configured)."""
        return {"currency": self.s.tariff_currency,
                **(tf.as_dict() if tf else {"day": None, "night": None, "night_start": None, "night_end": None})}

    def web_can_change(self) -> dict:
        """Whether the app's own settings (capacity, logger address, tariff) may be changed: a password is enough."""
        return {"can_change": bool(self.s.basic_auth), "reason": None if self.s.basic_auth else NEEDS_PASSWORD}
