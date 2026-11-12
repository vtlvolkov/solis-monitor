"""Grid quality: how well the grid kept to the supply standard over a period (routes/grid.py <-> pages/GridQuality.tsx).

The heavy lifting (histograms, per-hour statistics, counts of readings and of excursions outside the limits) is done
in InfluxDB over the raw 10-second readings; this module validates the limits, groups the hourly rows by the site's
local hour and day, and shapes the answer. Readings during an outage (voltage below 100 V) are not grid quality and are
left out everywhere; outages come from the event log instead. Results are cached for five minutes per period and limits."""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from .. import shaping
from ..context import Context
from ..validation import parse_range

# The supply standard (EN 50160 / ДСТУ): 230 V ±10 %, 50 Hz ±0.2 Hz. The page may ask for other limits, within reason.
DEFAULT_LIMITS = {"v_min": 207.0, "v_max": 253.0, "hz_min": 49.8, "hz_max": 50.2}
LIMIT_BOUNDS = {"v_min": (150.0, 229.0), "v_max": (231.0, 300.0), "hz_min": (45.0, 49.99), "hz_max": (50.01, 55.0)}
V_VALID, HZ_VALID = 100.0, 40.0          # below these the grid is simply not there
V_BINS = (150.0, 1.0, 150)               # start, width, count -> 150..300 V by 1 V
HZ_BINS = (49.5, 0.01, 100)              # 49.50..50.50 Hz by 0.01 Hz
STEP_S = 10                              # the poller's rate; counts of readings become seconds with it
RANGES = {"24h", "7d", "30d"}
CACHE_S = 300


def parse_limits(v_min, v_max, hz_min, hz_max) -> dict:
    out = {}
    for key, raw in (("v_min", v_min), ("v_max", v_max), ("hz_min", hz_min), ("hz_max", hz_max)):
        val = DEFAULT_LIMITS[key] if raw is None else raw
        lo, hi = LIMIT_BOUNDS[key]
        if not isinstance(val, (int, float)) or not lo <= val <= hi:
            raise HTTPException(status_code=400, detail=f"{key} must be between {lo:g} and {hi:g}")
        out[key] = float(val)
    return out


def _stats(values: list[float]):
    return (min(values), sum(values) / len(values), max(values)) if values else (None, None, None)


def percentiles(bins: list[tuple[float, int]], width: float, qs=(0.05, 0.95)) -> list[float | None]:
    """Quantiles read off a histogram (to the bin's resolution)."""
    total = sum(c for _, c in bins)
    if not total:
        return [None for _ in qs]
    out, acc, i = [], 0, 0
    for q in qs:
        target = q * total
        while i < len(bins) and acc + bins[i][1] < target:
            acc += bins[i][1]
            i += 1
        out.append(round(bins[i][0] + width / 2, 2) if i < len(bins) else None)
    return out


