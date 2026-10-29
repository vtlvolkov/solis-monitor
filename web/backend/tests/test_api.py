import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import shaping, validation
from app.config import Settings
from app.influx import history_flux
from app.main import create_app

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self, latest=None, history=None, rows=(), fail=False):
        self._latest, self._history, self._rows, self.fail = latest, history, list(rows), False
        self.fail = fail
        self.calls = []

    def _check(self):
        if self.fail:
            raise ConnectionError("connection refused")

    def latest(self):
        self._check()
        return self._latest

    def history(self, fields, range_s, every, agg):
        self._check()
        self.calls.append(("history", fields, range_s, every, agg))
        if self._history:
            return self._history
        return [NOW - timedelta(minutes=2), NOW - timedelta(minutes=1)], {f: [1.0, 2.0] for f in fields}

    def window_max(self, fields, start, every):
        self._check()
        self.calls.append(("window_max", fields, start, every))
        return self._rows


def client(repo, **kw):
    return TestClient(create_app(repo=repo, settings=Settings(static_dir="/nonexistent", **kw), now=lambda: NOW))


# ---------------------------------------------------------------- whitelists

@pytest.mark.parametrize("q", [
    "fields=battery_soc;drop&range=6h",
    "fields=nope&range=6h",
    'fields=battery_soc") |> yield()&range=6h',
    "fields=&range=6h",
    "fields=battery_soc&range=5h",
    "fields=battery_soc&range=-1y",
    "fields=battery_soc&range=6h&agg=sum",
    "fields=battery_soc&range=6h&agg=mean)",
    "fields=battery_soc&range=6h&every=7m",
    "fields=battery_soc&range=6h&every=1h)|>drop()",
    "fields=battery_soc&range=30d&every=10s",          # 259200 points > hard cap
    "fields=" + ",".join(validation.FIELDS[:13]) + "&range=6h",
])
def test_history_rejects_bad_input(q):
    repo = FakeRepo()
    r = client(repo).get("/api/history?" + q)
    assert r.status_code == 400, r.text
    assert repo.calls == []          # nothing reached the query layer


def test_history_accepts_valid_input_and_passes_canonical_values():
    repo = FakeRepo()
    r = client(repo).get("/api/history?fields=grid_power,battery_soc,grid_power&range=24h&every=auto&agg=max")
    assert r.status_code == 200
    body = r.json()
    assert body["every"] == "5m" and body["agg"] == "max" and body["range"] == "24h"
    assert repo.calls == [("history", ["grid_power", "battery_soc"], 86400, "5m", "max")]
    assert set(body["series"]) == {"grid_power", "battery_soc"}
    assert len(body["t"]) == len(body["series"]["grid_power"]) == 2


def test_every_field_is_known():
    assert len(validation.FIELDS) == len(validation.FIELD_SET) == 54
    for f in validation.ENERGY_FIELDS:
        assert f in validation.FIELD_SET


def test_flux_contains_only_whitelisted_tokens():
    q = history_flux("solis", ["grid_power", "battery_soc"], 21600, "1m", "mean")
    assert 'r._field == "grid_power" or r._field == "battery_soc"' in q
    assert "range(start: -21600s)" in q and "every: 1m, fn: mean" in q
    with pytest.raises(AssertionError):
        history_flux("solis", ['x" or true'], 21600, "1m", "mean")
    with pytest.raises(AssertionError):
        history_flux("solis", ["grid_power"], 21600, "1m) |> drop(", "mean")


# ---------------------------------------------------------------- every=auto

