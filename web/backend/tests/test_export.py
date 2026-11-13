from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)      # 18:00 in Kyiv
KYIV = "Europe/Kyiv"


class Repo:
    def __init__(self):
        self.calls = []

    def latest(self):
        return NOW, {"battery_soc": 70.0}

    def history_between(self, fields, start, stop, every, agg):
        self.calls.append(("between", start, stop, every, agg))
        step = {"10s": 10, "1m": 60, "15m": 900, "1h": 3600}[every]
        times, t = [], start
        while t < stop and len(times) < 5:
            times.append(t); t += timedelta(seconds=step)
        return times, {f: [1.5 if i % 2 == 0 else None for i in range(len(times))] for f in fields}

    def window_max(self, fields, start, every, stop=None):
        self.calls.append(("max", start, every, stop))
        day = start.astimezone(timezone.utc)
        return [(day + timedelta(hours=6), f, 3.25) for f in fields] + [(day + timedelta(hours=12), f, 7.5) for f in fields]

    def first_time(self):
        return NOW - timedelta(days=40)

    def control_log(self, limit):
        return []

    def events(self, days):
        t0 = NOW - timedelta(hours=5)
        return [{"time": t0, "kind": "outage", "key": "grid", "state": "start", "name": "Grid outage", "severity": "warning", "soc": 88.0, "load": 700.0},
                {"time": t0 + timedelta(hours=2), "kind": "outage", "key": "grid", "state": "end", "name": "Grid outage", "severity": "warning", "soc": 61.0, "load": None},
                {"time": NOW - timedelta(hours=1), "kind": "alarm", "key": "f2b1", "state": "start", "name": "Backup overload", "severity": "fault", "soc": 60.0, "load": 2500.0}]


def client(**kw):
    repo = Repo()
    app = create_app(repo=repo, control=object(), settings=Settings(static_dir="/nonexistent", tz=KYIV, site_name="Home", **kw), now=lambda: NOW)
    return TestClient(app), repo


def test_readings_over_a_range_as_csv():
    c, repo = client()
    r = c.get("/api/export.csv?fields=grid_power,battery_soc&range=1h&every=1m")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="solis-Home-1h-1m.csv"'
    lines = r.text.split("\r\n")
    assert lines[0] == "time,unix,grid_power,battery_soc"
    assert lines[1] == "2026-11-12T17:00:00+02:00,1794495600,1.5,1.5"        # local time with the zone, Unix seconds
    assert lines[2].endswith(",,")                                           # a gap stays empty
    assert repo.calls[0][3:] == ("1m", "mean")


def test_excel_variant_uses_semicolons_and_the_decimal_comma():
    c, _ = client()
    r = c.get("/api/export.csv?fields=grid_power&range=1h&every=15m&sep=%3B")
    assert r.text.split("\r\n")[1] == "2026-11-12T17:00:00+02:00;1794495600;1,5"


def test_days_are_local_and_one_row_per_day_at_1d():
    c, repo = client()
    r = c.get("/api/export.csv?fields=grid_import_today,grid_power&from=2026-11-10&to=2026-11-11&every=1d&sep=,")
    assert r.status_code == 200 and 'filename="solis-Home-2026-11-10_2026-11-11-1d.csv"' in r.headers["content-disposition"]
    lines = r.text.split("\r\n")
    assert lines[0] == "date,grid_import_today,grid_power"
    assert lines[1].startswith("2026-11-10,7.5,") and lines[2].startswith("2026-11-11,")     # the counter's daily maximum
    kinds = [k[0] for k in repo.calls]
    assert kinds == ["max", "between"]                                       # counters by window maxima, the rest by hourly means
    assert repo.calls[0][1] == datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)   # local midnight in Kyiv (UTC+2 in November)


