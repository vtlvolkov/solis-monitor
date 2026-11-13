from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.influx import histogram_flux, hourly_excursions_flux, hourly_outside_flux
from app.main import create_app
from app.routes.grid import percentiles

NOW = datetime(2026, 11, 12, 16, 30, tzinfo=timezone.utc)     # 18:30 Kyiv; the hourly rows start on the hour, so the last one is inside [start, now)
KYIV = "Europe/Kyiv"
H = timedelta(hours=1)


class Repo:
    """Two days of hourly rows: a dip every evening (18:00-20:00 Kyiv = 16:00-18:00 UTC), one outage yesterday."""

    def __init__(self):
        self.calls = []
        top = NOW.replace(minute=0)
        self.hours = [top - H * i for i in range(47, -1, -1)]          # oldest first, each an hour's start

    def latest(self): return NOW, {}
    def history(self, *a): return [], {}
    def history_between(self, *a): return [], {}
    def window_max(self, *a, **k): return []
    def first_time(self): return NOW - timedelta(days=40)
    def control_log(self, limit): return []

    def events(self, days):
        t0 = NOW - timedelta(hours=26)
        return [{"time": t0, "kind": "outage", "key": "grid", "state": "start", "name": "Grid outage", "severity": "warning", "soc": 80.0, "load": 500.0},
                {"time": t0 + timedelta(minutes=50), "kind": "outage", "key": "grid", "state": "end", "name": "Grid outage", "severity": "warning", "soc": 70.0, "load": None},
                {"time": NOW - timedelta(days=20), "kind": "outage", "key": "grid", "state": "start", "name": "Grid outage", "severity": "warning", "soc": 80.0, "load": 500.0},
                {"time": NOW - timedelta(days=20, hours=-1), "kind": "outage", "key": "grid", "state": "end", "name": "Grid outage", "severity": "warning", "soc": 70.0, "load": None}]

    def histogram(self, field, start, stop, bin_start, width, count, min_valid):
        self.calls.append(("hist", field, start, stop, bin_start, width, count, min_valid))
        if field == "grid_voltage":
            return [(200.0, 10), (225.0, 100), (230.0, 300), (235.0, 90)]
        return [(49.97, 50), (50.0, 400), (50.03, 40)]

    def _in(self, start, stop):
        return [t for t in self.hours if start <= t < stop]

    def hourly(self, field, start, stop, fn, min_valid):
        self.calls.append((fn, field, start, stop))
        def val(t):
            evening = t.hour in (16, 17)                                # 18:00-19:59 Kyiv
            return {"min": 198.0 if evening else 215.0, "max": 245.0, "mean": 222.0 if evening else 231.0, "count": 360.0}[fn]
        return [(t, val(t)) for t in self._in(start, stop)]

    def hourly_outside(self, field, start, stop, lo, hi, min_valid):
        self.calls.append(("out", field, lo, hi))
        if field == "grid_frequency":
            return [(self.hours[-1], 3)] if self.hours[-1] >= start else []
        return [(t, 120) for t in self._in(start, stop) if t.hour in (16, 17)]       # 20 minutes of each evening hour

    def hourly_excursions(self, field, start, stop, limit, below, min_valid):
        self.calls.append(("exc", limit, below))
        return [(t, 2) for t in self._in(start, stop) if t.hour == 16] if below else []


def client():
    repo = Repo()
    app = create_app(repo=repo, control=object(), settings=Settings(static_dir="/nonexistent", tz=KYIV), now=lambda: NOW)
    return TestClient(app), repo


def test_flux_builders_only_take_known_fields_and_numbers():
    q = histogram_flux("b", "grid_voltage", NOW - H, NOW, 150.0, 1.0, 150, 100.0)
    assert 'r._field == "grid_voltage" and r._value >= 100.0' in q and "linearBins(start: 150.0, width: 1.0, count: 150" in q and "difference(nonNegative: true" in q
    assert "range(start: 2026-11-12T15:30:00Z, stop: 2026-11-12T16:30:00Z)" in q
    assert "r._value < 207.0 or r._value > 253.0" in hourly_outside_flux("b", "grid_voltage", NOW - H, NOW, 207, 253, 100)
    assert "if r._value > 253.0 then 1 else 0" in hourly_excursions_flux("b", "grid_voltage", NOW - H, NOW, 253, False, 100)
    for bad in (lambda: histogram_flux("b", "nope", NOW - H, NOW, 150.0, 1.0, 150, 100.0), lambda: histogram_flux("b", "grid_voltage", NOW - H, NOW, 150.0, 1.0, 1000, 100.0)):
        try:
            bad()
        except AssertionError:
            continue
        raise AssertionError("accepted a bad query")


