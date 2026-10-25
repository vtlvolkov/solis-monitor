from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import shaping
from app.config import Settings
from app.main import create_app
from app.tariff import Tariff

KYIV = "Europe/Kyiv"


def kyiv(y, mo, d, h, mi=0):
    # Kyiv is UTC+3 in October 2026 (before the 25th)
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) - timedelta(hours=3)


@pytest.mark.parametrize("minute,start,end,inside", [
    (23 * 60, "23:00", "07:00", True), (2 * 60, "23:00", "07:00", True), (7 * 60, "23:00", "07:00", False),
    (12 * 60, "23:00", "07:00", False), (60, "00:00", "06:00", True), (6 * 60, "00:00", "06:00", False),
])
def test_in_window(minute, start, end, inside):
    assert shaping.in_window(minute, start, end) is inside


TWO = Tariff(day=4.32, night=2.16, night_start="23:00", night_end="07:00")
ONE = Tariff(day=4.32)

ROWS = [
    (kyiv(2026, 10, 17, 22, 0), 9.0),     # day:   9.0 since midnight
    (kyiv(2026, 10, 17, 23, 0), 10.0),    # night: +1.0
    (kyiv(2026, 10, 17, 23, 45), 10.5),   # night: +0.5
    (kyiv(2026, 10, 18, 0, 0), 0.4),      # new day, counter reset: night +0.4
    (kyiv(2026, 10, 18, 6, 45), 3.0),     # night +2.6
    (kyiv(2026, 10, 18, 7, 0), 3.2),      # day +0.2
    (kyiv(2026, 10, 18, 12, 0), 3.1),     # glitch backwards: ignored
    (kyiv(2026, 10, 18, 12, 15), 4.0),    # day +0.8
]


def test_import_costs_counts_increments_and_resets_at_midnight():
    z = shaping.import_costs(ROWS, KYIV, lambda t: TWO)
    assert z[date(2026, 10, 17)] == pytest.approx({"day_kwh": 9.0, "night_kwh": 1.5, "day_cost": 9.0 * 4.32, "night_cost": 1.5 * 2.16, "saved": 1.5 * 2.16})
    assert z[date(2026, 10, 18)] == pytest.approx({"day_kwh": 1.0, "night_kwh": 3.0, "day_cost": 4.32, "night_cost": 3.0 * 2.16, "saved": 3.0 * 2.16})
    flat = shaping.import_costs(ROWS, KYIV, lambda t: ONE)                          # one price: everything is "day"
    assert flat[date(2026, 10, 18)] == pytest.approx({"day_kwh": 4.0, "night_kwh": 0.0, "day_cost": 4.0 * 4.32, "night_cost": 0.0, "saved": 0.0})


def test_a_price_change_only_prices_the_windows_after_it():
    """The 12:15 window and later are priced 5.00; everything before keeps 4.32 / 2.16 and its night hours."""
    start = kyiv(2026, 10, 18, 12, 15)
    after = Tariff(day=5.00, night=2.50, night_start="22:00", night_end="06:00")
    z = shaping.import_costs(ROWS, KYIV, lambda t: after if t >= start else TWO)
    d18, d17 = z[date(2026, 10, 18)], z[date(2026, 10, 17)]
    assert d17 == shaping.import_costs(ROWS, KYIV, lambda t: TWO)[date(2026, 10, 17)]    # the earlier day is untouched
    assert d18["night_kwh"] == pytest.approx(3.0) and d18["night_cost"] == pytest.approx(3.0 * 2.16)   # night windows before the change
    assert d18["day_kwh"] == pytest.approx(1.0) and d18["day_cost"] == pytest.approx(0.2 * 4.32 + 0.8 * 5.00)  # +0.2 before, +0.8 after


def test_cost_summary():
    z = lambda dk, nk: {"day_kwh": dk, "night_kwh": nk, "day_cost": dk * 4.32, "night_cost": nk * 2.16, "saved": nk * (4.32 - 2.16)}
    zones = {date(2026, 10, 17): z(9.0, 1.5), date(2026, 10, 18): z(1.0, 3.0), date(2026, 9, 30): z(50.0, 0.0)}
    c = shaping.cost_summary(zones, date(2026, 10, 18))
    assert c["today"] == {"day_kwh": 1.0, "night_kwh": 3.0, "kwh": 4.0, "day_cost": 4.32, "night_cost": 6.48, "cost": 10.8, "saved": 6.48}
    assert c["month"]["kwh"] == 14.5 and c["month"]["cost"] == pytest.approx(10.0 * 4.32 + 4.5 * 2.16, abs=0.01)


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


class Repo:
    def __init__(self):
        self.calls = []

    def window_max(self, fields, start, every):
        self.calls.append((fields, start, every))
        return [(kyiv(2026, 10, 18, 1), "grid_import_today", 2.0), (kyiv(2026, 10, 18, 10), "grid_import_today", 5.0)]


