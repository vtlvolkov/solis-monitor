"""Pure data shaping, kept free of I/O so it can be unit-tested."""
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .validation import FIELDS

ONLINE_MAX_AGE_S = 45


def live_payload(latest, now: datetime) -> dict:
    """latest: (time, {field: value}) or None."""
    if not latest:
        return {"time": None, "age_s": None, "online": False, "fields": {f: None for f in FIELDS}}
    ts, vals = latest
    age = max(0.0, (now - ts).total_seconds())
    fields = {f: _num(vals.get(f)) for f in FIELDS}
    return {"time": ts.isoformat(), "age_s": round(age, 1),
            "online": age <= ONLINE_MAX_AGE_S, "fields": fields}


def _num(v):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 3)


def insert_gaps(times: list[datetime], series: dict[str, list], step_s: int, factor: float = 3.0):
    """Insert an all-null row where consecutive points are more than factor*step apart,
    so charts show a gap during an outage instead of a straight line across it."""
    out_t, out = [], {k: [] for k in series}
    for i, t in enumerate(times):
        if i and (t - times[i - 1]).total_seconds() > factor * max(step_s, 10):
            out_t.append(times[i - 1] + (t - times[i - 1]) / 2)
            for k in out:
                out[k].append(None)
        out_t.append(t)
        for k in out:
            out[k].append(series[k][i])
    return out_t, out


def daily_start(now: datetime, days: int, tz: str) -> datetime:
    """UTC instant of local midnight (days-1) days before today."""
    zone = ZoneInfo(tz)
    local_today = now.astimezone(zone).date()
    first = local_today - timedelta(days=days - 1)
    return datetime.combine(first, datetime.min.time(), zone).astimezone(timezone.utc)


def aggregate_dates(rows, fields, dates: list[date], tz: str) -> list[dict]:
    """rows: (window_start_utc, field, max_value) for sub-day windows.
    Groups by local calendar day and keeps the max of each *_today counter.
    Every given date is returned; days without data have null values."""
    zone = ZoneInfo(tz)
    acc: dict[date, dict[str, float]] = {d: {} for d in dates}
    for t, field, v in rows:
        d = t.astimezone(zone).date()
        if d in acc and field in fields and v is not None:
            cur = acc[d].get(field)
            acc[d][field] = v if cur is None else max(cur, v)
    return [{"date": d.isoformat(), **{f: _num(acc[d].get(f)) for f in fields}} for d in dates]


def aggregate_daily(rows, fields, days: int, now: datetime, tz: str) -> list[dict]:
    """The last `days` local days up to today, see aggregate_dates."""
    today = now.astimezone(ZoneInfo(tz)).date()
    return aggregate_dates(rows, fields, [today - timedelta(days=i) for i in range(days - 1, -1, -1)], tz)


def pair_events(rows: list[dict], now: datetime) -> dict:
    """rows: solis_event points (time, kind, key, state, name, severity, soc, load), oldest first.
    Pairs start/end per key. A repeated start (poller restarted mid-event) keeps the first one;
    an end without a start (event began before the range) is shown with an unknown start."""
    open_, done = {}, []
    for r in rows:
        k = (r["kind"], r["key"])
        if r.get("state") == "start":
            open_.setdefault(k, r)
        elif r.get("state") == "end":
            st = open_.pop(k, None)
            done.append(_event(st, r, now))
    ongoing = [_event(st, None, now) for st in open_.values()]
    allev = sorted(done + ongoing, key=lambda e: e["end"] or e["start"] or "", reverse=True)
    return {"outages": [e for e in allev if e["kind"] == "outage"],
            "alarms": [e for e in allev if e["kind"] == "alarm"],
            "active": [e for e in ongoing if e["kind"] == "alarm"],
            "outage_now": next((e for e in ongoing if e["kind"] == "outage"), None)}


def _event(start, end, now):
    ref = start or end
    t0 = start["time"] if start else None
    t1 = end["time"] if end else None
    dur = ((t1 or now) - t0).total_seconds() if t0 else None
    return {"kind": ref["kind"], "key": ref["key"], "name": ref.get("name"), "severity": ref.get("severity"),
            "start": t0.isoformat() if t0 else None, "end": t1.isoformat() if t1 else None,
            "ongoing": end is None, "duration_s": round(dur) if dur is not None else None,
            "soc_start": _num(start.get("soc")) if start else None,
            "soc_end": _num(end.get("soc")) if end else None,
            "load_start": _num(start.get("load")) if start else None}


def period_sums(days: list[dict], fields, today: date) -> dict:
    """Month-to-date and year-to-date sums of daily kWh (days from aggregate_daily)."""
    out = {"month": {f: 0.0 for f in fields}, "year": {f: 0.0 for f in fields}, "first_day": None}
    for d in days:
        day = date.fromisoformat(d["date"])
        if any(d.get(f) is not None for f in fields) and out["first_day"] is None:
            out["first_day"] = d["date"]
        for f in fields:
            v = d.get(f)
            if v is None:
                continue
            if day.year == today.year:
                out["year"][f] += v
                if day.month == today.month:
                    out["month"][f] += v
    for p in ("month", "year"):
        out[p] = {f: round(v, 1) for f, v in out[p].items()}
    return out


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(minute: int, start: str, end: str) -> bool:
    """minute of day inside [start, end), windows may cross midnight."""
    a, b = _minutes(start), _minutes(end)
    return a <= minute < b if a < b else minute >= a or minute < b


