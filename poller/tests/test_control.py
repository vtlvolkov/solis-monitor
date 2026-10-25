import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import pytest

import control
from control import (REG_BACKUP_SOC, REG_CLOCK, REG_MAX_CHARGE_A, REG_MAX_DISCHARGE_A, REG_OVERDISCHARGE_SOC,
                     REG_STORAGE_SWITCH, REG_TOU_CHARGE, REG_TOU_DISCHARGE, REG_TOU_SWITCH, Config, ControlError,
                     Controller)

# Holding registers read from the live S6-EH1P on 2026-10-18.
DUMP = {
    43000: [26, 10, 18, 20, 54, 17, 1, 190, 1, 4, 100, 20, 1350, 1350, 600, 420, 535, 550, 10, 126, 0, 0, 0, 1, 80,
            50, 450, 50, 0, 0],
    43100: [5500, 10, 4700, 1000, 4500, 10, 30, 30, 3105, 600, 33, 1, 2300, 5000, 1, 0, 500, 1000, 1000, 420, 535,
            550, 600, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 30, 20, 200, 260, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    43700: [0, 0, 0, 0, 0, 165, 0, 1, 100, 500, 490, 0, 0, 23, 59, 50, 500, 490, 0, 0, 0, 0, 50, 500, 490, 0, 0, 0,
            0, 50, 500, 490, 0, 0, 0, 0, 50, 500, 490, 0, 0, 0, 0, 50, 500, 490, 0, 0, 0, 0],
    43750: [50, 500, 490, 0, 0, 0, 0] * 6,
}
HOLD = {start + i: v for start, vals in DUMP.items() for i, v in enumerate(vals)}


class FakeInverter:
    def __init__(self, regs=None, keep=True):
        self.regs = {**HOLD, **(regs or {})}
        self.keep = keep          # False: firmware ignores writes
        self.writes = []

    def read_holding(self, reg, count=1):
        return [self.regs[reg + i] for i in range(count)]

    def write_holding(self, reg, value):
        self.writes.append(("fc06", reg, [value]))
        if self.keep:
            self.regs[reg] = value

    def write_block(self, reg, values):
        self.writes.append(("fc16", reg, list(values)))
        if self.keep:
            for i, v in enumerate(values):
                self.regs[reg + i] = v


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


NOW = datetime(2026, 10, 18, 21, 5, 30)


def make(enabled=True, max_charge="100", max_discharge="100", interval="60", keep=True, regs=None):
    cfg = Config({"CONTROL_ENABLED": "true" if enabled else "false", "CURRENT_SCALE": "0.1",
                  "MAX_CHARGE_CURRENT_A": max_charge, "MAX_DISCHARGE_CURRENT_A": max_discharge,
                  "MIN_WRITE_INTERVAL_S": interval, "INFLUX_TOKEN": "secret", "TZ": "Europe/Kyiv"})
    inv, clock, audits = FakeInverter(regs, keep), Clock(), []
    ctl = Controller(inv, cfg, audit=lambda action, **f: audits.append((action, f)), clock=clock,
                     sleep=lambda s: None, now=lambda: NOW)
    return ctl, inv, clock, audits


def err(fn, *args):
    with pytest.raises(ControlError) as e:
        fn(*args)
    return e.value


# ---------------------------------------------------------------- reading

def test_settings_decoding_live_dump():
    s, = [make()[0].settings()]
    assert s["grid_charging"] is True and s["time_of_use_mode"] is False and s["reserve_mode"] is False
    assert s["storage_flags"]["self_use"] is True
    assert s["max_charge_current_a"] == 100.0
    assert s["clock"] == "2026-10-18T20:54:17"
    assert s["battery"] == {"max_charge_soc": 100, "overdischarge_soc": 20, "force_charge_soc": 10,
                            "backup_soc": 80, "offgrid_overdischarge_soc": 30, "eps_dod": 20,
                            "rated_capacity_ah": 126, "float_voltage": 53.5, "equalize_voltage": 55.0}
    c1 = s["tou"]["charge"][0]
    assert c1 == {"slot": 1, "enabled": True, "soc": 100, "current_a": 50.0, "voltage": 49.0,
                  "start": "00:00", "end": "23:59"}
    assert [x["enabled"] for x in s["tou"]["charge"][1:]] == [False] * 5
    assert not any(x["enabled"] for x in s["tou"]["discharge"])
    assert s["control"]["server_time"] == "2026-10-18T21:05:30"


# ---------------------------------------------------------------- guards

def test_disabled_refuses_every_write():
    ctl, inv, *_ = make(enabled=False)
    for setting, value in [("grid_charging", False), ("max_charge_current_a", 30), ("backup_soc", 50),
                           ("clock_sync", True)]:
        assert err(ctl.apply, setting, value).status == 403
    assert inv.writes == []


def test_rate_limit_and_no_change():
    ctl, inv, clock, _ = make()
    assert ctl.apply("max_charge_current_a", 100)["no_change"] is True       # no write, no rate limit
    ctl.apply("max_charge_current_a", 30)
    clock.t += 30
    e = err(ctl.apply, "reserve_mode", True)
    assert e.status == 429 and "30 s" in str(e)
    clock.t += 31
    ctl.apply("reserve_mode", True)
    assert len(inv.writes) == 2


def test_unknown_and_concurrent():
    ctl, inv, *_ = make()
    assert err(ctl.apply, "off_grid", True).status == 400
    ctl.lock.acquire()
    assert err(ctl.apply, "grid_charging", False).status == 409
    assert inv.writes == []


def test_readback_mismatch_reported():
    ctl, inv, _, audits = make(keep=False)
    r = ctl.apply("backup_soc", 40)
    assert r["verified"] is False and "warning" in r
    assert audits[0][1]["verified"] is False


# ---------------------------------------------------------------- flags

@pytest.mark.parametrize("setting,bit,value,expected", [
    ("grid_charging", 5, False, 0b000001),
    ("time_of_use_mode", 1, True, 0b100011),
    ("reserve_mode", 4, True, 0b110001),
])
def test_flags_touch_only_their_bit(setting, bit, value, expected):
    ctl, inv, *_ = make()
    r = ctl.apply(setting, value)
    assert inv.writes == [("fc06", REG_STORAGE_SWITCH, [expected])] and r["verified"]


def test_flag_requires_bool():
    ctl, inv, *_ = make()
    assert err(ctl.apply, "reserve_mode", 1).status == 400


# ---------------------------------------------------------------- currents

def test_current_scaled_and_bounded():
    ctl, inv, clock, _ = make(max_charge="80")
    r = ctl.apply("max_charge_current_a", 30)
    assert inv.writes == [("fc06", REG_MAX_CHARGE_A, [300])]
    assert r["applied"] == "Max charge current: 100 A -> 30 A"
    clock.t += 61
    for bad in (80.1, -1, "30", True, None):
        assert err(ctl.apply, "max_charge_current_a", bad).status == 400
    assert err(make(max_discharge="0")[0].apply, "max_discharge_current_a", 10).status == 403


# ---------------------------------------------------------------- SOC limits

def test_overdischarge_soc_bounds():
    ctl, inv, *_ = make()
    assert err(ctl.apply, "overdischarge_soc", 10).status == 400     # must stay above force-charge SOC (10)
    assert err(ctl.apply, "overdischarge_soc", 100).status == 400    # must stay below max charge SOC (100)
    assert err(ctl.apply, "overdischarge_soc", 30.5).status == 400
    r = ctl.apply("overdischarge_soc", 30)
    assert inv.writes == [("fc16", REG_OVERDISCHARGE_SOC, [30])] and r["verified"]
    assert r["applied"] == "Over-discharge SOC: 20% -> 30%"


def test_backup_soc_bounds():
    ctl, inv, *_ = make()
    assert err(ctl.apply, "backup_soc", 19).status == 400            # below over-discharge SOC (20)
    assert err(ctl.apply, "backup_soc", 101).status == 400
    ctl.apply("backup_soc", 40)
    assert inv.writes == [("fc16", REG_BACKUP_SOC, [40])]


# ---------------------------------------------------------------- time of use

def tou(**kw):
    return {"kind": "charge", "slot": 2, "enabled": True, "start": "23:00", "end": "07:00",
            "current_a": 30, "soc": 100, **kw}


def test_tou_writes_slot_block_then_switch():
    ctl, inv, *_ = make()
    r = ctl.apply("tou_slot", tou())
    base = REG_TOU_CHARGE + 7
    assert inv.writes == [("fc16", base, [100, 300, 490, 23, 0, 7, 0]),   # voltage kept
                          ("fc16", REG_TOU_SWITCH, [0b11])]
    assert r["verified"] is True
    assert "Charge slot 2: 00:00-00:00 50 A 50% (off) -> 23:00-07:00 30 A 100% (on)" == r["applied"]


def test_tou_discharge_slot_and_floor():
    ctl, inv, *_ = make()
    assert err(ctl.apply, "tou_slot", tou(kind="discharge", soc=15)).status == 400   # below over-discharge 20%
    ctl.apply("tou_slot", tou(kind="discharge", slot=1, soc=30, current_a=20))
    assert inv.writes[0] == ("fc16", REG_TOU_DISCHARGE, [30, 200, 490, 23, 0, 7, 0])
    assert inv.writes[1] == ("fc16", REG_TOU_SWITCH, [1 | 1 << 6])


def test_tou_disable_only_writes_switch():
    ctl, inv, *_ = make()
    r = ctl.apply("tou_slot", {"kind": "charge", "slot": 1, "enabled": False, "start": "00:00", "end": "23:59",
                               "current_a": 50, "soc": 100})
    assert inv.writes == [("fc16", REG_TOU_SWITCH, [0])] and r["verified"]


@pytest.mark.parametrize("bad", [
    {"slot": 7}, {"slot": 0}, {"kind": "both"}, {"start": "24:00"}, {"end": "7:00"}, {"start": "07:00", "end": "07:00"},
    {"current_a": 101}, {"current_a": "30"}, {"soc": 5}, {"soc": 101}, {"enabled": "yes"},
])
def test_tou_validation(bad):
    ctl, inv, *_ = make()
    assert err(ctl.apply, "tou_slot", tou(**bad)).status == 400
    assert inv.writes == []


def test_tou_current_needs_limit():
    ctl, inv, *_ = make(max_charge="0")
    assert err(ctl.apply, "tou_slot", tou()).status == 403


# ---------------------------------------------------------------- clock

def test_clock_sync_writes_server_local_time():
    ctl, inv, *_ = make()
    r = ctl.apply("clock_sync", True)
    assert inv.writes == [("fc16", REG_CLOCK, [26, 10, 18, 21, 5, 30])]
    assert r["verified"] is True
    assert r["applied"] == "Inverter clock: 2026-10-18 20:54:17 -> 2026-10-18 21:05:30 (Europe/Kyiv)"
    assert err(ctl.apply, "clock_sync", False).status in (400, 429)


# ---------------------------------------------------------------- HTTP

@pytest.fixture
def server():
    ctl, inv, *_ = make()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), control.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", inv
    srv.shutdown()


