import faults
from faults import EventTracker

OK = {"fault_code_01": 0, "fault_code_02": 0, "fault_code_03": 0, "fault_code_04": 0, "fault_code_05": 0,
      "operating_status": 1, "bms_fault_01": 0, "bms_fault_02": 0, "status_code": 3, "grid_voltage": 230.0,
      "battery_soc": 90.0, "total_load_power": 700.0}


def test_active_alarms_names_and_severity():
    a = faults.active_alarms({**OK, "fault_code_02": 0b10, "fault_code_05": 1 << 2, "bms_fault_01": 1 << 3,
                              "operating_status": 1 | 1 << 13})
    assert a == {"f2b1": ("Backup overload", "fault"), "f5b2": ("Over-temperature", "fault"),
                 "bms1b3": ("BMS over-temperature", "fault"), "op13": ("Fan fault", "warning")}


def test_no_grid_is_outage_not_alarm():
    v = {**OK, "fault_code_01": 1}
    assert faults.active_alarms(v) == {}
    assert faults.grid_lost(v)
    assert faults.grid_lost({**OK, "status_code": 0x1015})
    assert faults.grid_lost({**OK, "grid_voltage": 12.0})
    assert not faults.grid_lost(OK)


def test_unknown_bits_still_reported():
    assert faults.active_alarms({**OK, "fault_code_01": 1 << 14}) == {"f1b14": ("Fault 1 bit 14", "fault")}


def test_status_text():
    assert faults.status_text(3) == "Generating"
    assert faults.status_text(0x1015) == "No grid"
    assert faults.status_text(0xABCD) == "Code 0xABCD"


def test_tracker_debounces_and_pairs():
    t = EventTracker(debounce=2)
    lost = {**OK, "fault_code_01": 1, "grid_voltage": 0.0, "battery_soc": 88.0}
    assert t.update(OK) == []
    assert t.update(lost) == []                         # one reading is not enough
    assert t.update(OK) == []                           # flap resets the counter
    assert t.update(lost) == []
    ev = t.update(lost)
    assert ev == [{"kind": "outage", "key": "grid", "name": "Grid outage", "severity": "warning", "state": "start",
                   "soc": 88.0, "load": 700.0}]
    assert t.update(lost) == []                         # still out: no repeat
    t.update(OK)
    ev = t.update({**OK, "battery_soc": 61.0})
    assert [(e["state"], e["soc"]) for e in ev] == [("end", 61.0)]


def test_tracker_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "events.json")
    t = [1_000_000.0]
    lost = {**OK, "fault_code_01": 1, "grid_voltage": 0.0, "battery_soc": 88.0}
    first = EventTracker(debounce=1, path=path, clock=lambda: t[0])
    assert first.update(OK) == [] and not (tmp_path / "events.json").exists()      # nothing to remember yet
    assert [e["state"] for e in first.update(lost)] == ["start"]
    t[0] += 1800
    again = EventTracker(debounce=2, path=path, clock=lambda: t[0])                  # the poller restarted mid-outage
    assert again.active_events() == [{"key": "grid", "kind": "outage", "name": "Grid outage", "severity": "warning",
                                      "since": 1_000_000.0, "soc": 88.0}]
    assert again.update(lost) == [] and again.update(lost) == []                      # still out: no second start
    again.update(OK)
    ev = again.update({**OK, "battery_soc": 61.0})
    assert [(e["kind"], e["state"], e["soc"]) for e in ev] == [("outage", "end", 61.0)]
    assert EventTracker(path=path).active_events() == []                              # the end is remembered too


def test_tracker_without_a_file_and_with_a_broken_one(tmp_path):
    t = EventTracker(debounce=1)
    assert [e["state"] for e in t.update({**OK, "fault_code_02": 2})] == ["start"]    # no path: works in memory
    (tmp_path / "events.json").write_text("{not json")
    t = EventTracker(debounce=1, path=str(tmp_path / "events.json"))
    assert t.active_events() == []
    assert [e["state"] for e in t.update({**OK, "fault_code_02": 2})] == ["start"]    # and the file is usable again
    assert EventTracker(path=str(tmp_path / "events.json")).active_events()[0]["key"] == "f2b1"


def test_tracker_alarms_start_and_end():
    t = EventTracker(debounce=1)
    ev = t.update({**OK, "fault_code_02": 2})
    assert [(e["kind"], e["key"], e["state"], e["name"]) for e in ev] == [("alarm", "f2b1", "start", "Backup overload")]
    ev = t.update(OK)
    assert [(e["key"], e["state"]) for e in ev] == [("f2b1", "end")]
