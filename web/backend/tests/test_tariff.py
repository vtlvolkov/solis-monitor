import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.tariff import Tariff, TariffError, TariffStore, next_boundary, validate

KYIV = "Europe/Kyiv"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)            # 15:00 in Kyiv
OLD = Tariff(day=4.32, night=2.16, night_start="23:00", night_end="07:00")
NEW = Tariff(day=6.0, night=3.0, night_start="22:00", night_end="06:00")


def kyiv(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) - timedelta(hours=3)


# ---------------------------------------------------------------- the store

def test_next_boundary_is_the_next_quarter_hour_strictly_after():
    assert next_boundary(datetime(2026, 10, 18, 12, 7, 31, tzinfo=timezone.utc)) == datetime(2026, 10, 18, 12, 15, tzinfo=timezone.utc)
    assert next_boundary(datetime(2026, 10, 18, 12, 15, tzinfo=timezone.utc)) == datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
    assert next_boundary(datetime(2026, 10, 18, 23, 59, 59, tzinfo=timezone.utc)) == datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)


def test_a_change_applies_from_the_next_quarter_hour_and_leaves_the_past_alone():
    store = TariffStore(OLD)
    assert store.source == "env" and store.at(NOW) == OLD
    eff = store.set(NEW, NOW)
    assert eff == datetime(2026, 10, 18, 12, 15, tzinfo=timezone.utc)
    assert store.at(eff - timedelta(seconds=1)) == OLD                # the window before is still the old price
    assert store.at(eff) == NEW and store.at(eff + timedelta(days=30)) == NEW
    assert store.at(datetime(2020, 1, 1, tzinfo=timezone.utc)) == OLD  # and so is all older history
    assert store.source == "saved"


def test_a_later_date_is_kept_pending_and_a_new_change_replaces_it():
    store = TariffStore(OLD)
    nov1 = kyiv(2026, 11, 1, 0)
    assert store.set(NEW, NOW, requested=nov1) == nov1
    assert store.at(NOW) == OLD and store.at(nov1) == NEW
    assert [s for s, _ in store.upcoming(NOW)] == [nov1]
    third = Tariff(day=7.0)
    eff = store.set(third, NOW)                                        # sooner: the pending November change is superseded
    assert eff == datetime(2026, 10, 18, 12, 15, tzinfo=timezone.utc)
    assert [tf for _, tf in store.versions] == [OLD, third]
    assert store.upcoming(NOW) == [(eff, third)]


def test_a_requested_moment_in_the_past_is_never_used():
    store = TariffStore(OLD)
    eff = store.set(NEW, NOW, requested=NOW - timedelta(days=10))
    assert eff == next_boundary(NOW)                                   # history can't be rewritten


def test_same_prices_and_far_dates_are_refused():
    store = TariffStore(OLD)
    with pytest.raises(TariffError, match="already"):
        store.set(OLD, NOW)
    with pytest.raises(TariffError, match="too far"):
        store.set(NEW, NOW, requested=NOW + timedelta(days=400))
    store.set(NEW, NOW, requested=kyiv(2026, 11, 1, 0))
    with pytest.raises(TariffError, match="already"):                  # compared with what is in force just before it would start
        store.set(NEW, NOW, requested=kyiv(2026, 12, 1, 0))


def test_the_first_ever_tariff_also_covers_the_past():
    store = TariffStore(None)
    assert store.at(NOW) is None and store.versions == []
    store.set(NEW, NOW)
    assert store.versions == [(None, NEW)]
    assert store.at(datetime(2020, 1, 1, tzinfo=timezone.utc)) == NEW   # nothing earlier to protect


def test_versions_between_two_moments():
    store = TariffStore(OLD)
    a = store.set(NEW, NOW)
    c = Tariff(day=8.0)
    b = store.set(c, a + timedelta(days=10))
    month_start, month_end = a - timedelta(days=5), a + timedelta(days=20)
    assert [tf for _, tf in store.between(month_start, month_end)] == [OLD, NEW, c]
    assert [tf for _, tf in store.between(a + timedelta(days=1), a + timedelta(days=5))] == [NEW]      # only the one in force
    assert [tf for _, tf in store.between(b, b + timedelta(days=1))] == [c]


def test_changes_survive_a_restart_and_the_file_wins_over_env(tmp_path):
    path = str(tmp_path / "sub" / "tariff.json")
    store = TariffStore(OLD, path)
    eff = store.set(NEW, NOW)
    again = TariffStore(Tariff(day=99.0), path)                        # .env changed afterwards: ignored, the file is the truth
    assert again.source == "saved"
    assert again.at(eff - timedelta(seconds=1)) == OLD and again.at(eff) == NEW
    assert json.load(open(path))["versions"][0]["from"] is None
    assert not os.path.exists(path + ".tmp")


