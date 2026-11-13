import io
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import outages
from outages import OutageStore, OutageWatcher, Yasno, intervals, next_outage, parse_group, verdict

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 13, 15, 0, tzinfo=KYIV)          # 15:00 Kyiv
# Today's real answer for Kyiv group 3.1 (app.yasno.ua, 13 Nov 2026), and a made-up tomorrow
ANSWER = {
    "3.1": {"today": {"slots": [{"start": 0, "end": 60, "type": "Definite"}, {"start": 60, "end": 480, "type": "NotPlanned"},
                                {"start": 480, "end": 840, "type": "Definite"}, {"start": 840, "end": 960, "type": "NotPlanned"},
                                {"start": 960, "end": 1320, "type": "Definite"}, {"start": 1320, "end": 1440, "type": "NotPlanned"}],
                      "date": "2026-11-13T00:00:00+02:00", "status": "ScheduleApplies"},
            "tomorrow": {"slots": [], "date": "2026-11-14T00:00:00+02:00", "status": "WaitingForSchedule"},
            "updatedOn": "2026-11-13T07:45:01+00:00"},
    "20.1": {"today": {"slots": [], "date": "2026-11-13T00:00:00+02:00", "status": "ScheduleApplies"}},
}


class FakeYasno:
    def __init__(self, answer=ANSWER):
        self.answer, self.calls = answer, []

    def planned(self, source):
        self.calls.append(source)
        if isinstance(self.answer, Exception):
            raise self.answer
        return json.loads(json.dumps(self.answer))


def test_parse_keeps_outages_only_and_checks_the_shape():
    days = parse_group(ANSWER, "3.1")
    assert days[0]["slots"] == [{"start": 0, "end": 60, "type": "Definite"}, {"start": 480, "end": 840, "type": "Definite"},
                                {"start": 960, "end": 1320, "type": "Definite"}]
    assert days[1] == {"date": "2026-11-14T00:00:00+02:00", "status": "WaitingForSchedule", "slots": []}
    odd = {"1.1": {"today": {"slots": [{"start": 60, "end": 30, "type": "Definite"}, {"start": True, "end": 90, "type": "Definite"},
                                       {"start": 100, "end": 200, "type": "Possible"}, "junk"], "date": "2026-11-13T00:00:00+02:00", "status": "ScheduleApplies"}}}
    assert parse_group(odd, "1.1")[0]["slots"] == [{"start": 100, "end": 200, "type": "Possible"}]
    with pytest.raises(outages.OutageError):
        parse_group(ANSWER, "99.1")


def test_next_outage_in_local_time():
    days = parse_group(ANSWER, "3.1")
    a, b = next_outage(days, NOW, "Europe/Kyiv")
    assert (a.hour, b.hour) == (16, 22)
    a, b = next_outage(days, NOW.replace(hour=10), "Europe/Kyiv")
    assert (a.hour, b.hour) == (8, 14)                       # the one going on
    assert next_outage(days, NOW.replace(hour=23), "Europe/Kyiv") is None   # tomorrow not published
    assert len(intervals(days, "Europe/Kyiv")) == 3


