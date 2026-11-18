"""
Solar (PV) forecast from Open-Meteo: free, no account, nothing sent but a location rounded to about 1 km.

    GET https://api.open-meteo.com/v1/forecast?latitude=50.45&longitude=30.52&tilt=35&azimuth=0&past_days=14&forecast_days=3
        &hourly=global_tilted_irradiance,temperature_2m,cloud_cover&timezone=UTC&timeformat=unixtime
        -> {"hourly": {"time": [1763424000, ...], "global_tilted_irradiance": [W/m², ...], "temperature_2m": [...], "cloud_cover": [%]}}

Checked live 2026-11-18: radiation at time T is the mean over the hour before T (so it belongs to the hour starting
T - 1 h); azimuth is 0 = south, -90 = east, 90 = west and must lie within ±180 (the page uses compass degrees,
180 = south); one request per group of panels, because tilt and azimuth are single values.

The model turns the irradiance on the panels into watts (kWp, losses, a simple cell-temperature derating) and then,
when asked to, scales it by what the panels really made in the last 14 days (`correction`): that covers shade, dirt
and a wrong kWp. Hours with the battery nearly full are left out of that, because a hybrid inverter holds PV back
when it has nowhere to put it.

What the dashboard shows for a past hour is the forecast as it stood when that hour began (`frozen`), so the chart's
dashed line never rewrites history; `evening` keeps each day's total as forecast the evening before, which is what
the "forecast vs made" list compares. Both live in /data next to the settings.
"""
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("solis-poller")

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FETCH_EVERY_S = 3600
PAST_DAYS = 14                  # for the correction and the usual day
FORECAST_DAYS = 3               # today, tomorrow, the day after
KEEP_DAYS = 35                  # frozen hours and evening totals: as long as the raw readings are kept at least
TIMEOUT_S = 20
TEMP_COEF = -0.004              # crystalline silicon: -0.4 % per °C of cell temperature above 25 °C
CELL_RISE = 0.03                # the cell is about 3 °C above the air per 100 W/m² of sun
FULL_SOC = 97                   # hours with the battery this full are not used to correct the forecast
FACTOR_MIN, FACTOR_MAX = 0.2, 1.5
MIN_HOURS = 20                  # sunny hours needed before the correction is trusted
EVENING_HOUR = 20               # when tomorrow's total is noted and the Telegram message goes
MAX_ARRAYS = 3
DEFAULTS = {"losses": 14, "correct": True, "telegram": False, "below_pct": 50, "overview": True}


class ForecastError(Exception):
    pass


# ---------------------------------------------------------------- the settings
def _num(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}")
    return float(v)


def clean_config(body: dict) -> dict:
    """What the Settings card sent, checked. lat null = off (the rest is then kept for the next time)."""
    if not isinstance(body, dict):
        raise ValueError("the settings must be an object")
    out = {}
    if body.get("lat") is None or body.get("lon") is None:
        out["lat"] = out["lon"] = None
    else:
        out["lat"] = round(_num(body["lat"], "Latitude", -90, 90), 2)           # about 1 km: that is all Open-Meteo gets
        out["lon"] = round(_num(body["lon"], "Longitude", -180, 180), 2)
    arrays = body.get("arrays")
    if not isinstance(arrays, list) or not 1 <= len(arrays) <= MAX_ARRAYS:
        raise ValueError(f"from 1 to {MAX_ARRAYS} groups of panels are needed")
    out["arrays"] = []
    for a in arrays:
        if not isinstance(a, dict) or set(a) != {"kwp", "tilt", "azimuth"}:
            raise ValueError("each group of panels needs kwp, tilt and azimuth")
        out["arrays"].append({"kwp": round(_num(a["kwp"], "Peak power", 0.1, 200), 2), "tilt": round(_num(a["tilt"], "Tilt", 0, 90)),
                              "azimuth": round(_num(a["azimuth"], "Facing", 0, 360)) % 360})
    out["losses"] = round(_num(body.get("losses", DEFAULTS["losses"]), "Losses", 0, 50))
    out["below_pct"] = round(_num(body.get("below_pct", DEFAULTS["below_pct"]), "The Telegram threshold", 10, 100))
    for k in ("correct", "telegram", "overview"):
        v = body.get(k, DEFAULTS[k])
        if not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        out[k] = v
    return out


