"""InfluxDB query layer. The only module that talks to InfluxDB; tests replace it."""
from datetime import datetime
from typing import Protocol

from influxdb_client import InfluxDBClient

from .config import Settings
from .validation import FIELD_SET, WINDOWS


class Repo(Protocol):
    def latest(self) -> tuple[datetime, dict[str, float]] | None: ...
    def history(self, fields: list[str], range_s: int, every: str, agg: str
                ) -> tuple[list[datetime], dict[str, list[float | None]]]: ...
    def history_between(self, fields: list[str], start: datetime, stop: datetime, every: str, agg: str
                        ) -> tuple[list[datetime], dict[str, list[float | None]]]: ...
    def window_max(self, fields: list[str], start: datetime, every: str, stop: datetime | None = None
                   ) -> list[tuple[datetime, str, float]]: ...
    def first_time(self) -> datetime | None: ...
    def control_log(self, limit: int) -> list[dict]: ...
    def events(self, days: int) -> list[dict]: ...
    # Grid quality (routes/grid.py): computed in InfluxDB so a month of 10-second readings never crosses the wire
    def histogram(self, field: str, range_s: int, start: float, width: float, count: int, min_valid: float
                  ) -> list[tuple[float, int]]: ...
    def hourly(self, field: str, range_s: int, fn: str, min_valid: float) -> list[tuple[datetime, float]]: ...
    def hourly_outside(self, field: str, range_s: int, lo: float, hi: float, min_valid: float) -> list[tuple[datetime, int]]: ...
    def hourly_excursions(self, field: str, range_s: int, limit: float, below: bool, min_valid: float) -> list[tuple[datetime, int]]: ...


def _field_filter(fields) -> str:
    assert all(f in FIELD_SET for f in fields)
    return " or ".join(f'r._field == "{f}"' for f in fields)


def latest_flux(bucket: str) -> str:
    return (f'from(bucket: "{bucket}") |> range(start: -7d) '
            f'|> filter(fn: (r) => r._measurement == "solis") '
            f'|> group(columns: ["_field"]) |> last()')


def history_flux(bucket: str, fields: list[str], range_s: int, every: str, agg: str) -> str:
    assert isinstance(range_s, int)
    return _history_query(bucket, f"start: -{range_s}s", fields, every, agg)


def history_between_flux(bucket: str, fields: list[str], start: datetime, stop: datetime, every: str, agg: str) -> str:
    return _history_query(bucket, f'start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {stop.strftime("%Y-%m-%dT%H:%M:%SZ")}',
                          fields, every, agg)


def _history_query(bucket: str, time_range: str, fields: list[str], every: str, agg: str) -> str:
    assert every in WINDOWS and agg in {"mean", "max", "min", "last"}
    return (f'from(bucket: "{bucket}") |> range({time_range}) '
            f'|> filter(fn: (r) => r._measurement == "solis" and ({_field_filter(fields)})) '
            f'|> group(columns: ["_field"]) '
            f'|> aggregateWindow(every: {every}, fn: {agg}, createEmpty: false) '
            f'|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")')


def window_max_flux(bucket: str, fields: list[str], start: datetime, every: str, stop: datetime | None = None) -> str:
    assert every in WINDOWS
    end = f', stop: {stop.strftime("%Y-%m-%dT%H:%M:%SZ")}' if stop else ''
    return (f'from(bucket: "{bucket}") |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}{end}) '
            f'|> filter(fn: (r) => r._measurement == "solis" and ({_field_filter(fields)})) '
            f'|> group(columns: ["_field"]) '
            f'|> aggregateWindow(every: {every}, fn: max, createEmpty: false, timeSrc: "_start")')


def first_time_flux(bucket: str) -> str:
    """The oldest point of one always-present field, one row per series (the caller takes the minimum)."""
    return (f'from(bucket: "{bucket}") |> range(start: 0) '
            f'|> filter(fn: (r) => r._measurement == "solis" and r._field == "grid_import_today") '
            f'|> first() |> keep(columns: ["_time"])')


def control_log_flux(bucket: str, limit: int) -> str:
    assert isinstance(limit, int)
    return (f'from(bucket: "{bucket}") |> range(start: -365d) '
            f'|> filter(fn: (r) => r._measurement == "solis_control") '
            f'|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
            f'|> group() |> sort(columns: ["_time"], desc: true) |> limit(n: {limit})')


def _valid(field: str, min_valid: float) -> str:
    """Readings during an outage (0 V, 0 Hz) are not grid quality: only values at or above min_valid count."""
    assert field in FIELD_SET and isinstance(min_valid, (int, float))
    return (f'|> filter(fn: (r) => r._measurement == "solis" and r._field == "{field}" and r._value >= {float(min_valid)}) ')


def histogram_flux(bucket: str, field: str, range_s: int, start: float, width: float, count: int, min_valid: float) -> str:
    """Counts per bin [start + i*width, start + (i+1)*width): Flux's histogram() is cumulative, difference() undoes that."""
    assert isinstance(range_s, int) and isinstance(count, int) and 1 <= count <= 400
    return (f'from(bucket: "{bucket}") |> range(start: -{range_s}s) {_valid(field, min_valid)}'
            f'|> group() |> histogram(bins: linearBins(start: {float(start)}, width: {float(width)}, count: {count}, infinity: false)) '
            f'|> difference(nonNegative: true, columns: ["_value"]) |> keep(columns: ["le", "_value"])')


