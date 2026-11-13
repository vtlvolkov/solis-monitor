"""CSV downloads: readings over a period, the cost history of a month, and the events (outages, alarms).

Read-only and open like the other data routes. Every parameter goes through the same whitelists as the JSON API;
the file is streamed row by row. `sep=;` is the Excel variant (semicolon between columns, comma as the decimal
mark) that a Ukrainian or European Excel opens straight away."""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import shaping
from ..context import Context
from ..validation import ENERGY_FIELDS, FIELD_SET, parse_days, parse_range

# The resolutions the dialog offers -> the InfluxDB window; "1d" is one row per local day (below).
RESOLUTIONS = {"10s": 10, "1m": 60, "15m": 900, "1h": 3600, "1d": 86400}
MAX_ROWS = 100_000
RAW_MAX_S, MINUTE_MAX_S = 86400, 7 * 86400          # 10 s only up to a day, 1 min up to a week
EVENT_COLUMNS = ("kind", "name", "severity", "start", "end", "ongoing", "duration_s", "soc_start", "soc_end", "load_start")
COST_COLUMNS = ("date", "kwh", "day_kwh", "night_kwh", "cost", "day_cost", "night_cost", "saved",
                "load_kwh", "export_kwh", "pv_kwh", "charge_kwh", "discharge_kwh")


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def parse_export_fields(raw: str) -> list[str]:
    """Like validation.parse_fields, without the JSON API's 12-field cap: a CSV may well want every column."""
    names = [f.strip() for f in (raw or "").split(",") if f.strip()]
    if not names:
        raise _bad("fields is required")
    unknown = [f for f in names if f not in FIELD_SET]
    if unknown:
        raise _bad(f"unknown field(s): {', '.join(sorted(set(unknown)))}")
    return list(dict.fromkeys(names))


def parse_sep(raw: str) -> str:
    if raw not in (",", ";"):
        raise _bad("sep must be , or ;")
    return raw


def parse_resolution(raw: str, seconds: int) -> str:
    if raw not in RESOLUTIONS:
        raise _bad(f"every must be one of {', '.join(RESOLUTIONS)}")
    if raw == "10s" and seconds > RAW_MAX_S:
        raise _bad("every=10s is offered up to one day")
    if raw == "1m" and seconds > MINUTE_MAX_S:
        raise _bad("every=1m is offered up to one week")
    if seconds / RESOLUTIONS[raw] > MAX_ROWS:
        raise _bad(f"this period at every={raw} is more than {MAX_ROWS} rows; use a larger step")
    return raw


class Rows:
    """Formats CSV rows for one separator; a semicolon file uses the decimal comma."""

    def __init__(self, sep: str, tz: str):
        self.sep, self.zone = sep, ZoneInfo(tz)
        self.buf = io.StringIO()
        self.writer = csv.writer(self.buf, delimiter=sep, lineterminator="\r\n")

    def num(self, v):
        v = shaping._num(v)
        if v is None:
            return ""
        s = f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
        return s.replace(".", ",") if self.sep == ";" else s

    def local(self, t: datetime) -> str:
        return t.astimezone(self.zone).isoformat(timespec="seconds")

    def line(self, cells) -> str:
        self.writer.writerow(list(cells))
        out = self.buf.getvalue()
        self.buf.seek(0)
        self.buf.truncate()
        return out


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


def _site(ctx: Context) -> str:
    s = "".join(ch for ch in (ctx.s.site_name or "") if ch.isalnum() or ch in "-_")
    return f"-{s}" if s else ""


