from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.influx import histogram_flux, hourly_excursions_flux, hourly_outside_flux
from app.main import create_app
from app.routes.grid import percentiles

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)      # 18:00 Kyiv
KYIV = "Europe/Kyiv"
H = timedelta(hours=1)


class Repo:
    """Two days of hourly rows: a dip every evening (18:00-20:00 Kyiv = 16:00-18:00 UTC), one outage yesterday."""

    def __init__(self):
        self.calls = []
        self.hours = [NOW - H * i for i in range(47, -1, -1)]          # oldest first

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

    def histogram(self, field, range_s, start, width, count, min_valid):
        self.calls.append(("hist", field, range_s, start, width, count, min_valid))
        if field == "grid_voltage":
            return [(200.0, 10), (225.0, 100), (230.0, 300), (235.0, 90)]
        return [(49.97, 50), (50.0, 400), (50.03, 40)]

    def hourly(self, field, range_s, fn, min_valid):
        self.calls.append((fn, field, range_s))
        def val(t):
            evening = t.hour in (16, 17)                                # 18:00-19:59 Kyiv
            return {"min": 198.0 if evening else 215.0, "max": 245.0, "mean": 222.0 if evening else 231.0, "count": 360.0}[fn]
        return [(t, val(t)) for t in self.hours]

    def hourly_outside(self, field, range_s, lo, hi, min_valid):
        self.calls.append(("out", field, lo, hi))
        if field == "grid_frequency":
            return [(self.hours[-1], 3)]
        return [(t, 120) for t in self.hours if t.hour in (16, 17)]       # 20 minutes of each evening hour

    def hourly_excursions(self, field, range_s, limit, below, min_valid):
        self.calls.append(("exc", limit, below))
        return [(t, 2) for t in self.hours if t.hour == 16] if below else []


def client():
    repo = Repo()
    app = create_app(repo=repo, control=object(), settings=Settings(static_dir="/nonexistent", tz=KYIV), now=lambda: NOW)
    return TestClient(app), repo


def test_flux_builders_only_take_known_fields_and_numbers():
    q = histogram_flux("b", "grid_voltage", 86400, 150.0, 1.0, 150, 100.0)
    assert 'r._field == "grid_voltage" and r._value >= 100.0' in q and "linearBins(start: 150.0, width: 1.0, count: 150" in q and "difference(nonNegative: true" in q
    assert "r._value < 207.0 or r._value > 253.0" in hourly_outside_flux("b", "grid_voltage", 3600, 207, 253, 100)
    assert "if r._value > 253.0 then 1 else 0" in hourly_excursions_flux("b", "grid_voltage", 3600, 253, False, 100)
    for bad in (lambda: histogram_flux("b", "nope", 3600, 150.0, 1.0, 150, 100.0), lambda: histogram_flux("b", "grid_voltage", 3600, 150.0, 1.0, 1000, 100.0)):
        try:
            bad()
        except AssertionError:
            continue
        raise AssertionError("accepted a bad query")


def test_hourly_stats_may_come_from_the_minute_aggregates_but_counts_never():
    from app.influx import InfluxRepo, hourly_flux
    assert '_measurement == "solis_min"' in hourly_flux("solis_1m", "grid_voltage", 86400, "min", 100.0, "solis_min")
    r = InfluxRepo.__new__(InfluxRepo)
    calls = []
    class Api:
        def query(self, q): calls.append(q); return []
    r.bucket, r.bucket_1m, r.api, r.slow_api, r.clock, r._ready = "solis", "solis_1m", Api(), Api(), lambda: 0.0, (0.0, True)
    r.hourly("grid_voltage", 30 * 86400, "max", 100.0)
    assert 'from(bucket: "solis_1m")' in calls[-1] and '_measurement == "solis_max"' in calls[-1]
    r.hourly("grid_voltage", 30 * 86400, "count", 100.0)
    assert 'from(bucket: "solis")' in calls[-1] and '_measurement == "solis"' in calls[-1]
    r.histogram("grid_voltage", 30 * 86400, 150.0, 1.0, 150, 100.0)
    assert 'histogram(' in calls[-1]                                          # went through slow_api (the only one here)


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
    assert [x[0] for x in repo.calls[:2]] == ["hist", "hist"] and repo.calls[0][6] == 100.0


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