def import_costs(rows, tz: str, tariff_at) -> dict[date, dict]:
    """rows: (window_start_utc, max of grid_import_today in that window), any order.
    The counter resets at local midnight, so each window's increment is its value minus the
    previous window's value on the same day. Each increment is priced with the tariff that was in
    force when its window started (`tariff_at(utc) -> Tariff | None`), split into day/night zone by
    that tariff's hours, so a later price change never alters the cost of earlier windows."""
    zone = ZoneInfo(tz)
    out: dict[date, dict] = {}
    prev_day, prev = None, 0.0
    for t, v in sorted(rows):
        local = t.astimezone(zone)
        d = local.date()
        if d != prev_day:
            prev_day, prev = d, 0.0
        inc = max(0.0, v - prev)
        prev = max(prev, v)
        tf = tariff_at(t)
        night = bool(tf and tf.night is not None and tf.night_start and tf.night_end
                     and in_window(local.hour * 60 + local.minute, tf.night_start, tf.night_end))
        z = out.setdefault(d, {"day_kwh": 0.0, "night_kwh": 0.0, "day_cost": 0.0, "night_cost": 0.0, "saved": 0.0})
        if night:
            z["night_kwh"] += inc
            z["night_cost"] += inc * tf.night
            z["saved"] += inc * (tf.day - tf.night)
        else:
            z["day_kwh"] += inc
            z["day_cost"] += inc * (tf.day if tf else 0.0)
    return out


def rate_now(now: datetime, tz: str, tariff) -> dict | None:
    """Which tariff zone is active at `now` (local time), its price and when it ends; None for a single-rate tariff."""
    if tariff is None or tariff.night is None or not tariff.night_start or not tariff.night_end:
        return None
    local = now.astimezone(ZoneInfo(tz))
    night = in_window(local.hour * 60 + local.minute, tariff.night_start, tariff.night_end)
    return {"zone": "night" if night else "day", "price": tariff.night if night else tariff.day,
            "until": tariff.night_end if night else tariff.night_start}


def cost_summary(zones: dict[date, dict], today: date) -> dict:
    def block(days):
        s = {k: sum(zones[d][k] for d in days) for k in ("day_kwh", "night_kwh", "day_cost", "night_cost", "saved")}
        return {"day_kwh": round(s["day_kwh"], 2), "night_kwh": round(s["night_kwh"], 2),
                "kwh": round(s["day_kwh"] + s["night_kwh"], 2),
                "day_cost": round(s["day_cost"], 2), "night_cost": round(s["night_cost"], 2),
                "cost": round(s["day_cost"] + s["night_cost"], 2), "saved": round(s["saved"], 2)}
    month_days = [d for d in zones if d.year == today.year and d.month == today.month]
    return {"today": block([d for d in zones if d == today]), "month": block(month_days)}


# ---------------------------------------------------------------- cost history
def parse_month(month: str) -> tuple[int, int] | None:
    """'2026-10' -> (2026, 10), or None if it is not a valid year-month."""
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2 or not (parts[0] + parts[1]).isdigit():
        return None
    y, m = int(parts[0]), int(parts[1])
    return (y, m) if 2000 <= y <= 2100 and 1 <= m <= 12 else None


def month_dates(year: int, month: int, today: date) -> list[date]:
    """Every day of the month, but not past today."""
    d = date(year, month, 1)
    out = []
    while d.month == month and d <= today:
        out.append(d)
        d += timedelta(days=1)
    return out


def local_range(first: date, last: date, tz: str) -> tuple[datetime, datetime]:
    """UTC instants of local midnight at the start of `first` and at the end of `last`."""
    zone = ZoneInfo(tz)
    start = datetime.combine(first, datetime.min.time(), zone)
    stop = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone)
    return start.astimezone(timezone.utc), stop.astimezone(timezone.utc)


def cost_days(energy: list[dict], zones: dict[date, dict]) -> dict:
    """Per-day consumption and cost for the cost history page.
    energy: aggregate_dates() output (the *_today maxima per day); zones: import_costs()."""
    days = []
    for e in energy:
        z = zones.get(date.fromisoformat(e["date"]))
        if z is None:
            days.append({"date": e["date"], "has_data": False})
            continue
        dk, nk = z["day_kwh"], z["night_kwh"]
        days.append({
            "date": e["date"], "has_data": True,
            "day_kwh": round(dk, 2), "night_kwh": round(nk, 2), "kwh": round(dk + nk, 2),
            "day_cost": round(z["day_cost"], 2), "night_cost": round(z["night_cost"], 2),
            "cost": round(z["day_cost"] + z["night_cost"], 2), "saved": round(z["saved"], 2),
            "load_kwh": e["load_consumption_today"], "export_kwh": e["grid_export_today"], "pv_kwh": e["pv_energy_today"],
            "charge_kwh": e["battery_charge_today"], "discharge_kwh": e["battery_discharge_today"],
        })
    have = [d for d in days if d["has_data"]]
    total = {k: round(sum(d[k] for d in have), 2)
             for k in ("day_kwh", "night_kwh", "kwh", "day_cost", "night_cost", "cost", "saved")}
    total["days"] = len(have)
    for k in ("load_kwh", "export_kwh", "pv_kwh", "charge_kwh", "discharge_kwh"):
        total[k] = round(sum(d[k] or 0 for d in have), 2)
    total["avg_cost"] = round(total["cost"] / len(have), 2) if have else None
    total["avg_kwh"] = round(total["kwh"] / len(have), 2) if have else None
    return {"days": days, "total": total}