def test_hourly_stats_may_come_from_the_minute_aggregates_but_counts_never():
    from app.influx import InfluxRepo, hourly_flux
    assert '_measurement == "solis_min"' in hourly_flux("solis_1m", "grid_voltage", NOW - H, NOW, "min", 100.0, "solis_min")
    r = InfluxRepo.__new__(InfluxRepo)
    calls = []
    class Api:
        def query(self, q): calls.append(q); return []
    r.bucket, r.bucket_1m, r.api, r.slow_api, r.clock, r._ready = "solis", "solis_1m", Api(), Api(), lambda: 0.0, (0.0, True)
    r.hourly("grid_voltage", NOW - timedelta(days=30), NOW, "max", 100.0)
    assert 'from(bucket: "solis_1m")' in calls[-1] and '_measurement == "solis_max"' in calls[-1]
    r.hourly("grid_voltage", NOW - timedelta(days=30), NOW, "count", 100.0)
    assert 'from(bucket: "solis")' in calls[-1] and '_measurement == "solis"' in calls[-1]
    r.histogram("grid_voltage", NOW - timedelta(days=30), NOW, 150.0, 1.0, 150, 100.0)
    assert 'histogram(' in calls[-1]                                          # went through slow_api (the only one here)


def test_histogram_has_an_overflow_bin_that_is_dropped():
    from app.influx import InfluxRepo
    assert "infinity: true" in histogram_flux("b", "grid_frequency", NOW - H, NOW, 49.5, 0.01, 100, 40.0)
    r = InfluxRepo.__new__(InfluxRepo)
    class Rec:
        def __init__(self, le, v): self.values, self._v = {"le": le}, v
        def get_value(self): return self._v
    class T:
        records = [Rec(49.51, 3), Rec(50.0, 400), Rec(float("inf"), 1)]       # the 59.5 Hz glitch lands in +Inf
    class Api:
        def query(self, q): return [T()]
    r.bucket, r.slow_api = "solis", Api()
    assert r.histogram("grid_frequency", NOW - H, NOW, 49.5, 0.01, 100, 40.0) == [(49.5, 3), (49.99, 400)]


def test_percentiles_from_bins():
    bins = [(200.0, 10), (225.0, 100), (230.0, 300), (235.0, 90)]
    assert percentiles(bins, 1.0) == [225.5, 235.5]
    assert percentiles([], 1.0) == [None, None]


def test_quality_shapes_hours_days_and_outages_by_local_time():
    c, repo = client()
    r = c.get("/api/grid/quality?range=7d")
    assert r.status_code == 200
    b = r.json()
    assert b["limits"] == {"v_min": 207.0, "v_max": 253.0, "hz_min": 49.8, "hz_max": 50.2}
    assert b["voltage"]["min"] == 198.0 and b["voltage"]["max"] == 245.0 and b["voltage"]["p05"] == 225.5 and b["voltage"]["p95"] == 235.5
    assert b["frequency"]["mean"] == 50.0 and b["frequency"]["min"] == 49.97 and b["frequency"]["max"] == 50.04
    total = 48 * 360
    assert b["out"]["v_pct"] == round(100 * 4 * 120 / total, 2) and b["out"]["v_min"] == 80.0      # 4 evening hours x 120 readings x 10 s
    assert b["out"]["sags"] == 4 and b["out"]["surges"] == 0
    hours = {h["hour"]: h for h in b["hours"]}
    assert hours[18]["min"] == 198.0 and hours[18]["out_pct"] == round(100 * 120 / 360, 1) and hours[12]["out_pct"] == 0.0   # local hours
    assert hours[18]["mean"] == 222.0 and hours[12]["mean"] == 231.0
    days = {d["date"]: d for d in b["days"]}
    assert set(days) >= {"2026-11-11", "2026-11-12"} and days["2026-11-11"]["sags"] == 2 and days["2026-11-11"]["out_min"] == 40.0
    assert days["2026-11-11"]["outages"] == 1 and days["2026-11-11"]["outage_s"] == 3000 and days["2026-11-12"]["outages"] == 0
    assert b["outages"] == {"count": 1, "total_s": 3000, "longest_s": 3000}       # the one 20 days ago is outside 7d
    assert [x[0] for x in repo.calls[:2]] == ["hist", "hist"] and repo.calls[0][7] == 100.0 and repo.calls[0][2] == NOW - timedelta(days=7)


