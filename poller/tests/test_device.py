import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import control
import device

# Raw dump from the live S6-EH1P on 2026-10-18 (input registers).
DUMP = {
    33000: [12659, 19, 38, 1, 12594, 13108, 13622, 14136, 14640, 12594, 13108, 13622, 0, 0, 0, 0, 0, 0, 0, 0,
            1, 12, 26, 10, 18, 20, 37, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    33090: [0, 0, 110, 376, 4999, 3, 63535, 516, 8, 396, 0, 0, 0, 0, 10000, 1000, 0, 0, 0, 0, 565, 0, 257, 2,
            0, 0, 0, 0, 0, 0, 0, 1, 4, 0, 0, 0, 0, 0, 2106, 392],
    33140: [100, 5313, 10, 200, 1000, 0, 0, 0, 650, 0],
    33250: [18, 2107, 394, 0, 0, 0, 0, 65535, 64857, 0, 0, 0, 0, 65535, 64857, 0, 412, 0, 0, 0, 0, 0, 412, 0,
            786, 0, 0, 0, 0, 0, 786, 65451, 5000, 0, 956, 0, 0, 3, 0, 43605],
}
# More registers from the same live inverter (second probe), the rest of each block read as 0.
EXTRA = {33070: [0, 4806, 0], 33130: [65535, 64868, 33, 532, 0, 0, 0, 2148, 37, 98],
         33155: [0, 0, 65526, 4, 0, 4], 33295: [0, 0, 30, 20, 200, 260]}
REGS = {r: 0 for start, n in device.BLOCKS for r in range(start, start + n)}
REGS.update({start + i: v for start, vals in {**DUMP, **EXTRA}.items() for i, v in enumerate(vals)})
REGS[33140] = 100                                        # SOH (overwritten by the 33130 probe block)
HOLD = {43010 + i: v for i, v in enumerate([100, 20, 1350, 1350, 600, 420, 535, 550, 10, 126, 0, 0, 0, 1, 80])}


class FakeInverter:
    host, port, unit = "192.168.1.50", 502, 1

    def __init__(self, regs=REGS):
        self.regs, self.reads = {**regs, **HOLD}, 0

    def read_input(self, reg, count=1):
        self.reads += 1
        return [self.regs[reg + i] for i in range(count)]

    read_holding = read_input


def test_decode_live_dump():
    d = device.decode(REGS, HOLD)
    inv, bat, met = d["inverter"], d["battery"], d["meter"]
    assert inv["serial"] == "1234567890123456"
    assert inv["clock"] == "2026-10-18T20:37:10"
    assert inv["model_code"] == "0x3173" and inv["dsp_version"] == "0x0013" and inv["hmi_version"] == "0x0026"
    assert inv["status"] == "Generating" and inv["has_fault"] is False
    assert {k: bat[k] for k in ("bms_voltage", "bms_current", "bms_charge_limit_a", "bms_discharge_limit_a",
                                "has_fault", "connected")} == {
        "bms_voltage": 53.13, "bms_current": 1.0, "bms_charge_limit_a": 20.0, "bms_discharge_limit_a": 100.0,
        "has_fault": False, "connected": True}
    assert bat["bms_temperature"] == 0.0           # 33044 was not in the live dump (read as 0)
    assert bat["rated_capacity_ah"] == 126 and bat["capacity_kwh"] == 6.45      # 126 Ah x 51.2 V
    assert bat["overdischarge_soc"] == 20 and bat["backup_soc"] == 80 and bat["force_charge_soc"] == 10
    assert bat["model"] == "Dyness LV"
    assert inv["dc_bus_voltage"] == 480.6 and inv["alarms"] == [] and inv["no_grid"] is False
    assert d["backup"] == {"voltage": 214.8, "current": 3.7, "eps_dod_pct": 20, "offgrid_dod_pct": 30,
                           "switch_time_ms": 2000}
    assert met["location"] == "Grid" and met["type"] == "Eastron 1-phase"
    assert met["voltage"] == 210.7 and met["current"] == 3.94 and met["frequency"] == 50.0
    assert met["active_power"] == -679 and met["reactive_power"] == 412 and met["apparent_power"] == 786
    assert met["power_factor"] == -0.85 and met["connected"] is True
    assert met["import_total_kwh"] == 9.56 and met["export_total_kwh"] == 0.0


def test_faults_and_status_names():
    regs = {**REGS, 33095: 0x1015, 33116: 0b1, 33118: 0b100, 33146: 1 << 3}
    d = device.decode(regs)
    assert d["inverter"]["status"] == "No grid" and d["inverter"]["no_grid"] is True
    assert d["inverter"]["has_fault"] is True and d["inverter"]["fault_codes"][2] == "0x0004"
    names = [a["name"] for a in d["inverter"]["alarms"]]
    assert sorted(names) == ["BMS internal protection", "Battery undervoltage"]
    assert d["battery"]["has_fault"] is True
    assert device.decode({**REGS, 33095: 0x7777})["inverter"]["status"] == "Code 0x7777"


def test_capacity_override():
    bat = device.decode(REGS, HOLD, 10.0)["battery"]
    assert bat["capacity_kwh"] == 10.0 and bat["capacity_source"] == "BATTERY_CAPACITY_KWH"
    assert device.decode(REGS, HOLD)["battery"]["capacity_source"] == "inverter"


def test_capacity_store_order_and_file(tmp_path):
    path = tmp_path / "sub" / "battery-capacity.json"
    st = device.CapacityStore(str(path), "10")
    assert st.value() == (10.0, "BATTERY_CAPACITY_KWH")
    st.set(5.12)
    assert st.value() == (5.12, "saved") and json.loads(path.read_text()) == {"kwh": 5.12}
    assert device.CapacityStore(str(path), "10").value() == (5.12, "saved")      # survives a restart
    st.set(None)
    assert not path.exists() and st.value() == (10.0, "BATTERY_CAPACITY_KWH")
    assert device.CapacityStore(None).value() == (None, None)


def test_capacity_store_refuses_bad_values(tmp_path):
    st = device.CapacityStore(str(tmp_path / "c.json"))
    for bad in (0, -1, 1001, "5", True, float("nan")):
        try:
            st.set(bad)
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass
    assert st.value() == (None, None) and not (tmp_path / "c.json").exists()


def test_capacity_store_ignores_a_broken_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    assert device.CapacityStore(str(path), "7").value() == (7.0, "BATTERY_CAPACITY_KWH")


def test_reader_applies_a_capacity_change_without_reading_again(tmp_path):
    inv = FakeInverter()
    r = device.DeviceReader(inv, capacity=device.CapacityStore(str(tmp_path / "c.json")))
    assert r.capacity_info()["inverter_kwh"] is None                            # nothing read yet
    r.refresh()
    assert r.cached()["battery"]["capacity_kwh"] == 6.45
    n = inv.reads
    info = r.set_capacity(10.24)
    assert inv.reads == n
    assert r.cached()["battery"]["capacity_kwh"] == 10.24 and r.cached()["battery"]["capacity_source"] == "saved"
    assert info == {"capacity_kwh": 10.24, "source": "saved", "saved_kwh": 10.24, "env_kwh": None,
                    "inverter_kwh": 6.45, "rated_capacity_ah": 126, "max_kwh": 1000}
    info = r.set_capacity(None)
    assert info["capacity_kwh"] == 6.45 and info["source"] == "inverter"
    assert r.cached()["battery"]["capacity_kwh"] == 6.45


def test_bms_temperature():
    assert device.decode({**REGS, 33044: 2679})["battery"]["bms_temperature"] == 26.79
    assert device.decode({**REGS, 33044: 0x10000 - 520})["battery"]["bms_temperature"] == -5.2
    assert device.decode({**REGS, 33044: 0x10000 - 4001})["battery"]["bms_temperature"] is None   # implausible
    assert device.decode({**REGS, 33044: 9001})["battery"]["bms_temperature"] is None


def test_disconnected_bms_and_meter_and_bad_clock():
    d = device.decode({**REGS, 33141: 0, 33251: 0, 33023: 13, 33044: 2679}, HOLD)
    assert d["battery"]["connected"] is False and d["battery"]["bms_temperature"] is None
    assert d["meter"]["connected"] is False
    assert d["inverter"]["clock"] is None


def test_reader_caches():
    t = [0.0]
    inv = FakeInverter()
    r = device.DeviceReader(inv, ttl=30, clock=lambda: t[0])
    first = r.read()
    assert first["logger"] == {"host": "192.168.1.50", "port": 502, "unit": 1}
    n = inv.reads
    t[0] = 10
    assert r.read() is first and inv.reads == n         # cached
    t[0] = 31
    r.read()
    assert inv.reads == 2 * n


def test_http_device_endpoint():
    ctl = control.Controller(FakeInverter(), control.Config({"INFLUX_TOKEN": "secret"}))
    ctl.devices = device.DeviceReader(ctl.inv)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), control.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/device",
                                     headers={"Authorization": "Bearer secret"})
        body = json.load(urllib.request.urlopen(req))
        assert body["inverter"]["serial"] == "1234567890123456"
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/device")
        try:
            urllib.request.urlopen(req)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        srv.shutdown()