@pytest.mark.parametrize("rng,expected", [
    ("1h", "10s"),      # 360 points
    ("3h", "30s"),      # 360
    ("6h", "1m"),       # 360
    ("12h", "2m"),      # 360
    ("24h", "5m"),      # 288
    ("2d", "10m"),      # 288
    ("7d", "30m"),      # 336
    ("30d", "3h"),      # 240
])
def test_auto_window(rng, expected):
    range_s = validation.RANGES[rng]
    w = validation.auto_window(range_s)
    assert w == expected
    assert range_s / validation.WINDOWS[w] <= validation.MAX_POINTS
    finer = list(validation.WINDOWS)[list(validation.WINDOWS).index(w) - 1] if w != "10s" else None
    if finer:   # the next finer window would exceed the budget
        assert range_s / validation.WINDOWS[finer] > validation.MAX_POINTS


def test_auto_window_never_exceeds_budget_for_any_range():
    for range_s in validation.RANGES.values():
        assert range_s / validation.WINDOWS[validation.auto_window(range_s)] <= 500


def test_explicit_every_allowed_within_hard_cap():
    assert validation.parse_every("1m", validation.RANGES["24h"]) == "1m"     # 1440 pts


# ---------------------------------------------------------------- live / online flag

@pytest.mark.parametrize("age,online", [(0, True), (10, True), (45, True), (45.5, False), (180, False)])
def test_live_online_flag(age, online):
    repo = FakeRepo(latest=(NOW - timedelta(seconds=age), {"battery_soc": 99.0, "grid_power": -12.5}))
    body = client(repo).get("/api/live").json()
    assert body["online"] is online
    assert body["age_s"] == pytest.approx(age, abs=0.1)
    assert body["fields"]["battery_soc"] == 99.0
    assert body["fields"]["grid_power"] == -12.5
    assert set(body["fields"]) == set(validation.FIELDS)        # every field present
    assert body["fields"]["pv_power"] is None                   # missing -> null


def test_live_no_data():
    body = client(FakeRepo(latest=None)).get("/api/live").json()
    assert body == {"time": None, "age_s": None, "online": False,
                    "fields": {f: None for f in validation.FIELDS}}


def test_live_nan_becomes_null():
    p = shaping.live_payload((NOW, {"battery_soc": float("nan")}), NOW)
    assert p["fields"]["battery_soc"] is None


def test_influx_down_returns_503():
    c = client(FakeRepo(fail=True))
    for path in ("/api/live", "/api/history?fields=battery_soc&range=1h", "/api/energy/daily"):
        r = c.get(path)
        assert r.status_code == 503, path
        assert r.json()["detail"] == "InfluxDB unavailable"
    assert c.get("/healthz").json() == {"status": "ok"}


# ---------------------------------------------------------------- daily energy

KYIV = "Europe/Kyiv"


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_daily_start_is_local_midnight():
    # 2026-10-18 12:00 UTC = 15:00 Kyiv (EEST, UTC+3). 3 days -> 2026-10-16 00:00 Kyiv = 10-15 21:00 UTC.
    assert shaping.daily_start(NOW, 3, KYIV) == utc(2026, 10, 15, 21)
    assert shaping.daily_start(NOW, 1, KYIV) == utc(2026, 10, 17, 21)


def test_daily_groups_by_local_day_and_takes_max():
    rows = [
        # 10-17 local day: 10-16 21:00 UTC .. 10-17 20:59 UTC
        (utc(2026, 10, 16, 21, 0), "grid_import_today", 0.1),
        (utc(2026, 10, 17, 10, 0), "grid_import_today", 3.4),
        (utc(2026, 10, 17, 20, 45), "grid_import_today", 7.9),   # 23:45 Kyiv -> still 10-17
        (utc(2026, 10, 17, 20, 45), "pv_energy_today", 0.0),
        # 10-18 local day: counter reset at midnight
        (utc(2026, 10, 17, 21, 0), "grid_import_today", 0.2),    # 00:00 Kyiv -> 10-18
        (utc(2026, 10, 18, 11, 45), "grid_import_today", 4.1),
        (utc(2026, 10, 18, 11, 45), "load_consumption_today", 9.0),
        (utc(2026, 10, 18, 11, 45), "unexpected_field", 99.0),   # ignored
        (utc(2026, 9, 1, 12), "grid_import_today", 50.0),         # outside range, ignored
    ]
    days = shaping.aggregate_daily(rows, validation.ENERGY_FIELDS, 3, NOW, KYIV)
    assert [d["date"] for d in days] == ["2026-10-16", "2026-10-17", "2026-10-18"]
    assert days[0]["grid_import_today"] is None                     # no data -> null, day kept
    assert days[1]["grid_import_today"] == 7.9
    assert days[1]["pv_energy_today"] == 0.0
    assert days[2]["grid_import_today"] == 4.1
    assert days[2]["load_consumption_today"] == 9.0
    assert "unexpected_field" not in days[2]