def build(ctx: Context, api: dict) -> APIRouter:
    r = APIRouter()
    s, repo, now, call = ctx.s, ctx.repo, ctx.now, ctx.call

    def period(range_: str | None, from_: str | None, to: str | None, t: datetime):
        """(start, stop, label) in UTC: a live range, or local days from..to inclusive (never in the future)."""
        if from_ or to:
            a, b = shaping.parse_day(from_ or ""), shaping.parse_day(to or from_ or "")
            today = t.astimezone(ZoneInfo(s.tz)).date()
            if a is None or b is None or b < a or b > today:
                raise _bad("from and to must be YYYY-MM-DD, from <= to, not in the future")
            start, stop = shaping.local_range(a, b, s.tz)
            return start, min(stop, t), f"{a.isoformat()}_{b.isoformat()}" if a != b else a.isoformat()
        rng, secs = parse_range(range_ or "6h")
        return t - timedelta(seconds=secs), t, rng

    async def daily_rows(fields, start, stop):
        """One row per local day: the energy counters' daily maxima, everything else the mean of the hourly means."""
        zone = ZoneInfo(s.tz)
        first, last = start.astimezone(zone).date(), (stop - timedelta(seconds=1)).astimezone(zone).date()
        dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        energy = [f for f in fields if f in ENERGY_FIELDS]
        others = [f for f in fields if f not in ENERGY_FIELDS]
        per_day: dict = {d.isoformat(): {} for d in dates}
        if energy:
            rows = await call(repo.window_max, energy, start, "1h", stop)
            for d in shaping.aggregate_dates(rows, energy, dates, s.tz):
                per_day[d["date"]].update({f: d[f] for f in energy})
        if others:
            times, series = await call(repo.history_between, others, start, stop, "1h", "mean")
            acc: dict = defaultdict(lambda: defaultdict(list))
            for i, tt in enumerate(times):
                day = tt.astimezone(zone).date().isoformat()
                for f in others:
                    v = series[f][i]
                    if v is not None and day in per_day:
                        acc[day][f].append(v)
            for day, vals in acc.items():
                per_day[day].update({f: sum(vs) / len(vs) for f, vs in vals.items()})
        return [(d, per_day[d.isoformat()]) for d in dates]

    @r.get("/api/export.csv")
    async def api_export(fields: str = Query(...), range: str | None = Query(None), every: str = Query("1m"),
                         sep: str = Query(","), from_: str | None = Query(None, alias="from"), to: str | None = Query(None)):
        """Readings as rows: a live range (range=6h) or local days (from=, to=), at one of the offered steps."""
        return await export_readings(fields, range, from_, to, every, sep)

    async def export_readings(fields, range_, from_, to, every, sep):
        names = parse_export_fields(fields)
        sp = parse_sep(sep)
        t = now()
        start, stop, label = period(range_, from_, to, t)
        res = parse_resolution(every, round((stop - start).total_seconds()))
        if res == "10s" and s.raw_retention_days and start < t - timedelta(days=s.raw_retention_days - 1):
            raise _bad(f"every=10s needs the 10-second readings, which are kept for the last {s.raw_retention_days} days; use 1 min or more")
        rows = Rows(sp, s.tz)
        name = f"solis{_site(ctx)}-{label}-{res}.csv"
        if res == "1d":
            days = await daily_rows(names, start, stop)

            def gen_days():
                yield rows.line(["date", *names])
                for d, vals in days:
                    yield rows.line([d.isoformat(), *(rows.num(vals.get(f)) for f in names)])
            return StreamingResponse(gen_days(), media_type="text/csv; charset=utf-8", headers=_attachment(name))
        times, series = await call(repo.history_between, names, start, stop, res, "mean")

        def gen():
            yield rows.line(["time", "unix", *names])
            for i, tt in enumerate(times):
                yield rows.line([rows.local(tt), round(tt.timestamp()), *(rows.num(series[f][i]) for f in names)])
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=_attachment(name))

    @r.get("/api/export/events.csv")
    async def api_export_events(days: int = Query(30), date: str | None = Query(None), sep: str = Query(",")):
        """Outages and alarms as rows: one exact day, or the last N days."""
        sp = parse_sep(sep)
        t = now()
        if date:
            d = shaping.parse_day(date)
            if d is None or d > t.astimezone(ZoneInfo(s.tz)).date():
                raise _bad("date must be YYYY-MM-DD, not in the future")
            start, stop = shaping.local_range(d, d, s.tz)
            paired = shaping.events_on_day(shaping.pair_events(await call(repo.events, (t - start).days + 31), t), start, stop, t)
            label = d.isoformat()
        else:
            n = parse_days(days)
            paired = shaping.pair_events(await call(repo.events, n), t)
            label = f"last-{n}-days"
        events = sorted(paired["outages"] + paired["alarms"], key=lambda e: e["start"] or e["end"] or "", reverse=True)
        rows = Rows(sp, s.tz)

        def gen():
            yield rows.line(EVENT_COLUMNS)
            for e in events:
                cells = []
                for c in EVENT_COLUMNS:
                    v = e.get(c)
                    if c in ("start", "end") and v:
                        v = rows.local(datetime.fromisoformat(v))
                    elif c in ("duration_s", "soc_start", "soc_end", "load_start"):
                        v = rows.num(v)
                    elif c == "ongoing":
                        v = "yes" if v else "no"
                    cells.append("" if v is None else v)
                yield rows.line(cells)
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=_attachment(f"solis{_site(ctx)}-events-{label}.csv"))

    @r.get("/api/export/cost.csv")
    async def api_export_cost(month: str | None = Query(None), sep: str = Query(",")):
        """The cost history page's table: one row per day of the month."""
        sp = parse_sep(sep)
        body = await api["cost_history"](month)
        if not body.get("configured"):
            raise _bad("no tariff configured")
        rows = Rows(sp, s.tz)

        def gen():
            yield rows.line([*COST_COLUMNS, "currency"])
            for d in body["days"]:
                if not d.get("has_data"):
                    continue
                yield rows.line([d["date"], *(rows.num(d.get(c)) for c in COST_COLUMNS[1:]), body["tariff"]["currency"]])
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=_attachment(f"solis{_site(ctx)}-cost-{body['month']}.csv"))

    return r