class Ctl:
    def cached_settings(self):
        return 200, {"tou": {"charge": []}, "control": {"server_time": "2026-10-18T15:00:00"}}


def client(**kw):
    repo = Repo()
    app = create_app(repo=repo, control=Ctl(), settings=Settings(static_dir="/nonexistent", tz=KYIV, **kw),
                     now=lambda: NOW)
    return TestClient(app), repo


def test_cost_not_configured():
    c, repo = client(tariff_day=None)
    body = c.get("/api/cost").json()
    assert body["configured"] is False and repo.calls == []


def test_rate_now_follows_the_local_clock():
    f = lambda utc_h, utc_m=0: shaping.rate_now(datetime(2026, 10, 18, utc_h, utc_m, tzinfo=timezone.utc), KYIV, TWO)
    assert f(12) == {"zone": "day", "price": 4.32, "until": "23:00"}                 # 15:00 Kyiv
    assert f(19, 59) == {"zone": "day", "price": 4.32, "until": "23:00"}              # 22:59
    assert f(20) == {"zone": "night", "price": 2.16, "until": "07:00"}                # 23:00, the night zone starts
    assert f(3, 59) == {"zone": "night", "price": 2.16, "until": "07:00"}             # 06:59
    assert f(4) == {"zone": "day", "price": 4.32, "until": "23:00"}                   # 07:00, back to the day zone
    assert shaping.rate_now(NOW, KYIV, ONE) is None                                    # single rate: no zones
    assert shaping.rate_now(NOW, KYIV, None) is None


def test_cost_endpoint():
    c, repo = client(tariff_day=4.32, tariff_night=2.16)
    body = c.get("/api/cost").json()
    assert body["configured"] and body["today"]["night_kwh"] == 2.0 and body["today"]["day_kwh"] == 3.0
    assert body["today"]["day_cost"] == 12.96 and body["today"]["night_cost"] == 4.32 and body["today"]["cost"] == 17.28
    assert body["now"] == {"zone": "day", "price": 4.32, "until": "23:00"}            # 15:00 in Kyiv
    fields, start, every = repo.calls[0]
    assert fields == ["grid_import_today"] and every == "15m" and start == kyiv(2026, 10, 1, 0)
    c.get("/api/cost")
    assert len(repo.calls) == 1                     # cached


def test_schedule_proxy():
    c, _ = client()
    assert c.get("/api/schedule").json()["control"]["server_time"] == "2026-10-18T15:00:00"


# ---------------------------------------------------------------- cost history
class HistRepo:
    """Two days of 15-minute window maxima for every energy counter, in Kyiv time."""
    def __init__(self):
        self.calls = []

    def first_time(self):
        return kyiv(2026, 10, 17, 9)

    def window_max(self, fields, start, every, stop=None):
        self.calls.append((fields, start, every, stop))
        rows = []
        for field, series in {
            "grid_import_today": {(17, 22, 0): 9.0, (17, 23, 0): 10.0, (18, 0, 0): 0.4, (18, 6, 45): 3.0, (18, 7, 0): 3.2, (18, 12, 15): 4.0},
            "load_consumption_today": {(17, 22, 0): 11.0, (18, 12, 15): 5.5},
            "pv_energy_today": {(18, 12, 15): 0.0},
            "grid_export_today": {(18, 12, 15): 0.0},
        }.items():
            rows += [(kyiv(2026, 10, d, h, m), field, v) for (d, h, m), v in series.items()]
        return rows


def hist_client(**kw):
    repo = HistRepo()
    app = create_app(repo=repo, control=Ctl(), settings=Settings(static_dir="/nonexistent", tz=KYIV, **kw), now=lambda: NOW)
    return TestClient(app), repo


def test_cost_days_splits_zones_and_totals():
    z = lambda dk, nk, dp=4.32, np_=2.16: {"day_kwh": dk, "night_kwh": nk, "day_cost": dk * dp, "night_cost": nk * np_, "saved": nk * (dp - np_)}
    zones = {date(2026, 10, 17): z(9.0, 1.5), date(2026, 10, 18): z(1.0, 3.0)}
    energy = [{"date": d, "grid_import_today": None, "grid_export_today": 0.5, "battery_charge_today": 2.0,
               "battery_discharge_today": 1.0, "load_consumption_today": 7.0, "pv_energy_today": None}
              for d in ("2026-10-16", "2026-10-17", "2026-10-18")]
    out = shaping.cost_days(energy, zones)
    assert out["days"][0] == {"date": "2026-10-16", "has_data": False}        # no readings that day
    d17 = out["days"][1]
    assert d17["day_kwh"] == 9.0 and d17["night_kwh"] == 1.5 and d17["kwh"] == 10.5
    assert d17["day_cost"] == 38.88 and d17["night_cost"] == 3.24 and d17["cost"] == 42.12 and d17["saved"] == 3.24
    assert d17["load_kwh"] == 7.0 and d17["pv_kwh"] is None
    t = out["total"]
    assert t["days"] == 2 and t["kwh"] == 14.5 and t["cost"] == pytest.approx(42.12 + 4.32 + 6.48, abs=0.01)
    assert t["avg_kwh"] == 7.25
    mixed = shaping.cost_days(energy, {**zones, date(2026, 10, 18): z(1.0, 3.0, 5.0, 2.5)})   # a later price does not touch the 17th
    assert mixed["days"][1]["cost"] == 42.12 and mixed["days"][2]["cost"] == 12.5