def test_a_corrupt_file_is_set_aside_not_trusted(tmp_path):
    path = tmp_path / "tariff.json"
    path.write_text("{ this is not json")
    store = TariffStore(OLD, str(path))
    assert store.source == "env" and store.at(NOW) == OLD
    assert (tmp_path / "tariff.json.corrupt").exists() and not path.exists()


def test_a_file_with_versions_out_of_order_is_rejected(tmp_path):
    path = tmp_path / "tariff.json"
    path.write_text(json.dumps({"versions": [
        {"from": None, "day": 1.0, "night": None, "night_start": None, "night_end": None},
        {"from": "2026-11-01T00:00:00+00:00", "day": 2.0, "night": None, "night_start": None, "night_end": None},
        {"from": "2026-10-01T00:00:00+00:00", "day": 3.0, "night": None, "night_start": None, "night_end": None}]}))
    assert TariffStore(OLD, str(path)).source == "env"


@pytest.mark.parametrize("args,message", [
    ((0, None, None, None), "day price"), ((-1, None, None, None), "day price"), (("4", None, None, None), "day price"),
    ((True, None, None, None), "day price"), ((20_000, None, None, None), "day price"), ((None, None, None, None), "day price"),
    ((4.3, 0, "23:00", "07:00"), "night price"), ((4.3, "x", "23:00", "07:00"), "night price"),
    ((4.3, 2.1, "24:00", "07:00"), "HH:MM"), ((4.3, 2.1, "23:00", None), "HH:MM"), ((4.3, 2.1, "7:00", "23:00"), "HH:MM"),
    ((4.3, 2.1, "07:00", "07:00"), "differ"),
])
def test_validate_rejects_bad_input(args, message):
    with pytest.raises(TariffError, match=message):
        validate(*args)


def test_validate_normalises_prices_and_drops_hours_without_a_night_price():
    assert validate(4.3200001, 2.16, "23:00", "07:00") == Tariff(day=4.32, night=2.16, night_start="23:00", night_end="07:00")
    assert validate(4, None, "23:00", "07:00") == Tariff(day=4.0)


# ---------------------------------------------------------------- the API

class HistRepo:
    """Grid-import counter windows in Kyiv time: the 17th, and the 18th up to a window after 'now' (15:30)."""
    ROWS = [(kyiv(2026, 10, 17, 22, 0), 9.0), (kyiv(2026, 10, 17, 23, 0), 10.0), (kyiv(2026, 10, 18, 0, 0), 0.4),
            (kyiv(2026, 10, 18, 6, 45), 3.0), (kyiv(2026, 10, 18, 12, 15), 4.0), (kyiv(2026, 10, 18, 15, 30), 5.0)]

    def first_time(self):
        return kyiv(2026, 10, 17, 9)

    def window_max(self, fields, start, every, stop=None):
        return [(t, "grid_import_today", v) for t, v in self.ROWS]


class Ctl:
    def cached_settings(self):
        return 200, {}


def client(tmp_path, auth="me:pw", **kw):
    app = create_app(repo=HistRepo(), control=Ctl(), now=lambda: NOW,
                     settings=Settings(static_dir="/nonexistent", tz=KYIV, basic_auth=auth, tariff_day=4.32, tariff_night=2.16,
                                       tariff_file=str(tmp_path / "tariff.json"), **kw))
    return TestClient(app)


AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
BODY = {"day": 6.0, "night": 3.0, "night_start": "22:00", "night_end": "06:00", "from": None}


def post(c, body=BODY, headers=None):
    return c.post("/api/settings/tariff", json=body, headers={**JSON, **(headers or {})}, auth=AUTH)


def test_get_reports_the_current_tariff_and_whether_it_can_be_changed(tmp_path):
    c = client(tmp_path)
    body = c.get("/api/settings/tariff", auth=AUTH).json()
    assert body["configured"] and body["source"] == "env" and body["can_change"] is True and body["reason"] is None
    assert body["current"] == {"day": 4.32, "night": 2.16, "night_start": "23:00", "night_end": "07:00"}
    assert body["currency"] == "UAH" and body["upcoming"] == [] and body["next_change_at"] == "2026-10-18T12:15:00+00:00"
    view_only = client(tmp_path, auth="").get("/api/settings/tariff").json()
    assert view_only["can_change"] is False and "WEB_BASIC_AUTH" in view_only["reason"]


def test_a_change_starts_at_the_next_quarter_hour_and_is_saved(tmp_path):
    c = client(tmp_path)
    r = post(c)
    assert r.status_code == 200 and r.json()["effective_from"] == "2026-10-18T12:15:00+00:00"
    body = r.json()
    assert body["source"] == "saved" and [v["day"] for v in body["versions"]] == [4.32, 6.0]
    assert body["versions"][0]["from"] is None and body["versions"][1]["from"] == "2026-10-18T12:15:00+00:00"
    assert body["current"]["day"] == 4.32                                # "now" (12:00) is still the old price
    assert os.path.exists(tmp_path / "tariff.json")