def test_every_column_may_be_exported_at_once():
    c, _ = client()
    from app.validation import FIELDS
    r = c.get(f"/api/export.csv?fields={','.join(FIELDS)}&range=1h&every=15m")      # 50+ fields: the JSON API caps at 12, a CSV does not
    assert r.status_code == 200 and r.text.split("\r\n")[0] == "time,unix," + ",".join(FIELDS)


def test_limits_and_bad_parameters():
    c, _ = client()
    assert c.get("/api/export.csv?fields=grid_power&range=7d&every=10s").status_code == 400     # raw only up to a day
    assert c.get("/api/export.csv?fields=grid_power&range=30d&every=1m").status_code == 400     # a minute only up to a week
    assert c.get("/api/export.csv?fields=grid_power&range=6h&every=2m").status_code == 400
    assert c.get("/api/export.csv?fields=grid_power&range=6h&sep=|").status_code == 400
    assert c.get("/api/export.csv?fields=nope&range=6h").status_code == 400
    assert c.get("/api/export.csv?fields=grid_power&from=2026-11-13&to=2026-11-13").status_code == 400   # tomorrow
    assert c.get("/api/export.csv?fields=grid_power&from=2026-11-12&to=2026-11-10").status_code == 400
    assert c.get("/api/export.csv?fields=grid_power&from=2026-13-01").status_code == 400


def test_events_csv_for_a_range_and_a_day():
    c, _ = client()
    r = c.get("/api/export/events.csv?days=7")
    assert r.status_code == 200 and 'filename="solis-Home-events-last-7-days.csv"' in r.headers["content-disposition"]
    lines = r.text.split("\r\n")
    assert lines[0] == "kind,name,severity,start,end,ongoing,duration_s,soc_start,soc_end,load_start"
    assert lines[1] == "alarm,Backup overload,fault,2026-11-12T17:00:00+02:00,,yes,3600,60,,2500"
    assert lines[2] == "outage,Grid outage,warning,2026-11-12T13:00:00+02:00,2026-11-12T15:00:00+02:00,no,7200,88,61,700"
    r = c.get("/api/export/events.csv?date=2026-11-12&sep=%3B")
    assert r.status_code == 200 and "outage;Grid outage" in r.text
    assert c.get("/api/export/events.csv?date=2026-11-13").status_code == 400
    assert c.get("/api/export/events.csv?days=0").status_code == 400


def test_cost_csv_uses_the_cost_history_days():
    c, _ = client(tariff_day=4.32, tariff_night=2.16, tariff_currency="UAH")
    r = c.get("/api/export/cost.csv?month=2026-11")
    assert r.status_code == 200 and 'filename="solis-Home-cost-2026-11.csv"' in r.headers["content-disposition"]
    lines = r.text.split("\r\n")
    assert lines[0] == "date,kwh,day_kwh,night_kwh,cost,day_cost,night_cost,saved,load_kwh,export_kwh,pv_kwh,charge_kwh,discharge_kwh,currency"
    assert lines[1].startswith("2026-11-") and lines[1].endswith(",UAH")
    c2, _ = client()
    assert c2.get("/api/export/cost.csv").status_code == 400                 # no tariff


def test_raw_10_second_export_only_within_the_kept_readings():
    repo = Repo()
    c = TestClient(create_app(repo=repo, control=object(), now=lambda: NOW, settings=Settings(static_dir="/nonexistent", tz=KYIV, raw_retention_days=40)))
    r = c.get("/api/export.csv?fields=grid_power&from=2026-09-01&to=2026-09-01&every=10s")
    assert r.status_code == 400 and "kept for the last 40 days" in r.json()["detail"]
    assert c.get("/api/export.csv?fields=grid_power&from=2026-09-01&to=2026-09-01&every=1m").status_code == 200   # the aggregates answer
    assert c.get("/api/export.csv?fields=grid_power&from=2026-11-11&to=2026-11-11&every=10s").status_code == 200


def test_site_tells_the_page_how_long_raw_readings_are_kept():
    c, _ = client()
    assert c.get("/api/site").json()["raw_retention_days"] == 90
