import io
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import forecast
from forecast import Forecaster, ForecastStore, OpenMeteo, clean_config, correction, model, panel_watts, parse_hourly, to_open_meteo
from outages import verdict

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 18, 13, 30, tzinfo=KYIV)              # 13:30 Kyiv = 11:30 UTC
CFG = {"lat": 50.4512, "lon": 30.5234, "arrays": [{"kwp": 4.2, "tilt": 35, "azimuth": 180}], "losses": 14,
       "correct": True, "telegram": True, "below_pct": 50, "overview": True}


def day_curve(peak: float) -> dict[int, float]:
    """A local hour -> W/m² bell from 08:00 to 16:00."""
    return {h: max(0.0, peak * (1 - abs(h - 12) / 4)) for h in range(24)}


def answer(days_back=14, days_ahead=3, peak=600.0, cloud=40):
    """Open-Meteo's shape: time = end of each hour (UTC), irradiance the mean of the hour before it."""
    start = datetime(2026, 11, 18, tzinfo=KYIV) - timedelta(days=days_back)
    times, gti, temp, cl = [], [], [], []
    for i in range((days_back + days_ahead) * 24):
        hour_start = start + timedelta(hours=i)
        times.append(int((hour_start + timedelta(hours=1)).timestamp()))
        gti.append(day_curve(peak)[hour_start.hour])
        temp.append(10.0)
        cl.append(cloud)
    return {"hourly": {"time": times, "global_tilted_irradiance": gti, "temperature_2m": temp, "cloud_cover": cl}}


class FakeMeteo:
    def __init__(self, data=None):
        self.data, self.calls = data or answer(), []

    def hourly(self, lat, lon, tilt, compass):
        self.calls.append((lat, lon, tilt, compass))
        if isinstance(self.data, Exception):
            raise self.data
        return parse_hourly(json.loads(json.dumps(self.data)))


def actual_like(f: Forecaster, ratio: float, soc: float = 60) -> dict:
    """What the panels made: the model's own past hours times a ratio."""
    return {t: (w * ratio, soc) for t, w in f.store.data["model"].items() if t < f._hour()}


def make(tmp_path, cfg=CFG, **kw):
    store = ForecastStore(str(tmp_path))
    store.set_config(cfg)
    clock = [NOW.timestamp()]
    sent = []
    f = Forecaster(store, kw.pop("client", FakeMeteo()), "Europe/Kyiv", send=sent.append, clock=lambda: clock[0], **kw)
    return f, sent, clock


def test_compass_to_open_meteo():
    assert [to_open_meteo(a) for a in (180, 90, 270, 0, 135, 359)] == [0, -90, 90, 180, -45, 179]


def test_open_meteo_request_and_hour_shift():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        return io.BytesIO(json.dumps({"hourly": {"time": [7200, 10800], "global_tilted_irradiance": [0, 250.5],
                                                 "temperature_2m": [5, 6], "cloud_cover": [100, 20]}}).encode())
    hours = OpenMeteo(base="https://x/v1/forecast", opener=opener).hourly(50.4512, 30.5234, 35, 180)
    assert "latitude=50.45&longitude=30.52&tilt=35&azimuth=0" in seen["url"] and "past_days=14" in seen["url"]
    assert hours == {3600: (0.0, 5, 100), 7200: (250.5, 6, 20)}                 # the hour before each time
    with pytest.raises(forecast.ForecastError):
        parse_hourly({"nope": 1})


def test_panel_watts_and_two_groups():
    assert panel_watts(0, 10, 4, 14) == 0
    assert panel_watts(1000, 25 - 30, 1, 0) == pytest.approx(1000)               # cell at exactly 25 °C
    assert panel_watts(1000, 35, 1, 0) < panel_watts(1000, 5, 1, 0)              # warm panels give less
    east, west = {0: (400.0, 15.0, 50)}, {0: (100.0, 15.0, 60)}
    w, cloud = model([east, west], [{"kwp": 2, "tilt": 30, "azimuth": 90}, {"kwp": 2, "tilt": 30, "azimuth": 270}], 10)
    assert w[0] == pytest.approx(panel_watts(400, 15, 2, 10) + panel_watts(100, 15, 2, 10), abs=0.1) and cloud == {0: 50}