# ---------------------------------------------------------------- Open-Meteo
class OpenMeteo:
    def __init__(self, base: str = OPEN_METEO, opener=None):
        self.base, self.opener = base, opener or urllib.request.urlopen

    def hourly(self, lat: float, lon: float, tilt: float, compass: float) -> dict:
        """{hour start (epoch s): (irradiance W/m², air °C, cloud %)} for the panels' tilt and facing."""
        params = {"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "tilt": round(tilt), "azimuth": to_open_meteo(compass),
                  "hourly": "global_tilted_irradiance,temperature_2m,cloud_cover", "past_days": PAST_DAYS,
                  "forecast_days": FORECAST_DAYS, "timezone": "UTC", "timeformat": "unixtime"}
        req = urllib.request.Request(f"{self.base}?{urllib.parse.urlencode(params)}",
                                     headers={"Accept": "application/json", "User-Agent": "solis-monitor"})
        try:
            with self.opener(req, timeout=TIMEOUT_S) as r:
                data = json.loads(r.read(2_000_000))
        except urllib.error.HTTPError as exc:
            raise ForecastError(f"Open-Meteo answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ForecastError(f"Open-Meteo not reachable ({type(exc).__name__})") from exc
        return parse_hourly(data)


def to_open_meteo(compass: float) -> int:
    """Compass degrees (0 north, 90 east, 180 south) -> Open-Meteo's (0 south, -90 east, 90 west, ±180 north)."""
    a = (round(compass) - 180) % 360
    return a - 360 if a > 180 else a


def parse_hourly(data) -> dict:
    h = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(h, dict) or not isinstance(h.get("time"), list) or not isinstance(h.get("global_tilted_irradiance"), list):
        raise ForecastError("Open-Meteo sent an unexpected answer")
    temps, clouds = h.get("temperature_2m") or [], h.get("cloud_cover") or []
    out = {}
    for i, t in enumerate(h["time"]):
        g = h["global_tilted_irradiance"][i] if i < len(h["global_tilted_irradiance"]) else None
        if type(t) is not int or not isinstance(g, (int, float)) or isinstance(g, bool):
            continue
        temp = temps[i] if i < len(temps) and isinstance(temps[i], (int, float)) else None
        cloud = clouds[i] if i < len(clouds) and isinstance(clouds[i], (int, float)) else None
        out[t - 3600] = (max(0.0, float(g)), temp, cloud)                         # the mean of the hour before t
    if not out:
        raise ForecastError("Open-Meteo sent no hours")
    return out


def panel_watts(irradiance: float, air_c: float | None, kwp: float, losses_pct: float) -> float:
    """What a group of panels gives at that irradiance on its plane: STC power, the losses, a warm cell's derating."""
    if irradiance <= 0:
        return 0.0
    cell = (air_c if air_c is not None else 15.0) + CELL_RISE * irradiance
    return irradiance * kwp * (1 - losses_pct / 100) * (1 + TEMP_COEF * (cell - 25))


def model(answers: list[dict], arrays: list[dict], losses_pct: float) -> tuple[dict[int, float], dict[int, float]]:
    """(hour -> W of all the panels together, hour -> cloud cover %) from one answer per group of panels."""
    watts, cloud = {}, {}
    hours = set.intersection(*(set(a) for a in answers)) if answers else set()
    for t in hours:
        watts[t] = round(sum(panel_watts(ans[t][0], ans[t][1], arr["kwp"], losses_pct) for ans, arr in zip(answers, arrays, strict=True)), 1)
        if answers[0][t][2] is not None:
            cloud[t] = answers[0][t][2]
    return watts, cloud


def correction(model_w: dict[int, float], actual: dict[int, tuple[float | None, float | None]], before: int, peak_w: float) -> tuple[float | None, int]:
    """(made ÷ forecast over the sunny hours before `before`, how many hours that used). None until there are enough.
    actual: hour -> (PV W mean, battery SOC max); hours with the battery nearly full are skipped (PV may be held back)."""
    made = expected = 0.0
    n = 0
    for t, (pv, soc) in actual.items():
        m = model_w.get(t)
        if t >= before or m is None or pv is None or m < 0.05 * peak_w or (soc is not None and soc >= FULL_SOC):
            continue
        made += pv
        expected += m
        n += 1
    if n < MIN_HOURS or expected <= 0:
        return None, n
    return max(FACTOR_MIN, min(FACTOR_MAX, made / expected)), n


# ---------------------------------------------------------------- what is kept in /data
class ForecastStore:
    """The settings (forecast.json) and what was fetched and noted (forecast-data.json)."""

    def __init__(self, directory: str | None):
        self.dir = directory or None
        self.lock = threading.Lock()
        raw = self._read("forecast.json")
        try:
            self.config = clean_config(raw) if raw else None
        except ValueError:
            self.config = None
        d = self._read("forecast-data.json") or {}
        self.data = {"fetched_at": d.get("fetched_at"), "model": _int_keys(d.get("model")), "cloud": _int_keys(d.get("cloud")),
                     "frozen": _int_keys(d.get("frozen")), "evening": d.get("evening") if isinstance(d.get("evening"), dict) else {},
                     "telegram_for": d.get("telegram_for")}

    def _read(self, name):
        if not self.dir:
            return None
        try:
            with open(os.path.join(self.dir, name)) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _write(self, name, data):
        if not self.dir:
            return
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, f".{name}.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(self.dir, name))

    def configured(self) -> bool:
        return bool(self.config and self.config["lat"] is not None)

    @property
    def show_card(self) -> bool:
        return self.config["overview"] if self.config else True

    def set_config(self, body: dict) -> tuple[dict, bool]:
        """(the clean settings, whether the panels or the place changed: then what was fetched no longer applies)."""
        new = clean_config(body)
        with self.lock:
            old = self.config
            changed = not old or any(old[k] != new[k] for k in ("lat", "lon", "arrays", "losses"))
            self.config = new
            self._write("forecast.json", new)
            if changed:
                self.data = {"fetched_at": None, "model": {}, "cloud": {}, "frozen": {}, "evening": {}, "telegram_for": None}
                self.save_data()
        return new, changed

    def save_data(self) -> None:
        self._write("forecast-data.json", {**self.data, "model": _str_keys(self.data["model"]), "cloud": _str_keys(self.data["cloud"]),
                                           "frozen": _str_keys(self.data["frozen"])})


def _int_keys(d) -> dict[int, float]:
    out = {}
    for k, v in (d or {}).items() if isinstance(d, dict) else ():
        try:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[int(k)] = float(v)
        except ValueError:
            continue
    return out


def _str_keys(d: dict) -> dict:
    return {str(k): v for k, v in sorted(d.items())}


# ---------------------------------------------------------------- the forecaster
class Forecaster:
    """Fetches every hour while set up, keeps the frozen hours and evening totals, and sends the evening message.

    `actual()` returns hour start -> (PV W mean, battery SOC max) for the last two weeks (from InfluxDB);
    `load()` the usual load by local hour (W); `send(text)` goes to Telegram."""

    def __init__(self, store: ForecastStore, client: OpenMeteo, tz: str, send=lambda text: None,
                 actual=lambda: {}, load=lambda: {}, clock=time.time):
        self.store, self.client, self.tz = store, client, tz
        self.send, self.actual, self.load, self.clock = send, actual, load, clock
        self.last_error: str | None = None
        self.lock = threading.Lock()

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc).astimezone(ZoneInfo(self.tz))

    def _hour(self) -> int:
        return int(self.clock()) // 3600 * 3600

    def _peak_w(self) -> float:
        return sum(a["kwp"] for a in self.store.config["arrays"]) * 1000 if self.store.config else 0.0

    # -- fetching
    def refresh(self) -> bool:
        cfg = self.store.config
        if not self.store.configured():
            return False
        try:
            answers = [self.client.hourly(cfg["lat"], cfg["lon"], a["tilt"], a["azimuth"]) for a in cfg["arrays"]]
        except ForecastError as exc:
            self.last_error = str(exc)
            log.warning("solar forecast: %s", exc)
            return False
        watts, cloud = model(answers, cfg["arrays"], cfg["losses"])
        with self.lock:
            d = self.store.data
            d["model"], d["cloud"] = watts, cloud
            k = self.factor()[0]
            hour = self._hour()
            oldest = hour - KEEP_DAYS * 86400
            frozen = {t: w for t, w in d["frozen"].items() if t >= oldest}      # a new dict: pages may be reading the old one
            frozen.update({t: round(w * k, 1) for t, w in watts.items() if t >= hour})   # the hour now and later
            d["frozen"] = frozen
            d["fetched_at"] = self.now().isoformat()
            self.last_error = None
            self.store.save_data()
        return True

    def factor(self) -> tuple[float, float | None, int]:
        """(the factor in use, the measured one or None, hours it is based on)."""
        measured, n = correction(self.store.data["model"], self.actual() or {}, self._hour(), self._peak_w())
        use = measured if (measured is not None and self.store.config and self.store.config["correct"]) else 1.0
        return use, measured, n

    def watts(self, t: int) -> float | None:
        """The forecast for the hour starting at t: frozen for a past hour, else the model now, corrected."""
        d = self.store.data
        if t < self._hour() and t in d["frozen"]:
            return d["frozen"][t]
        if t in d["model"]:
            return d["model"][t] * self.factor()[0]
        return d["frozen"].get(t)

    def watts_at(self, at: datetime) -> float:
        """For the outage verdict: the expected PV power at a moment (0 where nothing is known)."""
        if not self.store.configured():
            return 0.0
        return self.watts(int(at.timestamp()) // 3600 * 3600) or 0.0

    # -- what the dashboard shows
    def _day_hours(self, day: date) -> list[int]:
        zone = ZoneInfo(self.tz)
        a = datetime(day.year, day.month, day.day, tzinfo=zone)
        b = a + timedelta(days=1)
        return list(range(int(a.timestamp()) // 3600 * 3600, int(b.timestamp()), 3600))

    def _usual(self) -> tuple[float | None, float | None]:
        """(PV kWh of an average recent day, load kWh of an average day)."""
        zone, actual, today = ZoneInfo(self.tz), self.actual() or {}, self.now().date()
        days: dict[date, list[float]] = {}
        for t, (pv, _soc) in actual.items():
            d = datetime.fromtimestamp(t, zone).date()
            if d < today and pv is not None:
                days.setdefault(d, []).append(pv)
        full = [sum(v) / 1000 for v in days.values() if len(v) >= 20]
        prof = self.load() or {}
        return (round(sum(full) / len(full), 1) if full else None,
                round(sum(prof.values()) / 1000, 1) if len(prof) >= 20 else None)

    def _made(self, day: date) -> float | None:
        actual = self.actual() or {}
        vals = [actual[t][0] for t in self._day_hours(day) if t in actual and actual[t][0] is not None]
        return round(sum(vals) / 1000, 1) if len(vals) >= 20 else None

    def payload(self) -> dict:
        cfg, d = self.store.config, self.store.data
        base = {"configured": self.store.configured(), "show": self.store.show_card, "error": self.last_error, "fetched_at": d["fetched_at"],
                "stale": False, "kwp": None, "arrays": [], "losses": None, "factor": None, "factor_hours": 0, "correct": None,
                "days": [], "usual_pv_kwh": None, "usual_load_kwh": None, "load_by_hour": None, "below_pct": None, "recent": [], "miss_pct": None}
        if not base["configured"]:
            return base
        now = self.now()
        use, measured, n = self.factor()
        base.update(kwp=round(self._peak_w() / 1000, 2), arrays=cfg["arrays"], losses=cfg["losses"], correct=cfg["correct"],
                    factor=round(measured, 2) if measured is not None else None, factor_hours=n, below_pct=cfg["below_pct"])
        if d["fetched_at"]:
            base["stale"] = bool(self.last_error) or (now - datetime.fromisoformat(d["fetched_at"])).total_seconds() > 3 * FETCH_EVERY_S
        if not d["model"]:
            return base
        hour, frac = self._hour(), (self.clock() % 3600) / 3600
        for i in range(FORECAST_DAYS):
            day = now.date() + timedelta(days=i)
            hours = self._day_hours(day)
            vals = {t: self.watts(t) for t in hours}
            if any(v is None for t, v in vals.items() if t >= hour):
                break                                                    # beyond what Open-Meteo gave
            rest = sum((v or 0) * ((1 - frac) if t == hour else 1) for t, v in vals.items() if t >= hour)
            sunny = [d["cloud"][t] for t in hours if t in d["cloud"] and (d["model"].get(t) or 0) > 0]
            base["days"].append({"date": day.isoformat(), "kwh": round(sum(v or 0 for v in vals.values()) / 1000, 1),
                                 "rest_kwh": round(rest / 1000, 1) if i == 0 else None,
                                 "cloud_pct": round(sum(sunny) / len(sunny)) if sunny else None})
        base["usual_pv_kwh"], base["usual_load_kwh"] = self._usual()
        prof = self.load() or {}
        if len(prof) >= 20:                                              # the usual load by local hour, for the card's dashed line
            base["load_by_hour"] = [prof.get(h) for h in range(24)]
        recent = []
        for i in range(7, 0, -1):
            day = now.date() - timedelta(days=i)
            f, a = d["evening"].get(day.isoformat()), self._made(day)
            if isinstance(f, (int, float)) and a is not None:
                recent.append({"date": day.isoformat(), "forecast_kwh": f, "made_kwh": a})
        base["recent"] = recent
        made = sum(r["made_kwh"] for r in recent)
        if len(recent) >= 3 and made > 0:
            base["miss_pct"] = round(100 * sum(abs(r["forecast_kwh"] - r["made_kwh"]) for r in recent) / made)
        return base

    def hours(self) -> dict:
        """Every hour known, for the Charts tab: the frozen past and the forecast from now, [hour start, W]."""
        if not self.store.configured():
            return {"configured": False, "hours": []}
        d, hour = self.store.data, self._hour()
        k = self.factor()[0]
        merged = {t: w for t, w in d["frozen"].items() if t < hour}
        merged.update({t: round(w * k, 1) for t, w in d["model"].items() if t >= hour})
        return {"configured": True, "hours": [[t, w] for t, w in sorted(merged.items())]}

    # -- once a minute: note tomorrow in the evening, and say so when it is a weak day
    def evening(self) -> None:
        cfg, d = self.store.config, self.store.data
        now = self.now()
        if not self.store.configured() or now.hour < EVENING_HOUR or not d["model"]:
            return
        tomorrow = now.date() + timedelta(days=1)
        key = tomorrow.isoformat()
        vals = [self.watts(t) for t in self._day_hours(tomorrow)]
        if any(v is None for v in vals):
            return
        kwh = round(sum(vals) / 1000, 1)
        if key not in d["evening"]:
            d["evening"][key] = kwh
            oldest = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
            d["evening"] = {k: v for k, v in d["evening"].items() if k >= oldest}
            self.store.save_data()
        if not cfg["telegram"] or d.get("telegram_for") == key:
            return
        usual_pv, usual_load = self._usual()
        if not usual_pv or kwh >= cfg["below_pct"] / 100 * usual_pv:
            return
        d["telegram_for"] = key
        self.store.save_data()
        house = f" The house usually uses ≈ {usual_load:g} kWh a day." if usual_load else ""
        self.send(f"☁️ Tomorrow's solar forecast: ≈ {kwh:g} kWh, {round(100 * kwh / usual_pv)}% of a usual day ({usual_pv:g} kWh).{house}")

    def run_in_background(self, stop: threading.Event) -> threading.Thread:
        def work():
            last = -1e9
            while not stop.is_set():
                try:
                    if self.store.configured() and self.clock() - last >= FETCH_EVERY_S:
                        self.refresh()
                        last = self.clock()
                    self.evening()
                except Exception as exc:
                    log.warning("solar forecast: %s", exc)
                if stop.wait(60):
                    return
        t = threading.Thread(target=work, daemon=True, name="forecast")
        t.start()
        return t

    def reconfigured(self) -> None:
        threading.Thread(target=self.refresh, daemon=True, name="forecast-now").start()


def actual_flux(bucket: str) -> tuple[str, str]:
    """PV power (mean) and battery SOC (max) by hour over the last two weeks and a bit."""
    def q(field, fn):
        return (f'from(bucket: "{bucket}") |> range(start: -{PAST_DAYS + 1}d) '
                f'|> filter(fn: (r) => r._measurement == "solis" and r._field == "{field}") '
                f'|> aggregateWindow(every: 1h, fn: {fn}, createEmpty: false, timeSrc: "_start")')
    return q("pv_power", "mean"), q("battery_soc", "max")


def actual_rows(pv_rows, soc_rows) -> dict[int, tuple[float | None, float | None]]:
    """(hour start, value) rows of both queries -> hour -> (PV W, SOC max)."""
    soc = {int(t.timestamp()): v for t, v in soc_rows if v is not None}
    return {int(t.timestamp()): (float(v), soc.get(int(t.timestamp()))) for t, v in pv_rows if v is not None}