def test_verdict_minute_by_minute():
    a, b = NOW.replace(hour=16), NOW.replace(hour=22)                      # 6 h
    profile = {h: 500 for h in range(24)}
    v = verdict(a, b, NOW, soc=80, floor=20, capacity_kwh=10, profile=profile, load_now=500, battery_w=0)
    assert v["enough"] and v["load_w"] == 500 and v["soc_at_start"] == 80
    assert v["lasts_s"] == 12 * 3600                                       # 6 kWh above the floor at 500 W
    v = verdict(a, b, NOW, soc=40, floor=20, capacity_kwh=10, profile=profile, load_now=500, battery_w=0)
    assert not v["enough"] and v["runs_out_at"] == "20:00" and v["short_s"] == 2 * 3600 and v["lasts_s"] == 4 * 3600
    v = verdict(a, b, NOW, soc=40, floor=20, capacity_kwh=10, profile=profile, load_now=500, battery_w=2000)
    assert v["soc_at_start"] == 60 and v["enough"]                         # charging for an hour at 2 kW until 16:00
    v = verdict(a, b, NOW, soc=95, floor=20, capacity_kwh=10, profile=profile, load_now=500, battery_w=2000, max_soc=90)
    assert v["soc_at_start"] == 90                                         # never above the max charge SOC
    assert verdict(a, b, NOW, soc=None, floor=20, capacity_kwh=10, profile=profile, load_now=500, battery_w=0) is None
    evening = verdict(a, b, NOW, soc=50, floor=20, capacity_kwh=10, profile={**profile, 19: 2000, 20: 2000}, load_now=None, battery_w=0)
    assert evening["load_w"] == 1000 and not evening["enough"]             # a heavy evening counts


def test_store_keeps_only_the_group(tmp_path):
    st = OutageStore(str(tmp_path))
    assert not st.configured() and st.alerts == {"before": True, "short": True, "changes": True}
    st.set_config("yasno-kyiv", "3.1", {"changes": False})
    again = OutageStore(str(tmp_path))
    assert again.configured() and again.group == "3.1" and again.alerts["changes"] is False
    assert "street" not in json.loads((tmp_path / "outages.json").read_text())
    for bad in (("nope", "3.1", {}), ("yasno-kyiv", "3", {}), ("yasno-kyiv", "3.1", {"x": True}), ("yasno-kyiv", "3.1", {"before": "yes"})):
        with pytest.raises(ValueError):
            st.set_config(*bad)
    st.keep_schedule({"group": "3.1", "days": []})
    st.set_config("yasno-kyiv", "20.1", {})
    assert st.schedule is None and not (tmp_path / "outage-schedule.json").exists()     # another group: the old schedule goes
    st.set_config(None, None, {})
    assert not st.configured()


def watcher(tmp_path, **kw):
    st = OutageStore(str(tmp_path))
    st.set_config("yasno-kyiv", "3.1", {})
    sent = []
    clock = [NOW.timestamp()]
    w = OutageWatcher(st, kw.pop("yasno", FakeYasno()), "Europe/Kyiv", send=sent.append, clock=lambda: clock[0],
                      state=lambda: (80, 0, 500), battery=lambda: (10, 20, 100), profile=lambda: {h: 500 for h in range(24)}, **kw)
    return w, sent, clock


def test_payload_and_the_last_good_copy(tmp_path):
    w, _, clock = watcher(tmp_path)
    assert w.payload()["days"] == []                                      # nothing fetched yet
    assert w.refresh()
    p = w.payload()
    assert p["configured"] and p["group"] == "3.1" and p["region"] == "Київ" and p["source"] == "Yasno"
    assert p["next"]["start"].startswith("2026-11-13T16:00") and p["next"]["in_s"] == 3600 and not p["next"]["ongoing"]
    assert p["battery"]["enough"] and p["stale"] is False and p["updated_on"] == "2026-11-13T07:45:01+00:00"
    w.yasno = FakeYasno(outages.OutageError("Yasno not reachable (URLError)"))
    assert not w.refresh()
    p = w.payload()
    assert p["stale"] and p["error"].startswith("Yasno not reachable") and p["next"]          # the last good copy
    clock[0] = NOW.replace(hour=17).timestamp()
    assert w.payload()["next"]["ongoing"] is True


def test_telegram_before_an_outage_and_when_short_once_each(tmp_path):
    w, sent, clock = watcher(tmp_path)
    w.refresh()
    w.check_alerts()
    assert sent == []                                                     # 16:00 is an hour away
    clock[0] = NOW.replace(hour=15, minute=40).timestamp()
    w.check_alerts(); w.check_alerts()
    assert len(sent) == 1 and sent[0].startswith("⏰ Planned outage in 20 min (16:00–22:00, 6 h). Battery 80%")
    w.state = lambda: (30, 0, 500)                                        # not enough now
    w._alerted.clear()
    w.check_alerts()
    assert any(m.startswith("⚠️ The battery will not last through the 16:00–22:00 outage: runs out ≈ 18:00") for m in sent)