def test_limits_are_passed_through_and_checked():
    c, repo = client()
    r = c.get("/api/grid/quality?range=24h&v_min=200&v_max=250&hz_min=49.5&hz_max=50.5")
    assert r.status_code == 200 and r.json()["limits"] == {"v_min": 200.0, "v_max": 250.0, "hz_min": 49.5, "hz_max": 50.5}
    assert ("out", "grid_voltage", 200.0, 250.0) in repo.calls and ("exc", 200.0, True) in repo.calls and ("exc", 250.0, False) in repo.calls
    for bad in ("v_min=100", "v_max=230", "hz_min=50", "hz_max=60", "v_min=abc"):
        assert c.get(f"/api/grid/quality?range=24h&{bad}").status_code in (400, 422), bad
    assert c.get("/api/grid/quality?range=90d").status_code == 400


def test_answers_are_cached_per_period_and_limits():
    c, repo = client()
    c.get("/api/grid/quality?range=7d"); n = len(repo.calls)
    c.get("/api/grid/quality?range=7d"); assert len(repo.calls) == n
    c.get("/api/grid/quality?range=7d&v_min=200"); assert len(repo.calls) > n


def test_one_exact_local_day():
    c, repo = client()
    r = c.get("/api/grid/quality?date=2026-11-11")
    assert r.status_code == 200
    b = r.json()
    assert b["date"] == "2026-11-11" and b["range"] == "2026-11-11"
    start = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)                   # Kyiv midnight (UTC+2 in November)
    assert b["start"] == round(start.timestamp()) and b["stop"] == round((start + timedelta(days=1)).timestamp())
    assert repo.calls[0][2] == start and repo.calls[0][3] == start + timedelta(days=1)
    assert [d["date"] for d in b["days"]] == ["2026-11-11"]
    assert b["days"][0]["sags"] == 2 and b["days"][0]["outages"] == 1 and b["days"][0]["outage_s"] == 3000
    assert b["outages"]["count"] == 1 and b["out"]["sags"] == 2                  # only that day's evening, only that day's outage
    hours = {h["hour"]: h for h in b["hours"]}
    assert hours[18]["min"] == 198.0 and hours[12]["out_pct"] == 0.0
    assert c.get("/api/grid/quality?date=2026-11-13").status_code == 400         # tomorrow
    assert c.get("/api/grid/quality?date=11-11-2026").status_code == 400
    n = len(repo.calls); c.get("/api/grid/quality?date=2026-11-11"); assert len(repo.calls) == n   # a past day is cached for hours


def test_days_older_than_the_raw_readings_are_refused_and_the_picker_is_told():
    repo = Repo()
    app = create_app(repo=repo, control=object(), now=lambda: NOW,
                     settings=Settings(static_dir="/nonexistent", tz=KYIV, raw_retention_days=40))
    c = TestClient(app)
    assert c.get("/api/grid/quality?range=7d").json()["raw_from"] == "2026-10-04"       # 39 days back: one day of margin
    r = c.get("/api/grid/quality?date=2026-10-03")
    assert r.status_code == 400 and "kept for the last 40 days" in r.json()["detail"]
    assert c.get("/api/grid/quality?date=2026-10-04").status_code == 200
    everything = TestClient(create_app(repo=Repo(), control=object(), now=lambda: NOW, settings=Settings(static_dir="/nonexistent", tz=KYIV, raw_retention_days=0)))
    assert everything.get("/api/grid/quality?date=2025-01-01").status_code == 200 and everything.get("/api/grid/quality").json()["raw_from"] is None