def hourly_flux(bucket: str, field: str, range_s: int, fn: str, min_valid: float) -> str:
    assert isinstance(range_s, int) and fn in {"min", "max", "mean", "count"}
    return (f'from(bucket: "{bucket}") |> range(start: -{range_s}s) {_valid(field, min_valid)}'
            f'|> group() |> aggregateWindow(every: 1h, fn: {fn}, createEmpty: false, timeSrc: "_start")')


def hourly_outside_flux(bucket: str, field: str, range_s: int, lo: float, hi: float, min_valid: float) -> str:
    """How many readings per hour were below lo or above hi."""
    assert isinstance(range_s, int)
    return (f'from(bucket: "{bucket}") |> range(start: -{range_s}s) {_valid(field, min_valid)}'
            f'|> filter(fn: (r) => r._value < {float(lo)} or r._value > {float(hi)}) '
            f'|> group() |> aggregateWindow(every: 1h, fn: count, createEmpty: false, timeSrc: "_start")')


def hourly_excursions_flux(bucket: str, field: str, range_s: int, limit: float, below: bool, min_valid: float) -> str:
    """How many excursions (contiguous runs of readings past the limit) began in each hour: a 0/1 flag per reading,
    its difference, and the +1 steps counted."""
    assert isinstance(range_s, int)
    cond = f"r._value < {float(limit)}" if below else f"r._value > {float(limit)}"
    return (f'from(bucket: "{bucket}") |> range(start: -{range_s}s) {_valid(field, min_valid)}'
            f'|> group() |> map(fn: (r) => ({{r with _value: if {cond} then 1 else 0}})) '
            f'|> difference(columns: ["_value"]) |> filter(fn: (r) => r._value == 1) '
            f'|> aggregateWindow(every: 1h, fn: count, createEmpty: false, timeSrc: "_start")')


def events_flux(bucket: str, days: int) -> str:
    assert isinstance(days, int)
    return (f'from(bucket: "{bucket}") |> range(start: -{days}d) '
            f'|> filter(fn: (r) => r._measurement == "solis_event") '
            f'|> pivot(rowKey: ["_time", "kind", "key"], columnKey: ["_field"], valueColumn: "_value") '
            f'|> group() |> sort(columns: ["_time"])')


class InfluxRepo:
    def __init__(self, s: Settings):
        self.bucket = s.influx_bucket
        self.client = InfluxDBClient(url=s.influx_url, token=s.influx_token, org=s.influx_org,
                                     timeout=8000)
        self.api = self.client.query_api()

    def latest(self):
        ts, vals = None, {}
        for table in self.api.query(latest_flux(self.bucket)):
            for r in table.records:
                vals[r.get_field()] = r.get_value()
                t = r.get_time()
                ts = t if ts is None or t > ts else ts
        return (ts, vals) if ts else None

    def history(self, fields, range_s, every, agg):
        return self._history_rows(history_flux(self.bucket, fields, range_s, every, agg), fields)

    def history_between(self, fields, start, stop, every, agg):
        return self._history_rows(history_between_flux(self.bucket, fields, start, stop, every, agg), fields)

    def _history_rows(self, query, fields):
        rows = sorted(((r.get_time(), r.values) for table in self.api.query(query) for r in table.records),
                      key=lambda row: row[0])
        times = [t for t, _ in rows]
        series = {f: [None if v.get(f) is None else float(v[f]) for _, v in rows] for f in fields}
        return times, series

    def window_max(self, fields, start, every, stop=None):
        return [(r.get_time(), r.get_field(), float(r.get_value()))
                for table in self.api.query(window_max_flux(self.bucket, fields, start, every, stop))
                for r in table.records if r.get_value() is not None]

    def first_time(self):
        times = [r.get_time() for table in self.api.query(first_time_flux(self.bucket)) for r in table.records]
        return min(times) if times else None

    def control_log(self, limit):
        keep = ("summary", "register", "before", "written", "read_back", "verified")
        return [{"time": r.get_time().isoformat(), "action": r.values.get("action"),
                 **{k: r.values.get(k) for k in keep}}
                for table in self.api.query(control_log_flux(self.bucket, limit)) for r in table.records]

    def events(self, days):
        keep = ("state", "name", "severity", "soc", "load")
        return [{"time": r.get_time(), "kind": r.values.get("kind"), "key": r.values.get("key"),
                 **{k: r.values.get(k) for k in keep}}
                for table in self.api.query(events_flux(self.bucket, days)) for r in table.records]

    def histogram(self, field, range_s, start, width, count, min_valid):
        rows = [(r.values.get("le"), r.get_value()) for table in self.api.query(histogram_flux(self.bucket, field, range_s, start, width, count, min_valid))
                for r in table.records]
        # "le" is the bin's upper edge; the first bin (below start) is dropped by difference(), so edges map back to starts
        return [(float(le) - width, int(v)) for le, v in rows if le is not None and v is not None]

    def _hourly_rows(self, query):
        return [(r.get_time(), r.get_value()) for table in self.api.query(query) for r in table.records if r.get_value() is not None]

    def hourly(self, field, range_s, fn, min_valid):
        return [(t, float(v)) for t, v in self._hourly_rows(hourly_flux(self.bucket, field, range_s, fn, min_valid))]

    def hourly_outside(self, field, range_s, lo, hi, min_valid):
        return [(t, int(v)) for t, v in self._hourly_rows(hourly_outside_flux(self.bucket, field, range_s, lo, hi, min_valid))]

    def hourly_excursions(self, field, range_s, limit, below, min_valid):
        return [(t, int(v)) for t, v in self._hourly_rows(hourly_excursions_flux(self.bucket, field, range_s, limit, below, min_valid))]

    def close(self):
        self.client.close()
