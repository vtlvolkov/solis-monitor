"""
Downsampling: 1-minute aggregates of every field, kept in a second bucket (INFLUX_BUCKET_1M, "solis_1m").

The poller writes a point every 10 s forever; a month chart or a year of cost history read from that grows slower
with every week of history. An InfluxDB *task* (run by influxd itself, no extra process) aggregates each minute into
three measurements of the second bucket: `solis` (mean), `solis_min` and `solis_max`. The web service reads that
bucket for anything a day or longer (web/backend/app/influx.py) and the raw bucket for live ranges and for grid
quality, where 10-second resolution matters.

This module only makes sure the bucket and the task exist (idempotent, at every poller start: the poller holds the
admin token, and this works the same on an installer-based site and on Umbrel) and fills the bucket for the history
from before the task existed: day by day, newest first, so the recent ranges are complete within seconds and a year
takes minutes in the background. The same backfill over the last 24 hours runs again every few hours, which repairs
any gap left by a restart of the database.

Retention split (RAW_RETENTION_DAYS, default 90, 0 = keep everything): once a day, the 10-second readings older than
that are deleted from the raw bucket, a day at a time, each day re-aggregated just before so its 1-minute aggregates
are certainly there. Only measurement `solis` is deleted: the events (`solis_event`) and the settings audit
(`solis_control`) live in the same bucket and are kept forever. The aggregate bucket has no retention.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("solis-poller")

TASK_NAME = "solis-1m"
EVERY, OFFSET, LOOKBACK = "1m", "30s", "3m"       # the task runs 30 s after each minute and re-does the last 3 (to() overwrites)
REPAIR_EVERY_S = 6 * 3600
REPAIR_SPAN = timedelta(hours=24)
CHUNK = timedelta(days=1)
MIN_RAW_DAYS = 35          # grid quality reads 30 days of raw readings: never keep fewer than this


def parse_retention(raw: str | None) -> int:
    """RAW_RETENTION_DAYS: days of 10-second readings to keep (0 = all). Below the minimum it is raised to it."""
    try:
        days = int(str(raw).strip()) if raw not in (None, "") else 90
    except ValueError:
        log.warning("RAW_RETENTION_DAYS=%r is not a number: keeping 90 days", raw)
        return 90
    if days <= 0:
        return 0
    if days < MIN_RAW_DAYS:
        log.warning("RAW_RETENTION_DAYS=%d is below %d (grid quality needs a month of 10-second readings): using %d", days, MIN_RAW_DAYS, MIN_RAW_DAYS)
        return MIN_RAW_DAYS
    return days


def aggregation_flux(source: str, target: str, org: str, time_range: str) -> str:
    """The aggregation over `time_range` (a Flux range(...) argument list), written to the target bucket."""
    return (f'data = from(bucket: "{source}") |> range({time_range}) |> filter(fn: (r) => r._measurement == "solis")\n'
            f'data |> aggregateWindow(every: 1m, fn: mean, createEmpty: false) |> to(bucket: "{target}", org: "{org}") |> yield(name: "mean")\n'
            f'data |> aggregateWindow(every: 1m, fn: min, createEmpty: false) |> set(key: "_measurement", value: "solis_min") |> to(bucket: "{target}", org: "{org}") |> yield(name: "min")\n'
            f'data |> aggregateWindow(every: 1m, fn: max, createEmpty: false) |> set(key: "_measurement", value: "solis_max") |> to(bucket: "{target}", org: "{org}") |> yield(name: "max")\n')


def task_flux(source: str, target: str, org: str) -> str:
    return f'option task = {{name: "{TASK_NAME}", every: {EVERY}, offset: {OFFSET}}}\n' + aggregation_flux(source, target, org, f"start: -{LOOKBACK}")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _ceil_minute(t: datetime) -> datetime:
    f = _floor_minute(t)
    return f if f == t else f + timedelta(minutes=1)


class Downsampler:
    """`client` is an influxdb_client.InfluxDBClient (or a fake with the same buckets/tasks/query API in the tests)."""

    def __init__(self, client, org: str, source: str, target: str, clock=time.time, sleep=time.sleep, raw_days: int = 0):
        self.client, self.org, self.source, self.target = client, org, source, target
        self.clock, self.sleep = clock, sleep
        self.raw_days = raw_days                           # 10-second readings kept this many days (0 = all)
        self.backfilled_to: datetime | None = None         # the oldest day done, for the log and the tests

    # ---------------------------------------------------------------- the bucket and the task
    def ensure(self) -> bool:
        """Create the bucket and the task if they are missing, update the task if its script changed. True = ready."""
        try:
            orgs = self.client.organizations_api().find_organizations(org=self.org)
            if not orgs:
                log.error("downsampling: organisation %s not found", self.org)
                return False
            org = orgs[0]
            buckets = self.client.buckets_api()
            if not buckets.find_bucket_by_name(self.target):
                buckets.create_bucket(bucket_name=self.target, org=org, description="1-minute aggregates of the solis bucket (mean, min, max)")
                log.info("downsampling: created bucket %s", self.target)
            tasks = self.client.tasks_api()
            flux = task_flux(self.source, self.target, self.org)
            found = tasks.find_tasks(name=TASK_NAME)
            if not found:
                from influxdb_client.domain.task_create_request import TaskCreateRequest
                tasks.create_task(task_create_request=TaskCreateRequest(flux=flux, org_id=org.id, status="active",
                                                                        description="Solis Monitor: 1-minute aggregates"))
                log.info("downsampling: created task %s (every %s)", TASK_NAME, EVERY)
            elif found[0].flux != flux or found[0].status != "active":
                task = found[0]
                task.flux, task.status = flux, "active"
                tasks.update_task(task)
                log.info("downsampling: updated task %s", TASK_NAME)
            return True
        except Exception as exc:
            log.warning("downsampling: could not set up the bucket and task: %s", exc)
            return False

    # ---------------------------------------------------------------- filling the past
    def _first_time(self, bucket: str, measurement: str) -> datetime | None:
        q = (f'from(bucket: "{bucket}") |> range(start: 0) |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "grid_voltage") '
             f'|> first() |> keep(columns: ["_time"])')
        times = [r.get_time() for table in self.client.query_api().query(q) for r in table.records]
        return min(times) if times else None

    def needs_backfill(self) -> tuple[datetime, datetime] | None:
        """(start, stop) of the history the second bucket lacks, or None: the raw bucket's history starts more than a
        day before the aggregates do (before the task existed, or the aggregates were lost)."""
        raw_first = self._first_time(self.source, "solis")
        if raw_first is None:
            return None
        agg_first = self._first_time(self.target, "solis")
        if agg_first is not None and agg_first <= raw_first + CHUNK:
            return None
        stop = agg_first if agg_first is not None else datetime.fromtimestamp(self.clock(), timezone.utc)
        return raw_first, stop

    def aggregate(self, start: datetime, stop: datetime) -> None:
        """Aggregate [start, stop), widened to whole minutes: a chunk edge inside a minute would leave that minute's mean
        to whichever chunk wrote it last, from only part of its readings."""
        start, stop = _floor_minute(start), _ceil_minute(stop)
        if stop <= start:
            return
        self.client.query_api().query(aggregation_flux(self.source, self.target, self.org, f"start: {_iso(start)}, stop: {_iso(stop)}"))

    def backfill(self, start: datetime, stop: datetime) -> int:
        """Aggregate [start, stop) a day at a time, newest first; returns the number of chunks. The edges are whole minutes,
        so every chunk boundary is too (and no chunk shrinks to less than a second, which InfluxDB refuses as empty)."""
        start, stop = _floor_minute(start), _ceil_minute(stop)
        n, end = 0, stop
        while end > start:
            begin = max(start, end - CHUNK)
            self.aggregate(begin, end)
            self.backfilled_to, end, n = begin, begin, n + 1
            self.sleep(0.2)                                 # leave room for the poller's own writes
        return n

    def repair_recent(self) -> None:
        """Re-aggregate the last day: covers the minutes a database restart or a stopped task left out."""
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        self.aggregate(now - REPAIR_SPAN, now)

    # ---------------------------------------------------------------- the retention split
    def prune(self) -> int:
        """Delete the 10-second readings older than raw_days, oldest day first; returns the number of days deleted.
        Each day is aggregated again right before, so the 1-minute data for it is there whatever happened earlier."""
        if not self.raw_days:
            return 0
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        cutoff = (now - timedelta(days=self.raw_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        first = self._first_time(self.source, "solis")
        if first is None or first >= cutoff:
            return 0
        day, n = first.replace(hour=0, minute=0, second=0, microsecond=0), 0
        delete = self.client.delete_api()
        while day < cutoff:
            end = min(day + CHUNK, cutoff)
            self.aggregate(day, end)
            delete.delete(day, end, '_measurement="solis"', bucket=self.source, org=self.org)
            day, n = end, n + 1
            self.sleep(0.2)
        return n

    # ---------------------------------------------------------------- the background thread
    def run_in_background(self, stop_event: threading.Event) -> threading.Thread:
        def work():
            if not self.ensure():
                return
            try:
                gap = self.needs_backfill()
                if gap:
                    log.info("downsampling: filling %s from %s to %s (a day at a time, newest first)", self.target, gap[0].date(), gap[1].date())
                    n = self.backfill(*gap)
                    log.info("downsampling: backfill done (%d days)", n)
                self.repair_recent()
            except Exception as exc:
                log.warning("downsampling: backfill failed: %s", exc)
            last_prune = -1e9
            while True:
                if self.raw_days and self.clock() - last_prune >= 86400:
                    try:
                        n = self.prune()
                        if n:
                            log.info("retention: deleted the 10-second readings of %d day(s) older than %d days (the 1-minute aggregates stay)", n, self.raw_days)
                        last_prune = self.clock()
                    except Exception as exc:
                        log.warning("retention: could not delete old 10-second readings: %s", exc)
                if stop_event.wait(REPAIR_EVERY_S):
                    return
                try:
                    self.ensure()
                    self.repair_recent()
                except Exception as exc:
                    log.debug("downsampling: repair failed: %s", exc)
        t = threading.Thread(target=work, daemon=True, name="downsample")
        t.start()
        return t