def test_config_is_checked_and_rounded():
    c = clean_config(CFG)
    assert c["lat"] == 50.45 and c["lon"] == 30.52 and c["arrays"] == [{"kwp": 4.2, "tilt": 35, "azimuth": 180}]
    assert clean_config({**CFG, "lat": None})["lat"] is None
    for bad in ({**CFG, "lat": 91}, {**CFG, "arrays": []}, {**CFG, "arrays": [{"kwp": 0, "tilt": 30, "azimuth": 180}]},
                {**CFG, "arrays": [{"kwp": 1, "tilt": 30}]}, {**CFG, "correct": "yes"}, {**CFG, "below_pct": 5}, {**CFG, "arrays": [CFG["arrays"][0]] * 4}):
        with pytest.raises(ValueError):
            clean_config(bad)


def test_correction_skips_dark_and_full_battery_hours():
    m = {t * 3600: 1000.0 for t in range(30)}
    actual = {t * 3600: (800.0, 50) for t in range(25)} | {t * 3600: (100.0, 99) for t in range(25, 30)}
    k, n = correction(m, actual, before=30 * 3600, peak_w=4000)
    assert k == pytest.approx(0.8) and n == 25
    assert correction(m, dict(list(actual.items())[:10]), 30 * 3600, 4000) == (None, 10)        # too few hours
    k, _ = correction(m, {t: (5000.0, 50) for t in m}, 30 * 3600, 4000)
    assert k == forecast.FACTOR_MAX


def test_refresh_payload_and_correction(tmp_path):
    f, _, _ = make(tmp_path)
    assert f.payload()["days"] == []                                             # nothing fetched yet
    assert f.refresh()
    f.actual = lambda: actual_like(f, 0.5)
    p = f.payload()
    assert p["configured"] and p["kwp"] == 4.2 and p["factor"] == pytest.approx(0.5) and p["factor_hours"] >= 20
    assert [d["date"] for d in p["days"]] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    raw = sum(panel_watts(day_curve(600)[h], 10, 4.2, 14) for h in range(24)) / 1000
    assert p["days"][1]["kwh"] == pytest.approx(raw * 0.5, abs=0.1) and p["days"][1]["cloud_pct"] == 40
    rest = sum(panel_watts(day_curve(600)[h], 10, 4.2, 14) for h in range(14, 24)) + panel_watts(day_curve(600)[13], 10, 4.2, 14) / 2
    assert p["days"][0]["rest_kwh"] == pytest.approx(rest / 1000 * 0.5, abs=0.1)
    assert p["usual_pv_kwh"] == pytest.approx(raw * 0.5, abs=0.1)
    f.store.config["correct"] = False
    assert f.payload()["days"][1]["kwh"] == pytest.approx(raw, abs=0.1)         # the measured factor is still shown, not used
    assert f.payload()["factor"] == pytest.approx(0.5)


def test_past_hours_keep_what_was_forecast(tmp_path):
    f, _, clock = make(tmp_path)
    f.refresh()
    noon = int(NOW.replace(hour=12, minute=0).timestamp())
    two = noon + 2 * 3600
    before = f.watts(two)
    clock[0] = NOW.replace(hour=16).timestamp()
    f.client = FakeMeteo(answer(peak=100))                                      # a gloomier model later on
    f.refresh()
    assert f.watts(two) == pytest.approx(before)                                # 14:00 was frozen when it began
    assert f.watts(two + 3 * 3600) == 0 or f.watts(two + 24 * 3600) < before   # future hours follow the new model
    hours = f.hours()["hours"]
    assert hours == sorted(hours) and [two, before] in hours
    again = Forecaster(ForecastStore(str(tmp_path)), FakeMeteo(), "Europe/Kyiv", clock=lambda: clock[0])
    assert again.watts(two) == pytest.approx(before)                            # kept across a restart


def test_evening_note_and_telegram_once(tmp_path):
    f, sent, clock = make(tmp_path, cfg={**CFG, "correct": False})
    f.refresh()
    usual = actual_like(f, 1.0)                                                 # the last two weeks were like the first model
    f.actual = lambda: usual
    f.load = lambda: {h: 450 for h in range(24)}
    f.evening()
    assert sent == [] and f.store.data["evening"] == {}                        # 13:30: not evening yet
    clock[0] = NOW.replace(hour=20, minute=5).timestamp()
    f.client = FakeMeteo(answer(peak=150))                                      # tomorrow is gloomy: a quarter of usual
    f.refresh()
    f.evening(); f.evening()
    assert f.payload()["load_by_hour"] == [450] * 24
    assert len(sent) == 1 and sent[0].startswith("☁️ Tomorrow's solar forecast: ≈") and "The house usually uses ≈ 10.8 kWh" in sent[0]
    assert "2026-11-19" in f.store.data["evening"]
    f.store.data["telegram_for"] = None
    f.store.config["telegram"] = False
    f.evening()
    assert len(sent) == 1