def test_a_date_starts_at_local_midnight_and_stays_pending(tmp_path):
    c = client(tmp_path)
    r = post(c, {**BODY, "from": "2026-11-01"}).json()
    assert r["effective_from"] == "2026-10-31T22:00:00+00:00"           # 00:00 Kyiv, UTC+2 after the clocks change
    assert [u["day"] for u in r["upcoming"]] == [6.0]


def test_history_before_a_change_is_identical_afterwards(tmp_path):
    """The 15:30 window comes after the change (15:15 local) and is priced new; everything before keeps its price."""
    c = client(tmp_path)
    get = lambda url: c.get(url, auth=AUTH)
    before = get("/api/cost/history?month=2026-10").json()
    day = lambda body, d: next(x for x in body["days"] if x["date"] == d)
    assert day(before, "2026-10-18")["day_kwh"] == pytest.approx(2.0) and day(before, "2026-10-18")["day_cost"] == pytest.approx(2.0 * 4.32, abs=0.01)
    cost = get("/api/cost").json()
    post(c)
    after = get("/api/cost/history?month=2026-10").json()
    assert day(after, "2026-10-17") == day(before, "2026-10-17")        # the whole earlier day is unchanged
    d18_b, d18_a = day(before, "2026-10-18"), day(after, "2026-10-18")
    assert d18_a["night_cost"] == d18_b["night_cost"] and d18_a["night_kwh"] == d18_b["night_kwh"]   # night windows were before the change
    assert d18_a["day_kwh"] == d18_b["day_kwh"]
    assert d18_a["day_cost"] == pytest.approx(d18_b["day_cost"] + 1.0 * (6.0 - 4.32), abs=0.01)     # only the 15:30 kWh got the new price
    assert after["tariff_changes"][0]["day"] == 4.32 and after["tariff_changes"][1]["day"] == 6.0
    assert after["tariff"]["day"] == 4.32                                # not in force yet at 'now' = 15:00
    assert get("/api/cost").json()["today"]["day_cost"] != cost["today"]["day_cost"]               # the cost cache was dropped


def test_changes_need_the_password_and_a_same_origin_json_request(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/settings/tariff", json=BODY, headers=JSON).status_code == 401
    assert c.post("/api/settings/tariff", json=BODY, headers=JSON, auth=("me", "x")).status_code == 401
    assert client(tmp_path, auth="").post("/api/settings/tariff", json=BODY, headers=JSON).status_code == 403   # no password configured
    assert c.post("/api/settings/tariff", content="day=6", auth=AUTH).status_code == 415
    assert post(c, headers={"Origin": "http://evil.example"}).status_code == 403
    assert post(c, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert not os.path.exists(tmp_path / "tariff.json")                 # none of them wrote anything


def test_settings_only_auth_protects_the_tariff_page_too(tmp_path):
    c = client(tmp_path, auth_settings_only=True)
    assert c.get("/api/settings/tariff").status_code == 401
    assert c.get("/api/settings/tariff", auth=AUTH).status_code == 200
    assert c.get("/api/cost").status_code == 200                       # the cost card stays open


@pytest.mark.parametrize("body,fragment", [
    ({**BODY, "day": 0}, "Day price"), ({**BODY, "day": "6"}, "Day price"), ({**BODY, "night": -1}, "Night price"),
    ({**BODY, "night_start": "25:00"}, "HH:MM"), ({**BODY, "night_end": "22:00"}, "differ"),
    ({**BODY, "from": "next week"}, "date"), ({**BODY, "extra": 1}, "body must be"), ([1, 2], "body must be"),
    ({"day": 4.32, "night": 2.16, "night_start": "23:00", "night_end": "07:00", "from": None}, "already"),
    ({**BODY, "from": "2030-01-01"}, "too far"),
])
def test_bad_changes_are_refused_and_nothing_is_saved(tmp_path, body, fragment):
    c = client(tmp_path)
    r = post(c, body)
    assert r.status_code == 400 and fragment in r.json()["detail"], r.text
    assert not os.path.exists(tmp_path / "tariff.json")


def test_single_rate_and_an_unwritable_file(tmp_path):
    c = client(tmp_path)
    r = post(c, {"day": 5.0, "night": None, "night_start": None, "night_end": None, "from": None})
    assert r.status_code == 200 and r.json()["versions"][1]["night"] is None
    blocked = tmp_path / "file"
    blocked.write_text("x")
    c2 = client(tmp_path / "file")                                      # the "folder" is a file: saving must fail cleanly
    assert post(c2).status_code == 500