def test_telegram_when_tomorrow_is_published_or_changes(tmp_path):
    w, sent, _ = watcher(tmp_path)
    w.refresh()
    assert sent == []                                                     # the first fetch announces nothing
    published = json.loads(json.dumps(ANSWER))
    published["3.1"]["tomorrow"] = {"slots": [{"start": 240, "end": 480, "type": "Definite"}, {"start": 720, "end": 960, "type": "Possible"}],
                                    "date": "2026-11-14T00:00:00+02:00", "status": "ScheduleApplies"}
    w.yasno = FakeYasno(published)
    w.refresh()
    assert sent == ["📅 Tomorrow's schedule is out for group 3.1: 04:00–08:00, 12:00–16:00 (possible)."]
    w.refresh()
    assert len(sent) == 1                                                 # nothing new
    changed = json.loads(json.dumps(published))
    changed["3.1"]["today"]["slots"][4]["end"] = 1380
    w.yasno = FakeYasno(changed)
    w.refresh()
    assert sent[-1] == "📅 Today's schedule changed for group 3.1: 00:00–01:00, 08:00–14:00, 16:00–23:00."


def test_yasno_client_builds_the_requests_and_reads_the_answers():
    seen = []
    def opener(req, timeout):
        seen.append(req.full_url)
        body = {"streets": [{"id": 911, "value": "вул. Хрещатик"}, {"id": "x", "value": 1}],
                "group": {"group": 20, "subgroup": 1}}.get(req.full_url.split("/v2/")[-1].split("?")[0], ANSWER)
        return io.BytesIO(json.dumps(body).encode())
    y = Yasno(opener=opener)
    assert y.planned("yasno-kyiv")["3.1"]["updatedOn"]
    assert seen[-1] == "https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages"
    assert y.streets("yasno-kyiv", "Хрещ") == [{"id": 911, "value": "вул. Хрещатик"}]
    assert "regionId=25&dsoId=902&query=%D0%A5" in seen[-1]
    assert y.group("yasno-kyiv", 911, 39986) == "20.1"


def test_load_profile_by_local_hour():
    rows = [(datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc), 400.0), (datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc), 600.0),
            (datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc), None)]
    assert outages.load_profile(rows, "Europe/Kyiv") == {16: 500}          # 14:00 UTC is 16:00 in Kyiv
    assert NOW + timedelta(0) == NOW


def test_the_overview_card_can_be_hidden_with_or_without_a_source(tmp_path):
    st = OutageStore(str(tmp_path))
    assert st.show_card is True                                            # shown by default, even before setup ("Set up" prompt)
    st.set_config(None, None, {}, overview=False)
    assert OutageStore(str(tmp_path)).show_card is False                   # kept, also while off
    st.set_config("yasno-kyiv", "3.1", {})
    assert st.show_card is False                                           # saving the group keeps the choice
    st.set_config("yasno-kyiv", "3.1", {}, overview=True)
    assert st.show_card is True
    with pytest.raises(ValueError):
        st.set_config("yasno-kyiv", "3.1", {}, overview="no")
    w = OutageWatcher(st, FakeYasno(), "Europe/Kyiv")
    assert w.payload()["show"] is True
    st.set_config("yasno-kyiv", "3.1", {}, overview=False)
    assert w.payload()["show"] is False