def call(url, method="GET", body=None, token="secret"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_requires_token(server):
    url, inv = server
    assert call(url + "/settings", token=None)[0] == 401
    assert call(url + "/settings", token="wrong")[0] == 401
    assert call(url + "/settings", "POST", {"setting": "grid_charging", "value": False}, token="wrong")[0] == 401
    assert inv.writes == []


def test_http_get_and_post(server):
    url, inv = server
    status, body = call(url + "/settings")
    assert status == 200 and body["grid_charging"] is True and len(body["tou"]["charge"]) == 6
    status, body = call(url + "/settings", "POST", {"setting": "max_charge_current_a", "value": 25})
    assert status == 200 and body["verified"] is True and inv.writes == [("fc06", REG_MAX_CHARGE_A, [250])]
    assert call(url + "/settings", "POST", b"not json")[0] == 400
    assert call(url + "/settings", "POST", [1, 2])[0] == 400
    assert call(url + "/other")[0] == 404
    assert call(url + "/probe")[0] == 404


def test_cached_settings():
    ctl, inv, clock, _ = make()
    first = ctl.cached_settings()                       # no cache yet: live read
    assert first["grid_charging"] is True
    inv.regs[REG_STORAGE_SWITCH] = 1                    # changed on the inverter panel
    c = ctl.cached_settings()
    assert c["grid_charging"] is True and c["cached_age_s"] == 0       # served from cache
    clock.t += 601
    assert ctl.cached_settings()["grid_charging"] is False            # stale -> live read


def test_change_invalidates_cache():
    ctl, inv, clock, _ = make()
    ctl.cached_settings()
    ctl.apply("reserve_mode", True)
    assert ctl.cached_settings()["reserve_mode"] is True
    assert ctl.cached_settings()["control"]["next_change_in_s"] == 60