def shape(rows: dict, limits: dict, start: datetime, now: datetime, tz: str, events: list[dict], rng: str) -> dict:
    """rows: the repo's answers (see build); pure, so the tests can feed it."""
    zone = ZoneInfo(tz)
    v_bins, hz_bins = rows["v_hist"], rows["hz_hist"]
    hourly = defaultdict(dict)                                   # hour start -> {min, max, mean, count, v_out, hz_out, sags, surges}
    for key in ("min", "max", "mean", "count", "v_out", "hz_out", "sags", "surges"):
        for t, v in rows[key]:
            hourly[t][key] = v
    by_hour, by_day = defaultdict(list), defaultdict(list)
    for t, h in sorted(hourly.items()):
        local = t.astimezone(zone)
        by_hour[local.hour].append(h)
        by_day[local.date()].append(h)

    def agg(items):
        mins = [h["min"] for h in items if "min" in h]
        maxs = [h["max"] for h in items if "max" in h]
        means = [h["mean"] for h in items if "mean" in h]
        count = sum(h.get("count", 0) for h in items)
        return {"min": min(mins) if mins else None, "max": max(maxs) if maxs else None,
                "mean": sum(means) / len(means) if means else None, "count": count,
                "v_out": sum(h.get("v_out", 0) for h in items), "hz_out": sum(h.get("hz_out", 0) for h in items),
                "sags": sum(h.get("sags", 0) for h in items), "surges": sum(h.get("surges", 0) for h in items)}

    whole = agg(list(hourly.values()))
    total = whole["count"] or 0
    hours = []
    for hr in range(24):
        a = agg(by_hour.get(hr, []))
        hours.append({"hour": hr, "mean": None if a["mean"] is None else round(a["mean"], 1), "min": a["min"], "max": a["max"],
                      "out_pct": None if not a["count"] else round(100 * a["v_out"] / a["count"], 1)})
    first_day, last_day = start.astimezone(zone).date(), now.astimezone(zone).date()
    paired = shaping.pair_events(events, now)
    outages = [e for e in paired["outages"] if e["start"] and datetime.fromisoformat(e["start"]) >= start or e["ongoing"] or
               (e["end"] and datetime.fromisoformat(e["end"]) >= start)]
    out_by_day = defaultdict(lambda: {"n": 0, "s": 0})
    durations = []
    for e in outages:
        t0 = datetime.fromisoformat(e["start"]) if e["start"] else start
        t1 = datetime.fromisoformat(e["end"]) if e["end"] else now
        t0 = max(t0, start)
        d = (t1 - t0).total_seconds()
        durations.append(d)
        day = t0.astimezone(zone).date()
        out_by_day[day]["n"] += 1
        out_by_day[day]["s"] += d
    days = []
    d = first_day
    while d <= last_day:
        a = agg(by_day.get(d, []))
        o = out_by_day.get(d, {"n": 0, "s": 0})
        days.append({"date": d.isoformat(), "readings": a["count"], "min_v": a["min"], "max_v": a["max"],
                     "mean_v": None if a["mean"] is None else round(a["mean"], 1),
                     "out_min": round(a["v_out"] * STEP_S / 60, 1), "sags": a["sags"], "surges": a["surges"],
                     "hz_out_min": round(a["hz_out"] * STEP_S / 60, 1), "outages": o["n"], "outage_s": round(o["s"])})
        d += timedelta(days=1)
    p05, p95 = percentiles(v_bins, V_BINS[1])
    hz_vals = [(b + HZ_BINS[1] / 2, c) for b, c in hz_bins if c]
    hz_total = sum(c for _, c in hz_vals)
    return {
        "range": rng, "step_s": STEP_S, "tz": tz, "limits": limits,
        "voltage": {"mean": None if whole["mean"] is None else round(whole["mean"], 1), "min": whole["min"], "max": whole["max"],
                    "p05": p05, "p95": p95, "bins": [{"from": b, "count": c} for b, c in v_bins if c], "bin_width": V_BINS[1]},
        "frequency": {"mean": None if not hz_total else round(sum(v * c for v, c in hz_vals) / hz_total, 2),
                      "min": None if not hz_vals else round(hz_vals[0][0] - HZ_BINS[1] / 2, 2),
                      "max": None if not hz_vals else round(hz_vals[-1][0] + HZ_BINS[1] / 2, 2),
                      "bins": [{"from": round(b, 2), "count": c} for b, c in hz_bins if c], "bin_width": HZ_BINS[1]},
        "out": {"v_pct": 0.0 if not total else round(100 * whole["v_out"] / total, 2), "hz_pct": 0.0 if not total else round(100 * whole["hz_out"] / total, 2),
                "v_min": round(whole["v_out"] * STEP_S / 60, 1), "hz_min": round(whole["hz_out"] * STEP_S / 60, 1),
                "sags": whole["sags"], "surges": whole["surges"]},
        "outages": {"count": len(outages), "total_s": round(sum(durations)), "longest_s": round(max(durations)) if durations else 0},
        "hours": hours, "days": days,
    }


def build(ctx: Context) -> APIRouter:
    r = APIRouter()
    s, repo, now, call = ctx.s, ctx.repo, ctx.now, ctx.call
    cache: dict = {}

    @r.get("/api/grid/quality")
    async def api_grid_quality(range: str = Query("7d"), v_min: float | None = Query(None), v_max: float | None = Query(None),
                               hz_min: float | None = Query(None), hz_max: float | None = Query(None)):
        if range not in RANGES:
            raise HTTPException(status_code=400, detail="range must be 24h, 7d or 30d")
        rng, range_s = parse_range(range)
        limits = parse_limits(v_min, v_max, hz_min, hz_max)
        t = now()
        key = (rng, tuple(sorted(limits.items())))
        hit = cache.get(key)
        if hit and (t - hit["at"]).total_seconds() < CACHE_S:
            return hit["body"]
        rows = {}
        rows["v_hist"] = await call(repo.histogram, "grid_voltage", range_s, *V_BINS, V_VALID)
        rows["hz_hist"] = await call(repo.histogram, "grid_frequency", range_s, *HZ_BINS, HZ_VALID)
        for fn in ("min", "max", "mean", "count"):
            rows[fn] = await call(repo.hourly, "grid_voltage", range_s, fn, V_VALID)
        rows["v_out"] = await call(repo.hourly_outside, "grid_voltage", range_s, limits["v_min"], limits["v_max"], V_VALID)
        rows["hz_out"] = await call(repo.hourly_outside, "grid_frequency", range_s, limits["hz_min"], limits["hz_max"], HZ_VALID)
        rows["sags"] = await call(repo.hourly_excursions, "grid_voltage", range_s, limits["v_min"], True, V_VALID)
        rows["surges"] = await call(repo.hourly_excursions, "grid_voltage", range_s, limits["v_max"], False, V_VALID)
        events = await call(repo.events, max(1, range_s // 86400) + 31)
        body = shape(rows, limits, t - timedelta(seconds=range_s), t, s.tz, events, rng)
        cache[key] = {"at": t, "body": body}
        return body

    return r
