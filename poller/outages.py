"""
Planned outage schedule (графік відключень) from Yasno, DTEK's own service for Kyiv and Dnipro.

Yasno has no documented API; this uses the JSON its website and app load (the same as the Home Assistant
integrations), checked live on 2026-11-13:

    GET {SHUTDOWNS}/regions/{region}/dsos/{dso}/planned-outages
        -> {"3.1": {"today": {"slots": [{"start": 0, "end": 60, "type": "Definite"}, ...], "date": "...",
                              "status": "ScheduleApplies" | "WaitingForSchedule" | ...},
                    "tomorrow": {...}, "updatedOn": "..."}, ...}
    GET {SHUTDOWNS}/addresses/v2/streets?regionId=&dsoId=&query=      -> [{"id": 911, "value": "вул. Хрещатик"}]
    GET {SHUTDOWNS}/addresses/v2/houses?regionId=&dsoId=&streetId=&query=  -> [{"id": 39986, "value": "22"}] (at most 20)
    GET {SHUTDOWNS}/addresses/v2/group?regionId=&dsoId=&streetId=&houseId= -> {"group": 20, "subgroup": 1}

Slots are minutes from local midnight; "Definite" is an outage, "NotPlanned" is power, anything else with a
duration is shown as a possible outage.

DTEK's regional sites (Kyiv Oblast, Dnipro, Odesa) sit behind an anti-bot wall a server cannot pass without a
headless browser, so those come from the community mirror github.com/Baskerville42/outage-data-ua, which runs one
and publishes each region as JSON every few minutes (checked live 2026-11-13):

    {"fact": {"today": <unix time of local midnight>, "update": "13.11.2026 14:23",
              "data": {"<unix midnight>": {"GPV3.1": {"1": "no", "2": "first", ..., "24": "yes"}, ...}}},
     "preset": {"sch_names": {"GPV3.1": "Черга 3.1", ...}, ...}, "lastUpdated": "...Z", "lastUpdateStatus": {"ok": true, ...}}

Hour "1" is 00:00-01:00; "no" is an outage, "first"/"second" the first/second half hour, "maybe"/"mfirst"/"msecond"
possible ones. There is no address lookup for these: the page links to DTEK's own address search instead. The poller owns this because it has what the battery verdict needs (the
live SOC, the capacity and the backup floor, the load history) and sends the Telegram messages. Only the group is
kept, never the address; nothing is fetched while the source is off.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("solis-poller")

SHUTDOWNS = "https://app.yasno.ua/api/blackout-service/public/shutdowns"
MIRROR = "https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data"
SOURCES = {
    "yasno-kyiv": {"kind": "yasno", "label": "Yasno: Kyiv", "region_id": 25, "dso_id": 902, "region": "Київ", "provider": "ДТЕК Київські електромережі"},
    "yasno-dnipro-dnem": {"kind": "yasno", "label": "Yasno: Dnipro (DnEM)", "region_id": 3, "dso_id": 301, "region": "Дніпро", "provider": "ДнЕМ"},
    "yasno-dnipro-cek": {"kind": "yasno", "label": "Yasno: Dnipro (CEK)", "region_id": 3, "dso_id": 303, "region": "Дніпро", "provider": "ЦЕК"},
    "dtek-kyiv-region": {"kind": "mirror", "label": "DTEK: Kyiv Oblast", "file": "kyiv-region", "region": "Київська обл.",
                         "provider": "ДТЕК Київські регіональні електромережі", "find": "https://www.dtek-krem.com.ua/ua/shutdowns"},
    "dtek-dnipro": {"kind": "mirror", "label": "DTEK: Dnipro and Oblast", "file": "dnipro", "region": "Дніпро та обл.",
                    "provider": "ДТЕК Дніпровські електромережі", "find": "https://www.dtek-dnem.com.ua/ua/shutdowns"},
    "dtek-odesa": {"kind": "mirror", "label": "DTEK: Odesa and Oblast", "file": "odesa", "region": "Одеса та обл.",
                   "provider": "ДТЕК Одеські електромережі", "find": "https://www.dtek-oem.com.ua/ua/shutdowns"},
}
GROUP = re.compile(r"\d{1,4}\.\d{1,2}")
MIRROR_STALE_S = 3 * 3600          # the mirror scrapes every few minutes: hours without a good scrape is worth saying
HALF = {"no": ((0, 60), "Definite"), "first": ((0, 30), "Definite"), "second": ((30, 60), "Definite"),
        "maybe": ((0, 60), "Possible"), "mfirst": ((0, 30), "Possible"), "msecond": ((30, 60), "Possible")}
FETCH_EVERY_S = 15 * 60
ALERT_BEFORE_S = 30 * 60
DEFAULT_ALERTS = {"before": True, "short": True, "changes": True}
TIMEOUT_S = 15


class OutageError(Exception):
    pass


# ---------------------------------------------------------------- Yasno
class Yasno:
    def __init__(self, base: str = SHUTDOWNS, opener=None):
        self.base, self.opener = base, opener or urllib.request.urlopen

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.base}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "solis-monitor"})
        try:
            with self.opener(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read(2_000_000))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None                                  # EntryNotFound: an address Yasno does not know
            raise OutageError(f"Yasno answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OutageError(f"Yasno not reachable ({type(exc).__name__})") from exc

    def planned(self, source: str) -> dict:
        s = SOURCES[source]
        answer = self._get(f"regions/{s['region_id']}/dsos/{s['dso_id']}/planned-outages")
        if not isinstance(answer, dict):
            raise OutageError("Yasno sent no schedule")
        return answer

    def _address(self, kind: str, source: str, **params) -> list[dict]:
        s = SOURCES[source]
        answer = self._get(f"addresses/v2/{kind}", {"regionId": s["region_id"], "dsoId": s["dso_id"], **params})
        if answer is None:
            return []
        if not isinstance(answer, list):
            raise OutageError("Yasno sent an unexpected answer")
        return [{"id": int(x["id"]), "value": str(x["value"])} for x in answer
                if isinstance(x, dict) and isinstance(x.get("id"), int) and not isinstance(x.get("id"), bool) and isinstance(x.get("value"), str)][:20]

    def streets(self, source: str, query: str) -> list[dict]:
        return self._address("streets", source, query=query)

    def houses(self, source: str, street_id: int, query: str) -> list[dict]:
        return self._address("houses", source, streetId=street_id, query=query)

    def group(self, source: str, street_id: int, house_id: int) -> str | None:
        s = SOURCES[source]
        answer = self._get("addresses/v2/group", {"regionId": s["region_id"], "dsoId": s["dso_id"], "streetId": street_id, "houseId": house_id})
        if isinstance(answer, dict) and type(answer.get("group")) is int and type(answer.get("subgroup")) is int:
            return f"{answer['group']}.{answer['subgroup']}"
        return None


class Mirror:
    """The community mirror of DTEK's regional schedules (see the module docstring)."""

    def __init__(self, base: str = MIRROR, opener=None, clock=time.time):
        self.base, self.opener, self.clock = base, opener or urllib.request.urlopen, clock
        self._cache: dict[str, tuple[float, dict]] = {}

    def fetch(self, source: str, max_age_s: float = 0) -> dict:
        at, data = self._cache.get(source, (-1e9, None))
        if data is not None and self.clock() - at < max_age_s:
            return data
        url = f"{self.base}/{SOURCES[source]['file']}.json"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "solis-monitor"})
        try:
            with self.opener(req, timeout=TIMEOUT_S) as r:
                data = json.loads(r.read(5_000_000))
        except urllib.error.HTTPError as exc:
            raise OutageError(f"The community data answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OutageError(f"The community data is not reachable ({type(exc).__name__})") from exc
        if not isinstance(data, dict) or not isinstance(data.get("fact"), dict):
            raise OutageError("The community data has an unexpected shape")
        self._cache[source] = (self.clock(), data)
        return data

    def groups(self, source: str) -> list[dict]:
        """The groups of today's schedule, as {value: "3.1", label: "Черга 3.1"}, in their natural order."""
        data = self.fetch(source, max_age_s=600)
        names = (data.get("preset") or {}).get("sch_names") or {}
        days = (data["fact"].get("data") or {}).values()
        keys = sorted({k for d in days if isinstance(d, dict) for k in d}, key=_group_order)
        return [{"value": k.removeprefix("GPV"), "label": str(names.get(k) or f"Черга {k.removeprefix('GPV')}")}
                for k in keys if GROUP.fullmatch(k.removeprefix("GPV"))]


def _group_order(key: str):
    try:
        a, b = key.removeprefix("GPV").split(".")
        return int(a), int(b)
    except ValueError:
        return 10**9, 0


def mirror_slots(hours: dict) -> list[dict]:
    """{"1": "no", "2": "first", ...} -> outage slots in minutes, touching ones of the same kind merged."""
    out = []
    for h in range(1, 25):
        v = hours.get(str(h))
        if v not in HALF:
            continue
        (a, b), kind = HALF[v]
        start, end = (h - 1) * 60 + a, (h - 1) * 60 + b
        if out and out[-1]["type"] == kind and out[-1]["end"] == start:
            out[-1]["end"] = end
        else:
            out.append({"start": start, "end": end, "type": kind})
    return out


def parse_mirror(data: dict, group: str, tz: str, now: datetime) -> tuple[list[dict], str | None, str | None]:
    """(days, updated_on, warning): today and tomorrow of one group, DTEK's own update time, and a warning when the
    mirror has not scraped DTEK successfully for hours."""
    zone, fact = ZoneInfo(tz), data["fact"]
    by_day = fact.get("data") if isinstance(fact.get("data"), dict) else {}
    key = f"GPV{group}"
    if not any(isinstance(d, dict) and key in d for d in by_day.values()):
        raise OutageError(f"The community data has no group {group}")
    today = now.astimezone(zone).date()
    days = []
    for offset in (0, 1):
        date = today + timedelta(days=offset)
        midnight = datetime(date.year, date.month, date.day, tzinfo=zone)
        hours = next((d.get(key) for ts, d in by_day.items()
                      if isinstance(d, dict) and str(ts).isdigit() and datetime.fromtimestamp(int(ts), zone).date() == date), None)
        days.append({"date": midnight.isoformat(), "status": "ScheduleApplies" if isinstance(hours, dict) else "WaitingForSchedule",
                     "slots": mirror_slots(hours) if isinstance(hours, dict) else []})
    updated = None
    try:
        updated = datetime.strptime(str(fact.get("update")), "%d.%m.%Y %H:%M").replace(tzinfo=zone).isoformat()
    except ValueError:
        pass
    warning = None
    status = data.get("lastUpdateStatus") if isinstance(data.get("lastUpdateStatus"), dict) else {}
    try:
        scraped = datetime.fromisoformat(str(data.get("lastUpdated")).replace("Z", "+00:00"))
    except ValueError:
        scraped = None
    if status.get("ok") is False or (scraped is not None and (now - scraped).total_seconds() > MIRROR_STALE_S):
        warning = f"The community data has not been refreshed from DTEK since {scraped.astimezone(zone).strftime('%d.%m %H:%M') if scraped else 'a while'}."
    return days, updated, warning


def parse_group(answer: dict, group: str) -> list[dict]:
    """The days (today, tomorrow) of one group: {date, status, slots: [{start, end, type}]}, outages only, checked."""
    g = answer.get(group)
    if not isinstance(g, dict):
        raise OutageError(f"Yasno has no group {group}")
    days = []
    for key in ("today", "tomorrow"):
        day = g.get(key)
        if not isinstance(day, dict):
            continue
        slots = []
        for s in day.get("slots") or []:
            if not isinstance(s, dict) or type(s.get("start")) is not int or type(s.get("end")) is not int:
                continue
            start, end, kind = s["start"], s["end"], s.get("type")
            if not 0 <= start < end <= 1440 or kind == "NotPlanned":
                continue
            slots.append({"start": start, "end": end, "type": "Definite" if kind == "Definite" else "Possible"})
        days.append({"date": day.get("date") if isinstance(day.get("date"), str) else None,
                     "status": day.get("status") if isinstance(day.get("status"), str) else "ScheduleApplies", "slots": slots})
    return days


def intervals(days: list[dict], tz: str, kinds=("Definite",)) -> list[tuple[datetime, datetime]]:
    """The outages of the days as (start, end) in the site's zone, touching ones merged."""
    zone, out = ZoneInfo(tz), []
    for day in days:
        if not day.get("date") or day.get("status") != "ScheduleApplies":
            continue
        midnight = datetime.fromisoformat(day["date"]).astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        for s in sorted(day["slots"], key=lambda x: x["start"]):
            if s["type"] not in kinds:
                continue
            a, b = midnight + timedelta(minutes=s["start"]), midnight + timedelta(minutes=s["end"])
            if out and out[-1][1] >= a:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
    return out


def next_outage(days: list[dict], now: datetime, tz: str) -> tuple[datetime, datetime] | None:
    return next(((a, b) for a, b in intervals(days, tz) if b > now), None)


# ---------------------------------------------------------------- will the battery last?
def verdict(start: datetime, end: datetime, now: datetime, soc: float | None, floor: float | None, capacity_kwh: float | None,
            profile: dict[int, float], load_now: float | None, battery_w: float | None, max_soc: float = 100.0, tz: str = "Europe/Kyiv") -> dict | None:
    """Energy above the backup floor when the outage starts against the usual load of those hours, minute by minute.
    profile: local hour -> average load in W (the last two weeks); the current load fills hours without history.
    Before the outage the battery keeps its current charging (or discharging) rate, capped at max_soc and the floor."""
    if soc is None or floor is None or not capacity_kwh or capacity_kwh <= 0:
        return None
    zone = ZoneInfo(tz)
    wh_per_pct = capacity_kwh * 10.0
    until_h = max(0.0, (start - now).total_seconds() / 3600)
    soc_start = soc + (battery_w or 0.0) * until_h / wh_per_pct if until_h else soc
    soc_start = max(floor, min(max_soc, soc_start))
    left = (soc_start - floor) * wh_per_pct
    t, used, minutes = max(start, now), 0.0, 0

    def load_at(at):
        return profile.get(at.astimezone(zone).hour) or load_now or 0.0
    span = [load_at(max(start, now) + timedelta(minutes=i)) for i in range(int((end - max(start, now)).total_seconds() // 60))]
    load_w = round(sum(span) / len(span)) if span else None      # the usual load over the whole outage, not only until it runs out
    while t < end:
        w = load_at(t)
        if used + w / 60 > left + 1e-6:        # (a sum of 1/60 steps drifts above an exact limit)
            break
        used += w / 60
        t += timedelta(minutes=1)
        minutes += 1
    if t < end:                                                   # runs out before the end
        return {"enough": False, "lasts_s": minutes * 60, "short_s": round((end - t).total_seconds()), "runs_out_at": t.astimezone(zone).strftime("%H:%M"),
                "load_w": load_w, "soc_at_start": round(soc_start)}
    extra = 0                                                     # enough: how much longer it would go on
    while extra < 72 * 60:
        w = load_at(t)
        if w <= 0 or used + w / 60 > left + 1e-6:
            break
        used += w / 60
        t += timedelta(minutes=1)
        extra += 1
    return {"enough": True, "lasts_s": (minutes + extra) * 60, "short_s": None, "runs_out_at": None, "load_w": load_w, "soc_at_start": round(soc_start)}


# ---------------------------------------------------------------- what is kept in /data
class OutageStore:
    """The source, group and Telegram choices (outages.json), and the last good schedule (outage-schedule.json)."""

    def __init__(self, directory: str | None):
        self.dir = directory or None
        self.lock = threading.Lock()
        self.config = self._read("outages.json") or {}
        self.schedule = self._read("outage-schedule.json")       # {"fetched_at", "updated_on", "days", "source", "group"}

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
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(self.dir, name))

    @property
    def source(self) -> str | None:
        s = self.config.get("source")
        return s if s in SOURCES else None

    @property
    def group(self) -> str | None:
        g = self.config.get("group")
        return g if isinstance(g, str) and GROUP.fullmatch(g) else None

    @property
    def alerts(self) -> dict:
        a = self.config.get("alerts") if isinstance(self.config.get("alerts"), dict) else {}
        return {k: bool(a.get(k, v)) for k, v in DEFAULT_ALERTS.items()}

    def configured(self) -> bool:
        return bool(self.source and self.group)

    @property
    def show_card(self) -> bool:
        """Whether the Overview shows the Planned outages card (also its "Set up" prompt while off)."""
        return self.config.get("overview") is not False

    def set_config(self, source: str | None, group: str | None, alerts: dict, overview: bool | None = None) -> None:
        if source is not None and source not in SOURCES:
            raise ValueError("unknown source")
        if source is not None and (not isinstance(group, str) or not GROUP.fullmatch(group)):
            raise ValueError("a group like 20.1 is needed")
        if not isinstance(alerts, dict) or set(alerts) - set(DEFAULT_ALERTS) or not all(isinstance(v, bool) for v in alerts.values()):
            raise ValueError("alerts must be {before, short, changes} as true or false")
        if overview is not None and not isinstance(overview, bool):
            raise ValueError("overview must be true or false")
        with self.lock:
            changed = (source, group) != (self.source, self.group)
            self.config = {"source": source, "group": group if source else None, "alerts": {**DEFAULT_ALERTS, **alerts},
                           "overview": self.show_card if overview is None else overview}
            self._write("outages.json", self.config)
            if changed:
                self.schedule = None                             # another group: what we had says nothing about it
                if self.dir:
                    try:
                        os.remove(os.path.join(self.dir, "outage-schedule.json"))
                    except OSError:
                        pass

    def keep_schedule(self, schedule: dict) -> None:
        with self.lock:
            self.schedule = schedule
            self._write("outage-schedule.json", schedule)


# ---------------------------------------------------------------- the watcher
class OutageWatcher:
    """Fetches the schedule every 15 minutes while a source is set, keeps the last good copy, and sends the Telegram
    messages: 30 minutes before an outage, when the battery will not last, and when the schedule changes.

    `state()` returns (soc, battery_w, load_w) from the latest poll; `battery()` (capacity_kwh, floor_soc, max_soc) from the
    device cache; `profile()` the usual load by local hour."""

    def __init__(self, store: OutageStore, yasno: Yasno, tz: str, send=lambda text: None, state=lambda: (None, None, None),
                 battery=lambda: None, profile=lambda: {}, clock=time.time, mirror: "Mirror | None" = None):
        self.store, self.yasno, self.tz = store, yasno, tz
        self.mirror = mirror or Mirror(clock=clock)
        self.warning: str | None = None                        # the source works but its data may be old
        self.send, self.state, self.battery, self.profile, self.clock = send, state, battery, profile, clock
        self.last_error: str | None = None
        self._alerted: set[str] = set()                        # "before:<start>", "short:<start>" already sent

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc).astimezone(ZoneInfo(self.tz))

    # -- fetching
    def refresh(self) -> bool:
        source, group = self.store.source, self.store.group
        if not source or not group:
            return False
        try:
            if SOURCES[source]["kind"] == "mirror":
                days, updated, self.warning = parse_mirror(self.mirror.fetch(source), group, self.tz, self.now())
            else:
                answer = self.yasno.planned(source)
                days = parse_group(answer, group)
                updated = answer.get(group, {}).get("updatedOn") if isinstance(answer.get(group), dict) else None
                self.warning = None
        except OutageError as exc:
            self.last_error = str(exc)
            log.warning("outage schedule: %s", exc)
            return False
        old = self.store.schedule
        new = {"source": source, "group": group, "fetched_at": self.now().isoformat(), "updated_on": updated if isinstance(updated, str) else None, "days": days}
        self.last_error = None
        self.store.keep_schedule(new)
        if old and old.get("group") == group and self.store.alerts["changes"]:
            self._announce_changes(old["days"], days)
        return True

    def _announce_changes(self, old: list[dict], new: list[dict]) -> None:
        before = {d["date"][:10]: d for d in old if d.get("date")}
        for day in new:
            if not day.get("date") or day["status"] != "ScheduleApplies":
                continue
            prev = before.get(day["date"][:10])
            if prev is not None and prev["status"] == "ScheduleApplies" and prev["slots"] == day["slots"]:
                continue
            is_tomorrow = datetime.fromisoformat(day["date"]).astimezone(ZoneInfo(self.tz)).date() > self.now().date()
            published = prev is None or prev["status"] != "ScheduleApplies"
            head = ("📅 Tomorrow's schedule" if is_tomorrow else "📅 Today's schedule") + (" is out" if published else " changed")
            self.send(f"{head} for group {self.store.group}: {describe_slots(day['slots'])}.")

    # -- what the dashboard shows
    def payload(self) -> dict:
        s = SOURCES.get(self.store.source or "", {})
        base = {"configured": self.store.configured(), "show": self.store.show_card,
                "source": ("Yasno" if s.get("kind") == "yasno" else s.get("label")) if s else None, "kind": s.get("kind"),
                "region": s.get("region"), "provider": s.get("provider"),
                "group": self.store.group, "updated_on": None, "fetched_at": None, "stale": False, "error": self.last_error, "warning": self.warning,
                "days": [], "next": None, "battery": None}
        sch = self.store.schedule
        if not base["configured"] or not sch or sch.get("group") != self.store.group:
            return base
        now = self.now()
        fetched = datetime.fromisoformat(sch["fetched_at"]) if sch.get("fetched_at") else None
        base.update(updated_on=sch.get("updated_on"), fetched_at=sch.get("fetched_at"),
                    stale=bool(self.last_error) or (fetched is not None and (now - fetched).total_seconds() > 3 * FETCH_EVERY_S),
                    days=[d for d in sch["days"] if not d.get("date") or datetime.fromisoformat(d["date"]).astimezone(ZoneInfo(self.tz)).date() >= now.date()])
        nxt = next_outage(base["days"], now, self.tz)
        if nxt:
            a, b = nxt
            base["next"] = {"start": a.isoformat(), "end": b.isoformat(), "duration_s": round((b - a).total_seconds()),
                            "in_s": max(0, round((a - now).total_seconds())), "ongoing": a <= now < b}
            base["battery"] = self._verdict(a, b, now)
        return base

    def _verdict(self, a, b, now):
        soc, battery_w, load_w = self.state()
        bat = self.battery()
        if not bat:
            return None
        capacity, floor, max_soc = bat
        return verdict(a, b, now, soc, floor, capacity, self.profile(), load_w, battery_w, max_soc or 100.0, self.tz)

    # -- Telegram, once a minute
    def check_alerts(self) -> None:
        if not self.store.configured():
            return
        p = self.payload()
        n, alerts = p.get("next"), self.store.alerts
        if not n or n["ongoing"]:
            return
        start, end = datetime.fromisoformat(n["start"]), datetime.fromisoformat(n["end"])
        span = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
        v = p.get("battery")
        if alerts["short"] and v and not v["enough"] and n["in_s"] <= 3 * 3600 and f"short:{n['start']}" not in self._alerted:
            self._alerted.add(f"short:{n['start']}")
            self.send(f"⚠️ The battery will not last through the {span} outage: runs out ≈ {v['runs_out_at']}, "
                      f"{fmt_minutes(v['short_s'])} short (battery {v['soc_at_start']}% at the start, about {v['load_w']} W).")
        if alerts["before"] and 0 < n["in_s"] <= ALERT_BEFORE_S and f"before:{n['start']}" not in self._alerted:
            self._alerted.add(f"before:{n['start']}")
            lasts = f", ≈ {fmt_minutes(v['lasts_s'])} at the usual load" if v else ""
            soc = self.state()[0]
            self.send(f"⏰ Planned outage in {fmt_minutes(n['in_s'])} ({span}, {fmt_minutes(n['duration_s'])}). "
                      f"Battery {round(soc) if soc is not None else '--'}%{lasts}.")
        if len(self._alerted) > 50:
            self._alerted = set(sorted(self._alerted)[-20:])

    def run_in_background(self, stop: threading.Event) -> threading.Thread:
        def work():
            last_fetch = -1e9
            while not stop.is_set():
                try:
                    if self.store.configured() and self.clock() - last_fetch >= FETCH_EVERY_S:
                        self.refresh()
                        last_fetch = self.clock()
                    self.check_alerts()
                except Exception as exc:
                    log.warning("outage schedule: %s", exc)
                if stop.wait(60):
                    return
        t = threading.Thread(target=work, daemon=True, name="outages")
        t.start()
        return t

    def reconfigured(self) -> None:
        """A new source or group was saved: fetch now rather than in up to 15 minutes."""
        threading.Thread(target=self.refresh, daemon=True, name="outages-now").start()


def load_profile(rows, tz: str) -> dict[int, float]:
    """rows: (hour start, mean load W) over the last weeks -> local hour -> average W."""
    zone, acc = ZoneInfo(tz), {}
    for t, w in rows:
        if w is None:
            continue
        acc.setdefault(t.astimezone(zone).hour, []).append(float(w))
    return {h: round(sum(v) / len(v)) for h, v in acc.items()}


def load_profile_flux(bucket: str) -> str:
    return (f'from(bucket: "{bucket}") |> range(start: -14d) '
            f'|> filter(fn: (r) => r._measurement == "solis" and r._field == "total_load_power") '
            f'|> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")')


def fmt_minutes(seconds: float) -> str:
    m = int(round(seconds / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def describe_slots(slots: list[dict]) -> str:
    if not slots:
        return "no outages"
    def hm(x):
        return f"{x // 60:02d}:{x % 60:02d}"
    return ", ".join(f"{hm(s['start'])}–{hm(s['end'])}" + (" (possible)" if s["type"] == "Possible" else "") for s in sorted(slots, key=lambda s: s["start"]))
