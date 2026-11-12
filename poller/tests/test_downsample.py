from datetime import datetime, timedelta, timezone

import downsample
from downsample import Downsampler, aggregation_flux, task_flux

T0 = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


class Org:
    id = "org1"


class Record:
    def __init__(self, t): self._t = t
    def get_time(self): return self._t


class Table:
    def __init__(self, times): self.records = [Record(t) for t in times]


class Task:
    def __init__(self, flux, status="active"): self.flux, self.status, self.name = flux, status, "solis-1m"


class FakeClient:
    """Just enough of influxdb_client: buckets, tasks, organisations and a query API that records what it ran."""

    def __init__(self, raw_first=None, agg_first=None, task=None, bucket=False):
        self.buckets = {"solis_1m": object()} if bucket else {}
        self.tasks = [task] if task else []
        self.queries = []
        self.raw_first, self.agg_first = raw_first, agg_first
        outer = self

        class Buckets:
            def find_bucket_by_name(self, name): return outer.buckets.get(name)
            def create_bucket(self, bucket_name, org, description): outer.buckets[bucket_name] = object()

        class Tasks:
            def find_tasks(self, name): return [t for t in outer.tasks if t.name == name]
            def create_task(self, task_create_request): outer.tasks.append(Task(task_create_request.flux, task_create_request.status))
            def update_task(self, task): outer.updated = task

        class Orgs:
            def find_organizations(self, org): return [Org()]

        class Query:
            def query(self, q):
                outer.queries.append(q)
                if "|> first()" in q:
                    first = outer.raw_first if 'bucket: "solis"' in q else outer.agg_first
                    return [Table([first])] if first else []
                return []

        self._b, self._t, self._o, self._q = Buckets(), Tasks(), Orgs(), Query()

    def buckets_api(self): return self._b
    def tasks_api(self): return self._t
    def organizations_api(self): return self._o
    def query_api(self): return self._q


def make(**kw):
    c = FakeClient(**kw)
    return c, Downsampler(c, "home", "solis", "solis_1m", clock=lambda: T0.timestamp(), sleep=lambda s: None)


def test_task_script_aggregates_mean_min_and_max_into_the_second_bucket():
    f = task_flux("solis", "solis_1m", "home")
    assert f.startswith('option task = {name: "solis-1m", every: 1m, offset: 30s}')
    assert 'from(bucket: "solis") |> range(start: -3m)' in f
    assert f.count('to(bucket: "solis_1m", org: "home")') == 3
    assert 'set(key: "_measurement", value: "solis_min")' in f and 'set(key: "_measurement", value: "solis_max")' in f
    assert 'range(start: 2026-11-11T18:00:00Z, stop: 2026-11-12T18:00:00Z)' in aggregation_flux("solis", "solis_1m", "home", "start: 2026-11-11T18:00:00Z, stop: 2026-11-12T18:00:00Z")


def test_ensure_creates_bucket_and_task_once_and_updates_a_changed_script():
    c, d = make()
    assert d.ensure() is True
    assert "solis_1m" in c.buckets and len(c.tasks) == 1 and c.tasks[0].flux == task_flux("solis", "solis_1m", "home")
    assert d.ensure() is True and len(c.tasks) == 1 and not hasattr(c, "updated")         # nothing to do the second time
    c2, d2 = make(bucket=True, task=Task("option task = {name: \"solis-1m\", every: 5m}\nold", status="inactive"))
    assert d2.ensure() is True and c2.updated.flux == task_flux("solis", "solis_1m", "home") and c2.updated.status == "active"


def test_backfill_is_needed_only_when_the_aggregates_start_later_than_the_raw_history():
    _, d = make(raw_first=None)
    assert d.needs_backfill() is None                                             # an empty database
    _, d = make(raw_first=T0 - timedelta(days=40), agg_first=None)
    assert d.needs_backfill() == (T0 - timedelta(days=40), T0)                    # never aggregated: everything up to now
    _, d = make(raw_first=T0 - timedelta(days=40), agg_first=T0 - timedelta(days=3))
    assert d.needs_backfill() == (T0 - timedelta(days=40), T0 - timedelta(days=3))  # the task ran for 3 days: fill the rest
    _, d = make(raw_first=T0 - timedelta(days=40), agg_first=T0 - timedelta(days=40, hours=-6))
    assert d.needs_backfill() is None                                             # within a day of the start: done


def test_backfill_goes_a_day_at_a_time_newest_first():
    c, d = make()
    n = d.backfill(T0 - timedelta(days=2, hours=6), T0)
    assert n == 3
    ranges = [q.split("range(")[1].split(")")[0] for q in c.queries]
    assert ranges == ["start: 2026-11-11T18:00:00Z, stop: 2026-11-12T18:00:00Z",
                      "start: 2026-11-10T18:00:00Z, stop: 2026-11-11T18:00:00Z",
                      "start: 2026-11-10T12:00:00Z, stop: 2026-11-10T18:00:00Z"]
    assert d.backfilled_to == T0 - timedelta(days=2, hours=6)
    assert all('to(bucket: "solis_1m"' in q for q in c.queries)


def test_repair_covers_the_last_day():
    c, d = make()
    d.repair_recent()
    assert c.queries[-1].count("range(start: 2026-11-11T18:00:00Z, stop: 2026-11-12T18:00:00Z)") == 1


def test_setup_failure_is_logged_not_raised():
    c, d = make()
    c._o.find_organizations = lambda org: []
    assert d.ensure() is False
