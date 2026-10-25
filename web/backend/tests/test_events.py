from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import shaping, validation
from app.config import Settings
from app.main import create_app, valid_setting

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def ev(minutes_ago, kind, key, state, soc=None, name=None):
    return {"time": NOW - timedelta(minutes=minutes_ago), "kind": kind, "key": key, "state": state,
            "name": name or ("Grid outage" if kind == "outage" else key), "severity": "warning", "soc": soc, "load": 600.0}


def test_pair_events():
    rows = [
        ev(300, "outage", "grid", "start", soc=95),
        ev(240, "outage", "grid", "end", soc=70),          # 60 min outage
        ev(200, "alarm", "f2b1", "start", name="Backup overload"),
        ev(199, "alarm", "f2b1", "start", name="Backup overload"),   # poller restarted: duplicate start ignored
        ev(190, "alarm", "f2b1", "end"),
        ev(100, "alarm", "f9", "end"),                     # began before the range
        ev(30, "outage", "grid", "start", soc=88),          # ongoing
        ev(20, "alarm", "op13", "start", name="Fan fault"),
    ]
    r = shaping.pair_events(rows, NOW)
    first, now_out = r["outages"][1], r["outages"][0]
    assert first["duration_s"] == 3600 and first["soc_start"] == 95 and first["soc_end"] == 70 and not first["ongoing"]
    assert now_out["ongoing"] and now_out["duration_s"] == 1800 and now_out["end"] is None
    assert r["outage_now"] == now_out
    assert [a["key"] for a in r["active"]] == ["op13"]
    over = next(a for a in r["alarms"] if a["key"] == "f2b1")
    assert over["duration_s"] == 600
    orphan = next(a for a in r["alarms"] if a["key"] == "f9")
    assert orphan["start"] is None and orphan["duration_s"] is None


def test_pair_events_empty():
    assert shaping.pair_events([], NOW) == {"outages": [], "alarms": [], "active": [], "outage_now": None}


def test_period_sums():
    days = [{"date": "2025-12-31", "grid_import_today": 50.0},
            {"date": "2026-09-30", "grid_import_today": 10.0},
            {"date": "2026-10-01", "grid_import_today": 7.5},
            {"date": "2026-10-02", "grid_import_today": None},
            {"date": "2026-10-18", "grid_import_today": 2.25}]
    r = shaping.period_sums(days, ("grid_import_today",), date(2026, 10, 18))
    assert r["month"] == {"grid_import_today": 9.8}
    assert r["year"] == {"grid_import_today": 19.8}
    assert r["first_day"] == "2025-12-31"


class Repo:
    def __init__(self):
        self.calls = []

    def events(self, days):
        self.calls.append(days)
        return [ev(10, "outage", "grid", "start", soc=90)]

    def window_max(self, fields, start, every):
        self.calls.append(("wm", start, every))
        return [(datetime(2026, 10, 18, 6, tzinfo=timezone.utc), "grid_import_today", 4.0),
                (datetime(2026, 10, 1, 6, tzinfo=timezone.utc), "grid_import_today", 6.0)]


def client(repo):
    return TestClient(create_app(repo=repo, settings=Settings(static_dir="/nonexistent", tz="Europe/Kyiv"),
                                 now=lambda: NOW))


def test_events_endpoint():
    repo = Repo()
    body = client(repo).get("/api/events?days=7").json()
    assert body["days"] == 7 and body["outage_now"]["ongoing"] is True and repo.calls == [7]
    assert client(repo).get("/api/events?days=0").status_code == 400


def test_energy_summary_endpoint_and_cache():
    repo = Repo()
    c = client(repo)
    body = c.get("/api/energy/summary").json()
    assert body["month"]["grid_import_today"] == 10.0 and body["year"]["grid_import_today"] == 10.0
    _, start, every = repo.calls[0]
    assert every == "1h" and start == datetime(2025, 12, 31, 22, tzinfo=timezone.utc)   # Jan 1 00:00 Kyiv
    c.get("/api/energy/summary")
    assert len(repo.calls) == 1                         # cached for 5 minutes


TOU = {"kind": "charge", "slot": 1, "enabled": True, "start": "23:00", "end": "07:00", "current_a": 30, "soc": 100}


@pytest.mark.parametrize("setting,value,ok", [
    ("reserve_mode", True, True), ("reserve_mode", 1, False),
    ("time_of_use_mode", False, True),
    ("backup_soc", 40, True), ("backup_soc", 40.0, True), ("backup_soc", 40.5, False), ("backup_soc", True, False),
    ("overdischarge_soc", "30", False), ("max_charge_soc", 90, True), ("force_charge_soc", 10.5, False),
    ("tou_slot", TOU, True), ("tou_slot", {**TOU, "extra": 1}, False), ("tou_slot", [1], False),
    ("clock_sync", True, True), ("clock_sync", False, False),
    ("off_grid", True, False),
])
def test_valid_setting(setting, value, ok):
    assert valid_setting(setting, value) is ok