def test_recent_days_compare_the_evening_forecast_with_what_was_made(tmp_path):
    f, _, _ = make(tmp_path)
    f.refresh()
    f.actual = lambda: actual_like(f, 1.0)
    made = f._made(NOW.date() - timedelta(days=1))
    for i in range(1, 6):
        f.store.data["evening"][(NOW.date() - timedelta(days=i)).isoformat()] = round(made * 1.1, 1)
    p = f.payload()
    assert len(p["recent"]) == 5 and p["recent"][-1] == {"date": "2026-11-17", "forecast_kwh": round(made * 1.1, 1), "made_kwh": made}
    assert p["miss_pct"] == pytest.approx(10, abs=1)


def test_a_new_place_forgets_what_was_fetched(tmp_path):
    f, _, _ = make(tmp_path)
    f.refresh()
    _, changed = f.store.set_config({**CFG, "telegram": False})
    assert not changed and f.store.data["model"]
    _, changed = f.store.set_config({**CFG, "arrays": [{"kwp": 6, "tilt": 30, "azimuth": 180}]})
    assert changed and not f.store.data["model"] and not f.store.data["frozen"]
    f.store.set_config({**CFG, "lat": None})
    assert not f.store.configured() and f.payload() == {**f.payload(), "configured": False, "days": []}


def test_a_failed_fetch_keeps_the_last_good_one(tmp_path):
    f, _, _ = make(tmp_path)
    f.refresh()
    f.client = FakeMeteo(forecast.ForecastError("Open-Meteo not reachable (URLError)"))
    assert not f.refresh()
    p = f.payload()
    assert p["stale"] and p["error"].startswith("Open-Meteo not reachable") and p["days"]


def test_the_outage_verdict_counts_the_sun():
    now = datetime(2026, 11, 18, 8, 0, tzinfo=KYIV)
    a, b = now.replace(hour=10), now.replace(hour=14)
    profile = {h: 1000 for h in range(24)}
    dark = verdict(a, b, now, soc=40, floor=20, capacity_kwh=10, profile=profile, load_now=1000, battery_w=0)
    assert not dark["enough"] and dark["pv_kwh"] is None
    sunny = verdict(a, b, now, soc=40, floor=20, capacity_kwh=10, profile=profile, load_now=1000, battery_w=0, pv=lambda at: 600)
    assert sunny["enough"] and sunny["pv_kwh"] == 2.4
    daylight = lambda at: 3000 if 9 <= at.hour < 15 else 0
    full = verdict(a, b, now, soc=100, floor=20, capacity_kwh=10, profile=profile, load_now=1000, battery_w=0, pv=daylight)
    assert full["enough"] and full["soc_at_start"] == 100
    assert full["lasts_s"] == 13 * 3600                                        # full until 15:00 (no banking above 100%), then 8 kWh at 1 kW


def test_the_watcher_takes_the_forecast_when_there_is_one(tmp_path):
    import outages
    store = outages.OutageStore(str(tmp_path))
    w = outages.OutageWatcher(store, None, "Europe/Kyiv", state=lambda: (40, 0, 1000), battery=lambda: (10, 20, 100),
                              profile=lambda: {h: 1000 for h in range(24)}, pv=lambda: (lambda at: 600))
    now = datetime(2026, 11, 18, 8, 0, tzinfo=KYIV)
    v = w._verdict(now.replace(hour=10), now.replace(hour=14), now)
    assert v["enough"] and v["pv_kwh"] == 2.4
    w.pv = lambda: None
    assert not w._verdict(now.replace(hour=10), now.replace(hour=14), now)["enough"]


def test_actual_rows_join_pv_and_soc():
    t0 = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    rows = forecast.actual_rows([(t0, 1200.0), (t0 + timedelta(hours=1), None)], [(t0, 98.0)])
    assert rows == {int(t0.timestamp()): (1200.0, 98.0)}
