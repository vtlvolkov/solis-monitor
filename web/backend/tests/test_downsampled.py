"""The web side of downsampling: which bucket a query reads, and the fallback to raw."""
from datetime import datetime, timedelta, timezone

from app import influx
from app.config import Settings
from app.influx import InfluxRepo, history_between_flux, history_flux, use_downsampled, window_max_flux

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)


def test_long_spans_with_minute_windows_use_the_aggregates():
    assert use_downsampled(86400, "5m") and use_downsampled(30 * 86400, "1h") and use_downsampled(12 * 3600, "1m")
    assert not use_downsampled(6 * 3600, "1m")            # a live range stays raw (the last minute is not aggregated yet)
    assert not use_downsampled(86400, "10s")              # a raw window can only come from the raw bucket
    assert not use_downsampled(3600, "10s")


def test_flux_names_the_measurement_of_the_aggregate():
    assert '_measurement == "solis_max"' in window_max_flux("solis_1m", ["grid_import_today"], NOW - timedelta(days=30), "15m", None, "solis_max")
    assert '_measurement == "solis_min"' in history_flux("solis_1m", ["grid_voltage"], 86400, "5m", "min", "solis_min")
    assert 'from(bucket: "solis") ' in history_between_flux("solis", ["grid_voltage"], NOW - timedelta(hours=1), NOW, "1m", "mean")
    for bad in (lambda: window_max_flux("b", ["grid_power"], NOW, "15m", None, "solis_min"), lambda: history_flux("b", ["grid_power"], 10, "1m", "mean", "other")):
        try:
            bad()
        except AssertionError:
            continue
        raise AssertionError("accepted a bad measurement")


class FakeApi:
    def __init__(self, ready):
        self.ready, self.queries = ready, []

    def query(self, q):
        self.queries.append(q)
        if "|> last() |> keep" in q:                      # the readiness check
            if self.ready == "error":
                raise RuntimeError("unauthorized access")
            class R:
                def get_time(self): return NOW
            class T:
                records = [R()] if self.ready else []
            return [T()]
        return []


def repo(ready, clock):
    r = InfluxRepo.__new__(InfluxRepo)
    r.bucket, r.bucket_1m, r.api, r.clock, r._ready = "solis", "solis_1m", FakeApi(ready), clock, (-1e9, False)
    return r


def test_reads_the_aggregates_when_ready_and_raw_otherwise():
    t = [0.0]
    r = repo(True, lambda: t[0])
    r.history(["grid_power"], 7 * 86400, "30m", "mean")
    assert 'from(bucket: "solis_1m")' in r.api.queries[-1] and '_measurement == "solis"' in r.api.queries[-1]
    r.window_max(["grid_import_today"], NOW - timedelta(days=30), "15m", NOW)
    assert 'from(bucket: "solis_1m")' in r.api.queries[-1] and '_measurement == "solis_max"' in r.api.queries[-1]
    r.history(["grid_power"], 3600, "10s", "mean")
    assert 'from(bucket: "solis")' in r.api.queries[-1]                          # live range: raw
    assert sum("|> last() |> keep" in q for q in r.api.queries) == 1              # readiness checked once, then remembered
    t[0] += 301
    r.history(["grid_power"], 7 * 86400, "30m", "mean")
    assert sum("|> last() |> keep" in q for q in r.api.queries) == 2              # ... and again after five minutes

    for state in (False, "error"):                                                # no data yet, or no permission on an old token
        r = repo(state, lambda: 0.0)
        r.history(["grid_power"], 7 * 86400, "30m", "mean")
        assert 'from(bucket: "solis")' in r.api.queries[-1] and '_measurement == "solis"' in r.api.queries[-1]


def test_settings_default_the_aggregate_bucket_name(monkeypatch):
    monkeypatch.setenv("INFLUX_BUCKET", "solis"); monkeypatch.delenv("INFLUX_BUCKET_1M", raising=False)
    assert Settings().influx_bucket_1m == "solis_1m"
    monkeypatch.setenv("INFLUX_BUCKET_1M", "agg")
    assert Settings().influx_bucket_1m == "agg"
    monkeypatch.setenv("INFLUX_BUCKET_1M", "")
    assert Settings().influx_bucket_1m == "solis_1m"
