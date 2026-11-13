"""One exact day: /api/day/* and the calendar's marks."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import shaping
from app.config import Settings
from app.influx import history_between_flux
from app.main import create_app

KYIV = "Europe/Kyiv"
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def ev(t, state, kind="outage", key="grid", soc=None):
    return {"time": t, "kind": kind, "key": key, "state": state, "name": "x", "severity": "warning", "soc": soc, "load": 500.0}


class Repo:
    def __init__(self, events=(), rows=(), first=utc(2026, 9, 1)):
        self._events, self._rows, self._first = list(events), list(rows), first
        self.calls = []

    def history_between(self, fields, start, stop, every, agg):
        self.calls.append(("history_between", start, stop, every, agg))
        return [start + timedelta(hours=1)], {f: [1.0] for f in fields}

    def window_max(self, fields, start, every, stop=None):
        self.calls.append(("window_max", start, stop))
        return self._rows

    def events(self, days):
        self.calls.append(("events", days))
        return self._events

    def first_time(self):
        return self._first


def client(repo):
    return TestClient(create_app(repo=repo, settings=Settings(static_dir="/nonexistent", tz=KYIV), now=lambda: NOW))


# ---------------------------------------------------------------- validating the day

@pytest.mark.parametrize("raw", ["2026-11-02", "2026-13-01", "2026-02-30", "26-10-24", "2026-1-5", "2026-10-24T00:00",
                                 '2026-10-24") |> drop(', "today", ""])
@pytest.mark.parametrize("path", ["/api/day/energy?date=", "/api/day/events?date=", "/api/day/history?fields=pv_power&date="])
def test_bad_or_future_day_is_rejected(path, raw):
    repo = Repo()
    assert client(repo).get(path + raw).status_code == 400
    assert repo.calls == []


def test_day_history_whitelists_the_rest():
    c = client(Repo())
    assert c.get("/api/day/history?date=2026-10-24&fields=nope").status_code == 400
    assert c.get("/api/day/history?date=2026-10-24&fields=pv_power&agg=sum").status_code == 400
    assert c.get("/api/day/history?date=2026-10-24&fields=pv_power&every=7m").status_code == 400


# ---------------------------------------------------------------- history

def test_day_history_covers_local_midnight_to_midnight():
    repo = Repo()
    b = client(repo).get("/api/day/history?date=2026-10-24&fields=pv_power").json()
    start, stop = utc(2026, 10, 23, 21), utc(2026, 10, 24, 21)          # Kyiv is UTC+3 on that day
    assert repo.calls == [("history_between", start, stop, "5m", "mean")]
    assert (b["start"], b["stop"]) == (start.timestamp(), stop.timestamp())
    assert b["date"] == "2026-10-24" and b["is_today"] is False and b["series"]["pv_power"] == [1.0]


def test_day_history_on_a_25_hour_day():
    client_repo = Repo()
    b = client(client_repo).get("/api/day/history?date=2026-10-25&fields=pv_power").json()   # clocks go back
    assert b["stop"] - b["start"] == 25 * 3600


def test_today_stops_at_now_and_says_so():
    repo = Repo()
    b = client(repo).get("/api/day/history?date=2026-11-01&fields=pv_power").json()
    assert repo.calls[0][2] == NOW
    assert b["is_today"] is True and b["stop"] > NOW.timestamp()          # the axis still runs to midnight


def test_history_between_flux_uses_only_canonical_values():
    q = history_between_flux("solis", ["pv_power"], utc(2026, 10, 23, 21), utc(2026, 10, 24, 21), "5m", "mean")
    assert "range(start: 2026-10-23T21:00:00Z, stop: 2026-10-24T21:00:00Z)" in q
    with pytest.raises(AssertionError):
        history_between_flux("solis", ['x" or true'], NOW, NOW, "5m", "mean")
    with pytest.raises(AssertionError):
        history_between_flux("solis", ["pv_power"], NOW, NOW, "1m) |> drop(", "mean")


# ---------------------------------------------------------------- energy

def test_day_energy_counts_what_the_counters_added_that_day():
    rows = [(utc(2026, 10, 24, 8), "pv_energy_today", 5.0), (utc(2026, 10, 24, 15), "pv_energy_today", 17.6),
            (utc(2026, 10, 24, 15), "grid_import_today", 2.5)]
    repo = Repo(rows=rows)
    b = client(repo).get("/api/day/energy?date=2026-10-24").json()
    assert b["date"] == "2026-10-24" and b["pv_energy_today"] == 17.6 and b["grid_import_today"] == 2.5
    assert b["load_consumption_today"] is None
    assert repo.calls == [("window_max", utc(2026, 10, 23, 20), utc(2026, 10, 24, 21))]     # from an hour before: the baseline


# ---------------------------------------------------------------- events

def test_day_events_keeps_what_overlaps_that_local_day():
    rows = [
        ev(utc(2026, 10, 23, 20), "start"), ev(utc(2026, 10, 23, 20, 30), "end"),    # 23:00 on the 23rd local: before
        ev(utc(2026, 10, 23, 22), "start"), ev(utc(2026, 10, 24, 1), "end"),         # 01:00-04:00 local: inside
        ev(utc(2026, 10, 24, 20), "start"), ev(utc(2026, 10, 24, 22), "end"),        # runs past local midnight: still on the day
        ev(utc(2026, 10, 24, 21), "start", "alarm", "f1"), ev(utc(2026, 10, 24, 21, 5), "end", "alarm", "f1"),   # just after: no
    ]
    b = client(Repo(rows)).get("/api/day/events?date=2026-10-24").json()
    assert [o["start"][:16] for o in b["outages"]] == ["2026-10-24T20:00", "2026-10-23T22:00"]
    assert b["alarms"] == []


def test_day_events_finds_an_outage_that_began_days_earlier_and_reads_up_to_now():
    repo = Repo([ev(utc(2026, 10, 22, 12), "start"), ev(utc(2026, 10, 26, 12), "end")])
    b = client(repo).get("/api/day/events?date=2026-10-24").json()
    assert len(b["outages"]) == 1 and not b["outages"][0]["ongoing"]      # it ended after the day: not "ongoing"
    assert repo.calls == [("events", (NOW - utc(2026, 10, 23, 21)).days + 31)]


def test_day_events_still_reports_the_present():
    rows = [ev(NOW - timedelta(hours=1), "start"), ev(NOW - timedelta(hours=1), "start", "alarm", "f1")]
    b = client(Repo(rows)).get("/api/day/events?date=2026-10-24").json()
    assert b["outages"] == [] and b["alarms"] == []
    assert b["outage_now"]["ongoing"] and [a["key"] for a in b["active"]] == ["f1"]


# ---------------------------------------------------------------- calendar

def test_calendar_marks_every_day_an_event_touches():
    rows = [
        ev(utc(2026, 10, 3, 12), "start"), ev(utc(2026, 10, 5, 12), "end"),
        ev(utc(2026, 10, 3, 13), "start", "alarm", "f1"), ev(utc(2026, 10, 3, 14), "end", "alarm", "f1"),
        ev(utc(2026, 10, 31, 22), "start"), ev(utc(2026, 10, 31, 23), "end"),        # 1 November local: not October
    ]
    b = client(Repo(rows)).get("/api/events/calendar?month=2026-10").json()
    assert b["days"] == {"2026-10-03": {"outages": 1, "alarms": 1}, "2026-10-04": {"outages": 1, "alarms": 0},
                         "2026-10-05": {"outages": 1, "alarms": 0}}
    assert b["month"] == "2026-10" and b["today"] == "2026-11-01" and b["first_day"] == "2026-09-01"


def test_calendar_defaults_to_this_month_and_marks_an_ongoing_outage_up_to_today():
    b = client(Repo([ev(NOW - timedelta(hours=2), "start")])).get("/api/events/calendar").json()
    assert b["month"] == "2026-11" and b["days"] == {"2026-11-01": {"outages": 1, "alarms": 0}}


@pytest.mark.parametrize("month", ["2026-12", "2026-13", "nope"])
def test_calendar_rejects_bad_months(month):
    assert client(Repo()).get(f"/api/events/calendar?month={month}").status_code == 400


def test_calendar_with_no_readings_yet():
    assert client(Repo(first=None)).get("/api/events/calendar").json()["first_day"] is None


def test_parse_day_is_strict():
    assert shaping.parse_day("2026-10-24") == date(2026, 10, 24)
    assert shaping.parse_day("2026-02-30") is None and shaping.parse_day("1999-01-01") is None