# ---------------------------------------------------------------- the community mirror (DTEK regions)
MIRROR = {"regionId": "kyiv-region", "lastUpdated": "2026-11-13T12:51:11.641Z", "lastUpdateStatus": {"status": "parsed", "ok": True},
          "fact": {"today": 1794520800, "update": "12.11.2026 20:47",
                   "data": {"1794520800": {"GPV3.1": {**{str(h): "yes" for h in range(1, 25)}, "2": "no", "3": "no", "4": "no", "5": "first",
                                                      "11": "second", "12": "no", "13": "no", "14": "no", "15": "no", "16": "maybe", "22": "mfirst"},
                                           "GPV1.2": {str(h): "yes" for h in range(1, 25)}, "GPV1.1": {str(h): "yes" for h in range(1, 25)}}}},
          "preset": {"sch_names": {"GPV3.1": "Черга 3.1", "GPV1.1": "Черга 1.1", "GPV1.2": "Черга 1.2"}}}


class FakeMirror:
    def __init__(self, data=MIRROR): self.data = data
    def fetch(self, source, max_age_s=0):
        if isinstance(self.data, Exception):
            raise self.data
        return json.loads(json.dumps(self.data))
    def groups(self, source): return outages.Mirror.groups(self, source)


def test_mirror_hours_become_slots_with_half_hours_and_possible_ones():
    days, updated, warning = outages.parse_mirror(MIRROR, "3.1", "Europe/Kyiv", NOW)
    assert days[0]["date"].startswith("2026-11-13T00:00:00") and days[0]["status"] == "ScheduleApplies"
    assert days[0]["slots"] == [{"start": 60, "end": 270, "type": "Definite"},      # hours 2-4 and the first half of 5: 01:00-04:30
                                {"start": 630, "end": 900, "type": "Definite"},     # the second half of 11, then 12-15: 10:30-15:00
                                {"start": 900, "end": 960, "type": "Possible"},     # 16 "maybe": 15:00-16:00
                                {"start": 1260, "end": 1290, "type": "Possible"}]   # 22 "mfirst": 21:00-21:30
    assert days[1]["status"] == "WaitingForSchedule" and days[1]["slots"] == []
    assert updated == "2026-11-12T20:47:00+02:00" and warning is None
    with pytest.raises(outages.OutageError):
        outages.parse_mirror(MIRROR, "9.9", "Europe/Kyiv", NOW)


def test_mirror_staleness_is_said_but_the_schedule_still_shows():
    old = {**MIRROR, "lastUpdated": "2026-11-13T05:00:00Z"}
    assert "since 13.11 07:00" in outages.parse_mirror(old, "3.1", "Europe/Kyiv", NOW)[2]
    failing = {**MIRROR, "lastUpdateStatus": {"ok": False}}
    assert outages.parse_mirror(failing, "3.1", "Europe/Kyiv", NOW)[2]


def test_mirror_groups_in_order_with_names():
    assert FakeMirror().groups("dtek-kyiv-region") == [{"value": "1.1", "label": "Черга 1.1"}, {"value": "1.2", "label": "Черга 1.2"},
                                                        {"value": "3.1", "label": "Черга 3.1"}]


def test_the_watcher_reads_a_dtek_region_from_the_mirror(tmp_path):
    st = OutageStore(str(tmp_path))
    st.set_config("dtek-kyiv-region", "3.1", {})
    w = OutageWatcher(st, FakeYasno(), "Europe/Kyiv", clock=lambda: NOW.replace(hour=9).timestamp(), mirror=FakeMirror(),
                      state=lambda: (80, 0, 500), battery=lambda: (10, 20, 100), profile=lambda: {h: 500 for h in range(24)})
    assert w.refresh() and w.yasno.calls == []                                # never asks Yasno for a DTEK region
    p = w.payload()
    assert p["source"] == "DTEK: Kyiv Oblast" and p["kind"] == "mirror" and p["region"] == "Київська обл."
    assert p["next"]["start"].startswith("2026-11-13T10:30") and p["next"]["end"].startswith("2026-11-13T15:00")   # possible ones never count as next
    assert p["updated_on"] == "2026-11-12T20:47:00+02:00" and p["warning"] is None