def test_http_battery_capacity(tmp_path):
    audit = []
    ctl = control.Controller(FakeInverter(), control.Config({"INFLUX_TOKEN": "secret"}),
                             audit=lambda action, **f: audit.append((action, f)))
    ctl.devices = device.DeviceReader(ctl.inv, capacity=device.CapacityStore(str(tmp_path / "c.json")))
    ctl.devices.refresh()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), control.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, body=None, token="secret"):
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/battery-capacity", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    try:
        assert call("GET")[1]["capacity_kwh"] == 6.45
        assert call("POST", {"kwh": 10}, token="wrong")[0] == 401
        assert call("POST", {"kwh": 0})[0] == 400
        assert call("POST", {"kwh": 10, "extra": 1})[0] == 400
        assert ctl.devices.capacity.saved is None and audit == []
        status, body = call("POST", {"kwh": 10})
        assert status == 200 and body["capacity_kwh"] == 10.0 and body["applied"] == "Battery capacity: 6.45 → 10.0 kWh"
        assert audit[-1][0] == "battery_capacity" and audit[-1][1]["verified"] is True
        status, body = call("POST", {"kwh": None})
        assert status == 200 and body["source"] == "inverter" and body["applied"].endswith("(from inverter)")
    finally:
        srv.shutdown()