def test_month_helpers():
    assert shaping.parse_month("2026-10") == (2026, 10)
    for bad in ("2026-13", "2026-1", "26-10", "2026/10", "abcd-ef", "", "2026-10-01", "1999-12"):
        assert shaping.parse_month(bad) is None
    ds = shaping.month_dates(2026, 10, date(2026, 10, 18))
    assert ds[0] == date(2026, 10, 1) and ds[-1] == date(2026, 10, 18) and len(ds) == 18      # only up to today
    assert len(shaping.month_dates(2026, 2, date(2026, 10, 18))) == 28
    start, stop = shaping.local_range(date(2026, 10, 1), date(2026, 10, 31), KYIV)
    assert start == kyiv(2026, 10, 1, 0) and stop == datetime(2026, 11, 1, tzinfo=timezone.utc) - timedelta(hours=2)   # Kyiv is UTC+2 from Oct 25


def test_cost_history_endpoint():
    c, repo = hist_client(tariff_day=4.32, tariff_night=2.16)
    body = c.get("/api/cost/history").json()                    # current month = October 2026
    assert body["configured"] and body["month"] == body["current_month"] == "2026-10" and body["first_day"] == "2026-10-17"
    fields, start, every, stop = repo.calls[0]
    assert every == "15m" and start == kyiv(2026, 10, 1, 0) and "grid_import_today" in fields and "load_consumption_today" in fields
    assert stop == kyiv(2026, 10, 19, 0)                         # end of today (the 18th)
    days = {d["date"]: d for d in body["days"]}
    assert len(body["days"]) == 18 and days["2026-10-05"] == {"date": "2026-10-05", "has_data": False}
    assert days["2026-10-17"]["day_kwh"] == 9.0 and days["2026-10-17"]["night_kwh"] == 1.0   # 22:00 is day, 23:00 is night
    assert days["2026-10-18"]["load_kwh"] == 5.5
    assert body["total"]["days"] == 2 and body["tariff"]["night"] == 2.16
    c.get("/api/cost/history")
    assert len(repo.calls) == 1                                  # cached


def test_cost_history_past_month_and_validation():
    c, repo = hist_client(tariff_day=4.32, tariff_night=2.16)
    body = c.get("/api/cost/history?month=2026-09").json()
    assert body["month"] == "2026-09" and body["current_month"] == "2026-10" and len(body["days"]) == 30 and body["total"]["days"] == 0
    _, start, _, stop = repo.calls[0]
    assert start == kyiv(2026, 9, 1, 0) and stop == kyiv(2026, 10, 1, 0)
    for bad in ("2026-11", "2027-01", "garbage", "2026-00", "2026-10-05"):
        assert c.get(f"/api/cost/history?month={bad}").status_code == 400, bad
    assert len(repo.calls) == 1                                  # rejected requests never reach InfluxDB


def test_cost_history_not_configured_and_no_data():
    c, repo = hist_client(tariff_day=None)
    assert c.get("/api/cost/history").json()["configured"] is False and repo.calls == []


def test_window_max_flux_with_and_without_stop():
    from app.influx import first_time_flux, window_max_flux
    a, b = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert "stop" not in window_max_flux("solis", ["grid_import_today"], a, "15m")
    q = window_max_flux("solis", ["grid_import_today"], a, "15m", b)
    assert "range(start: 2026-10-01T00:00:00Z, stop: 2026-11-01T00:00:00Z)" in q
    assert "first()" in first_time_flux("solis") and "start: 0" in first_time_flux("solis")


def test_cost_now_is_not_cached_with_the_rest():
    clock = {"t": datetime(2026, 10, 18, 19, 58, tzinfo=timezone.utc)}              # 22:58 in Kyiv
    app = create_app(repo=Repo(), control=Ctl(), settings=Settings(static_dir="/nonexistent", tz=KYIV, tariff_day=4.32, tariff_night=2.16),
                     now=lambda: clock["t"])
    c = TestClient(app)
    assert c.get("/api/cost").json()["now"]["zone"] == "day"
    clock["t"] = datetime(2026, 10, 18, 20, 1, tzinfo=timezone.utc)                  # 23:01: three minutes later, same cached body
    assert c.get("/api/cost").json()["now"] == {"zone": "night", "price": 2.16, "until": "07:00"}


def test_cost_single_rate_has_no_zone():
    c, _ = client(tariff_day=4.32)
    assert c.get("/api/cost").json()["now"] is None
