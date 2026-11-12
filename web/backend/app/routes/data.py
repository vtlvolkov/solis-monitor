"""Read-only data for the dashboard: live values, history, energy, events, cost, and the poller's cached
device and schedule details. Nothing here changes anything."""
import asyncio
import json
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import shaping
from ..context import Context, InfluxUnavailable
from ..tariff import version_dict
from ..validation import ENERGY_FIELDS, WINDOWS, parse_agg, parse_days, parse_every, parse_fields, parse_range


def build(ctx: Context) -> tuple[APIRouter, dict]:
    """The router, and the route functions the MCP server reuses (live, history, events, energy_summary, cost)."""
    r = APIRouter()
    s, repo, now, call, poller, control, tariffs = ctx.s, ctx.repo, ctx.now, ctx.call, ctx.poller, ctx.control, ctx.tariffs

    @r.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @r.get("/api/site")
    async def api_site():
        return {"name": s.site_name}

    async def live():
        return shaping.live_payload(await call(repo.latest), now())

    @r.get("/api/live")
    async def api_live():
        return await live()

    @r.get("/api/stream")
    async def api_stream(request: Request):
        async def events():
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                try:
                    yield f"data: {json.dumps(await live())}\n\n"
                except InfluxUnavailable as exc:
                    yield f"event: influx-error\ndata: {json.dumps({'detail': 'InfluxDB unavailable', 'error': str(exc)})}\n\n"
                await asyncio.sleep(s.stream_interval_s)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.get("/api/history")
    async def api_history(fields: str = Query(...), range: str = Query("6h"),
                          every: str = Query("auto"), agg: str = Query("mean")):
        names = parse_fields(fields)
        rng, range_s = parse_range(range)
        win = parse_every(every, range_s)
        fn = parse_agg(agg)
        times, series = await call(repo.history, names, range_s, win, fn)
        times, series = shaping.insert_gaps(times, series, WINDOWS[win])
        return {"range": rng, "every": win, "agg": fn,
                "t": [round(t.timestamp()) for t in times],
                "series": {k: [shaping._num(v) for v in vs] for k, vs in series.items()}}

    @r.get("/api/energy/daily")
    async def api_energy_daily(days: int = Query(30)):
        n = parse_days(days)
        t = now()
        rows = await call(repo.window_max, list(ENERGY_FIELDS), shaping.daily_start(t, n, s.tz), "15m")
        return {"tz": s.tz, "unit": "kWh", "days": shaping.aggregate_daily(rows, ENERGY_FIELDS, n, t, s.tz)}

    # ------------------------------------------------------------------ one exact day (Charts and Events tabs)
    def local_day(raw: str, t):
        """The requested local day and its UTC bounds; a day that is malformed or still to come is a 400."""
        d = shaping.parse_day(raw)
        if d is None or d > t.astimezone(ZoneInfo(s.tz)).date():
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD, not in the future")
        return (d, *shaping.local_range(d, d, s.tz))

    # Separate from /api/history and /api/events: the MCP server calls those route functions directly.
    @r.get("/api/day/history")
    async def api_day_history(date: str = Query(...), fields: str = Query(...),
                              every: str = Query("auto"), agg: str = Query("mean")):
        t = now()
        d, start, stop = local_day(date, t)
        names = parse_fields(fields)
        win = parse_every(every, round((stop - start).total_seconds()))
        fn = parse_agg(agg)
        times, series = await call(repo.history_between, names, start, min(stop, t), win, fn)
        times, series = shaping.insert_gaps(times, series, WINDOWS[win])
        return {"date": d.isoformat(), "start": round(start.timestamp()), "stop": round(stop.timestamp()),
                "is_today": t < stop, "every": win, "agg": fn,
                "t": [round(x.timestamp()) for x in times],
                "series": {k: [shaping._num(v) for v in vs] for k, vs in series.items()}}

    @r.get("/api/day/energy")
    async def api_day_energy(date: str = Query(...)):
        d, start, stop = local_day(date, now())
        rows = await call(repo.window_max, list(ENERGY_FIELDS), start, "15m", stop)
        return {"tz": s.tz, "unit": "kWh", **shaping.aggregate_dates(rows, ENERGY_FIELDS, [d], s.tz)[0]}

    @r.get("/api/day/events")
    async def api_day_events(date: str = Query(...)):
        t = now()
        d, start, stop = local_day(date, t)
        # Read on to the present: an outage's end may come after this day, and its start up to 30 days before.
        rows = await call(repo.events, (t - start).days + 31)
        return {"date": d.isoformat(), "start": round(start.timestamp()), "stop": round(stop.timestamp()),
                **shaping.events_on_day(shaping.pair_events(rows, t), start, stop, t)}

    first_cache: dict = {}

    async def first_day(t):
        """Local date of the oldest stored reading (cached: it only changes if old data is deleted)."""
        if "at" not in first_cache or (t - first_cache["at"]).total_seconds() > 6 * 3600:
            ft = await call(repo.first_time)
            first_cache.update(at=t, day=ft.astimezone(ZoneInfo(s.tz)).date() if ft else None)
        return first_cache["day"]

    @r.get("/api/events/calendar")
    async def api_events_calendar(month: str | None = Query(None)):
        """Which days of a month had outages or alarms, and the days the picker can offer."""
        t = now()
        today = t.astimezone(ZoneInfo(s.tz)).date()
        ym = shaping.parse_month(month) if month else (today.year, today.month)
        if ym is None or ym > (today.year, today.month):
            raise HTTPException(status_code=400, detail="month must be YYYY-MM, not in the future")
        dates = shaping.month_dates(ym[0], ym[1], today)
        start, _ = shaping.local_range(dates[0], dates[-1], s.tz)
        rows = await call(repo.events, (t - start).days + 31)
        first = await first_day(t)
        return {"month": f"{ym[0]}-{ym[1]:02d}", "today": today.isoformat(), "first_day": first.isoformat() if first else None,
                "days": shaping.event_calendar(shaping.pair_events(rows, t), dates, s.tz, t)}

    @r.get("/api/events")
    async def api_events(days: int = Query(30)):
        if not 1 <= days <= 366:
            raise HTTPException(status_code=400, detail="days must be 1..366")
        return {"days": days, **shaping.pair_events(await call(repo.events, days), now())}

    summary_cache: dict = {}

    @r.get("/api/energy/summary")
    async def api_energy_summary():
        """Month/year to date from our own daily maxima (the inverter only keeps month/year for PV)."""
        t = now()
        if summary_cache.get("at") and (t - summary_cache["at"]).total_seconds() < 300:
            return summary_cache["body"]
        zone = ZoneInfo(s.tz)
        today = t.astimezone(zone).date()
        n = (today - today.replace(month=1, day=1)).days + 1
        rows = await call(repo.window_max, list(ENERGY_FIELDS), shaping.daily_start(t, n, s.tz), "1h")
        days = shaping.aggregate_daily(rows, ENERGY_FIELDS, n, t, s.tz)
        body = {"tz": s.tz, "unit": "kWh", **shaping.period_sums(days, ENERGY_FIELDS, today)}
        summary_cache.update(at=t, body=body)
        return body

    # ------------------------------------------------------------------ from the poller's caches
    @r.get("/api/schedule")
    async def api_schedule():
        """Cached inverter settings (Time-of-Use slots, modes, SOC limits) for the Overview."""
        status, body = await poller(control.cached_settings)
        return JSONResponse(body, status_code=status)

    @r.get("/api/devices")
    async def api_devices():
        status, body = await poller(control.devices)
        return JSONResponse(body, status_code=status)

    # ------------------------------------------------------------------ electricity cost
    @r.get("/api/cost")
    async def api_cost():
        t = now()
        if tariffs.at(t) is None:
            return {"configured": False, "tariff": ctx.tariff_view(None)}
        # the active zone follows the clock, so it is computed on every request, outside the 5-minute cache
        def with_rate(body):
            return {**body, "tariff": ctx.tariff_view(tariffs.at(t)), "now": shaping.rate_now(t, s.tz, tariffs.at(t))}
        cache = ctx.cost_cache
        if cache.get("at") and (t - cache["at"]).total_seconds() < 300:
            return with_rate(cache["body"])
        today = t.astimezone(ZoneInfo(s.tz)).date()
        rows = await call(repo.window_max, ["grid_import_today"], shaping.daily_start(t, today.day, s.tz), "15m")
        zones = shaping.import_costs([(r[0], r[2]) for r in rows], s.tz, tariffs.at)
        body = {"configured": True, **shaping.cost_summary(zones, today)}
        cache.update(at=t, body=body)
        return with_rate(body)

    @r.get("/api/cost/history")
    async def api_cost_history(month: str | None = Query(None)):
        """Day-by-day import, cost (day/night zones) and consumption for one month.
        Every day is priced with the tariff that was in force at the time, so later price changes don't alter it."""
        t = now()
        if tariffs.at(t) is None:
            return {"configured": False, "tariff": ctx.tariff_view(None)}
        today = t.astimezone(ZoneInfo(s.tz)).date()
        ym = shaping.parse_month(month) if month else (today.year, today.month)
        if ym is None or ym > (today.year, today.month):
            raise HTTPException(status_code=400, detail="month must be YYYY-MM, not in the future")
        first = await first_day(t)
        key = f"{ym[0]}-{ym[1]:02d}"
        is_now = ym == (today.year, today.month)
        hit = ctx.history_cache.get(key)
        if hit and (t - hit["at"]).total_seconds() < (300 if is_now else 3600):
            return hit["body"]
        dates = shaping.month_dates(ym[0], ym[1], today)
        start, stop = shaping.local_range(dates[0], dates[-1], s.tz)
        rows = await call(repo.window_max, list(ENERGY_FIELDS), start, "15m", stop)
        zones = shaping.import_costs([(r[0], r[2]) for r in rows if r[1] == "grid_import_today"], s.tz, tariffs.at)
        energy = shaping.aggregate_dates(rows, list(ENERGY_FIELDS), dates, s.tz)
        body = {"configured": True, "tariff": ctx.tariff_view(tariffs.at(t)), "month": key,
                "current_month": f"{today.year}-{today.month:02d}", "tz": s.tz,
                "first_day": first.isoformat() if first else None,
                "tariff_changes": [version_dict(st, tf) for st, tf in tariffs.between(start, stop)],
                **shaping.cost_days(energy, zones)}
        ctx.history_cache[key] = {"at": t, "body": body}
        return body

    return r, {"live": live, "history": api_history, "events": api_events,
               "energy_summary": api_energy_summary, "cost": api_cost, "cost_history": api_cost_history}
