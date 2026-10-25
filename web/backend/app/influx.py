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
    def window_max(self, fields: list[str], start: datetime, every: str, stop: datetime | None = None
                   ) -> list[tuple[datetime, str, float]]: ...
    def first_time(self) -> datetime | None: ...
    def control_log(self, limit: int) -> list[dict]: ...
    def events(self, days: int) -> list[dict]: ...


def _field_filter(fields) -> str:
    assert all(f in FIELD_SET for f in fields)
    return " or ".join(f'r._field == "{f}"' for f in fields)


def latest_flux(bucket: str) -> str:
    return (f'from(bucket: "{bucket}") |> range(start: -7d) '
            f'|> filter(fn: (r) => r._measurement == "solis") '
            f'|> group(columns: ["_field"]) |> last()')


def history_flux(bucket: str, fields: list[str], range_s: int, every: str, agg: str) -> str:
    assert every in WINDOWS and agg in {"mean", "max", "min", "last"} and isinstance(range_s, int)
    return (f'from(bucket: "{bucket}") |> range(start: -{range_s}s) '
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
        rows = sorted(((r.get_time(), r.values)
                       for table in self.api.query(history_flux(self.bucket, fields, range_s, every, agg))
                       for r in table.records), key=lambda row: row[0])
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

    def close(self):
        self.client.close()