def test_daily_across_dst_change():
    # Kyiv leaves DST on 2026-10-25 04:00 local (01:00 UTC). After that UTC+2.
    now = utc(2026, 10, 26, 12)
    rows = [
        (utc(2026, 10, 25, 21, 45), "grid_export_today", 1.5),    # 23:45 local on 10-25 (UTC+2)
        (utc(2026, 10, 25, 22, 0), "grid_export_today", 0.1),     # 00:00 local on 10-26
        (utc(2026, 10, 24, 21, 0), "grid_export_today", 0.3),     # 00:00 local on 10-25 (UTC+3)
    ]
    days = shaping.aggregate_daily(rows, validation.ENERGY_FIELDS, 2, now, KYIV)
    assert days[0] == {**days[0], "date": "2026-10-25", "grid_export_today": 1.5}
    assert days[1]["grid_export_today"] == 0.1


def test_daily_endpoint():
    repo = FakeRepo(rows=[(utc(2026, 10, 18, 9), "grid_import_today", 2.25)])
    r = client(repo, tz=KYIV).get("/api/energy/daily?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["tz"] == KYIV and len(body["days"]) == 7
    assert body["days"][-1] == {"date": "2026-10-18", "grid_import_today": 2.25,
                                **{f: None for f in validation.ENERGY_FIELDS if f != "grid_import_today"}}
    _, fields, start, every = repo.calls[0]
    assert start == utc(2026, 10, 11, 21) and every == "15m"


@pytest.mark.parametrize("days", ["0", "367", "-1", "abc"])
def test_daily_rejects_bad_days(days):
    repo = FakeRepo()
    assert client(repo).get(f"/api/energy/daily?days={days}").status_code in (400, 422)
    assert repo.calls == []


# ---------------------------------------------------------------- gaps

def test_insert_gaps():
    t0 = NOW
    times = [t0, t0 + timedelta(seconds=60), t0 + timedelta(seconds=600)]
    t, s = shaping.insert_gaps(times, {"a": [1, 2, 3]}, 60)
    assert s["a"] == [1, 2, None, 3]
    assert len(t) == 4


# ---------------------------------------------------------------- basic auth

def test_basic_auth_optional():
    assert client(FakeRepo(latest=None)).get("/api/live").status_code == 200


def test_basic_auth_enforced():
    c = client(FakeRepo(latest=None), basic_auth="me:secret")
    assert c.get("/api/live").status_code == 401
    assert c.get("/api/live").headers["www-authenticate"].startswith("Basic")
    assert c.get("/api/live", auth=("me", "wrong")).status_code == 401
    assert c.get("/api/live", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert c.get("/api/live", auth=("me", "secret")).status_code == 200
    assert c.get("/healthz").status_code == 200        # Docker healthcheck stays open
    assert c.get("/apple-touch-icon.png").status_code == 404   # no static dir here, but past the auth check
    assert c.get("/index.html").status_code == 401


def test_site_name():
    assert client(FakeRepo(latest=None)).get("/api/site").json() == {"name": ""}
    assert client(FakeRepo(latest=None), site_name="Cottage").get("/api/site").json() == {"name": "Cottage"}
