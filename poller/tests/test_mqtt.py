"""MQTT: the settings, the messages, Home Assistant discovery, and the publisher against a fake paho client."""
import json
import os
import stat
import threading
import time

import pytest

import mqtt

GOOD = {"enabled": True, "host": "192.168.0.20", "port": 1883, "username": "solis", "password": "pw", "tls": False,
        "prefix": "solis/home", "interval_s": 10, "discovery": True}
VALS = {"battery_soc": 73.0, "battery_power_signed": 1130.0, "pv_power": 2410.4, "grid_power": 0.0, "house_load_power": 980.0,
        "backup_load_power": 290.0, "total_load_power": 1270.0, "grid_voltage": 231.04, "grid_frequency": 50.013,
        "battery_soh": 100.0, "battery_temperature": 21.37, "pv_energy_today": 14.8, "grid_import_today": 4.6,
        "load_consumption_today": 12.9, "grid_import_total": 2210.0}


class Rc:
    def __init__(self, value):
        self.value, self.is_failure = value, value != 0

    def __str__(self):
        return f"rc {self.value}"


class FakeClient:
    """Enough of paho's Client: connect, loop_start (answers CONNACK at once), publish, will, disconnect."""
    instances: list = []
    refuse: Exception | None = None
    connack = 0

    def __init__(self, client_id):
        self.client_id, self.published, self.will, self.auth, self.tls = client_id, [], None, None, False
        self.on_connect = self.on_disconnect = None
        self.disconnected = self.stopped = False
        FakeClient.instances.append(self)

    def username_pw_set(self, u, p): self.auth = (u, p)
    def tls_set(self): self.tls = True
    def will_set(self, topic, payload, qos, retain): self.will = (topic, payload, qos, retain)
    def reconnect_delay_set(self, a, b): pass

    def connect(self, host, port, keepalive):
        if FakeClient.refuse:
            raise FakeClient.refuse
        self.addr = (host, port)

    def loop_start(self):
        self.on_connect(self, None, None, Rc(FakeClient.connack), None)

    def loop(self, timeout):
        self.on_connect(self, None, None, Rc(FakeClient.connack), None)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return type("Info", (), {"wait_for_publish": lambda self, t=None: None})()

    def disconnect(self): self.disconnected = True
    def loop_stop(self): self.stopped = True


@pytest.fixture(autouse=True)
def fresh():
    FakeClient.instances, FakeClient.refuse, FakeClient.connack = [], None, 0


def store(tmp_path, env=None, **saved):
    path = tmp_path / "mqtt.json"
    if saved:
        path.write_text(json.dumps(saved))
    return mqtt.MqttStore(str(path), env=env or {}, site="Home")


def until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- settings
@pytest.mark.parametrize("patch,message", [
    ({"host": ""}, "Enter the broker’s address."),
    ({"host": "mqtt://broker"}, "Use just the IP address"),
    ({"host": "broker:1883"}, "Use just the IP address"),
    ({"port": 0}, "The port must be"),
    ({"port": "1883"}, "The port must be"),
    ({"prefix": "solis/#"}, "The topic prefix"),
    ({"prefix": "solis/+/x"}, "The topic prefix"),
    ({"interval_s": 5}, "Send every"),
])
def test_bad_settings_are_refused_with_a_fixable_message(patch, message):
    with pytest.raises(mqtt.MqttError, match=message.split("’")[0]):
        mqtt.validate({**GOOD, **patch}, {})


def test_a_missing_password_keeps_the_saved_one_and_off_keeps_the_rest(tmp_path):
    s = store(tmp_path)
    s.set(GOOD)
    s.set({**GOOD, "password": None, "interval_s": 30})
    assert s.config()["password"] == "pw" and s.config()["interval_s"] == 30
    s.set({"enabled": False})
    assert s.config()["enabled"] is False and s.config()["host"] == "192.168.0.20"
    assert mqtt.validate({**GOOD, "username": "", "password": None}, s.config())["password"] == ""   # no user: no password


def test_the_file_wins_over_the_environment_is_private_and_the_password_stays_inside(tmp_path):
    env = {"MQTT_HOST": "Broker.lan", "MQTT_USERNAME": "env", "MQTT_PASSWORD": "envpw", "MQTT_INTERVAL": "30"}
    s = store(tmp_path, env)
    assert s.source == "env" and s.config()["host"] == "broker.lan" and s.config()["prefix"] == "solis/home"
    assert s.config()["interval_s"] == 30 and s.config()["discovery"] is True
    s.set(GOOD)
    assert s.source == "saved" and s.config()["host"] == "192.168.0.20"
    assert stat.S_IMODE(os.stat(tmp_path / "mqtt.json").st_mode) == 0o600
    pub = s.public()
    assert "password" not in pub and pub["password_set"] is True
    assert mqtt.MqttStore(str(tmp_path / "mqtt.json"), env=env, site="Home").config()["host"] == "192.168.0.20"


def test_nothing_set_means_off(tmp_path):
    s = store(tmp_path)
    assert s.source == "unset" and s.config()["enabled"] is False and s.public()["password_set"] is False


# ---------------------------------------------------------------- messages
def test_the_state_message_carries_the_readings_and_what_the_dashboard_works_out():
    active = [{"kind": "alarm", "name": "Battery over-temperature"}]
    msg = mqtt.build_state(VALS, active, 6.34, "2026-11-13T21:00:00+02:00", 1794600000)
    assert msg["battery_soc"] == 73 and msg["battery_power"] == 1130 and msg["pv_power"] == 2410
    assert msg["grid_voltage"] == 231.0 and msg["grid_frequency"] == 50.01 and msg["battery_temperature"] == 21.4
    assert msg["load_today"] == 12.9 and msg["grid_import_total"] == 2210 and msg["grid_export_today"] is None
    assert msg["grid_connected"] is True and msg["alarms"] == ["Battery over-temperature"]
    assert msg["backup_runtime_h"] == 6.3 and msg["next_outage"] == "2026-11-13T21:00:00+02:00" and msg["online"] is True
    assert mqtt.build_state(VALS, [{"kind": "outage", "name": "Grid outage"}], None, None, 0)["grid_connected"] is False


def test_discovery_makes_one_device_with_energy_sensors_home_assistant_understands():
    cfg = mqtt.validate(GOOD, {})
    configs = dict(mqtt.discovery(cfg, "Home", {"inverter": {"serial": "1234567890123456", "model_code": "0x3173", "dsp_version": "0x0013", "hmi_version": "0x0026"}}))
    assert len(configs) == len(mqtt.SENSORS) + len(mqtt.BINARY)
    soc = configs["homeassistant/sensor/solis_home/battery_soc/config"]
    assert soc["unique_id"] == "solis_home_battery_soc" and soc["device_class"] == "battery" and soc["unit_of_measurement"] == "%"
    assert soc["state_topic"] == "solis/home/state" and soc["availability_topic"] == "solis/home/status"
    assert soc["value_template"] == "{{ value_json.battery_soc }}"
    assert soc["device"] == {"identifiers": ["solis_home"], "name": "Solis · Home", "manufacturer": "Solis", "model": "Hybrid inverter",
                             "model_id": "0x3173", "serial_number": "1234567890123456", "sw_version": "DSP 0x0013 · HMI 0x0026"}
    imp = configs["homeassistant/sensor/solis_home/grid_import_today/config"]
    assert imp["device_class"] == "energy" and imp["state_class"] == "total_increasing" and imp["unit_of_measurement"] == "kWh"
    assert configs["homeassistant/sensor/solis_home/grid_import_total/config"]["enabled_by_default"] is False
    grid = configs["homeassistant/binary_sensor/solis_home/grid/config"]
    assert grid["device_class"] == "power" and "grid_connected" in grid["value_template"]
    assert "unit_of_measurement" not in configs["homeassistant/sensor/solis_home/next_outage/config"]
    assert all(c["unique_id"].startswith("solis_home_") for c in configs.values())
    assert len({c["unique_id"] for c in configs.values()}) == len(configs)


def test_the_node_follows_the_prefix():
    assert mqtt.node_id("solis/home") == "solis_home" and mqtt.node_id("solis/Romankiv-2") == "solis_romankiv_2"
    assert mqtt.node_id("house/inverter") == "solis_house_inverter"
    assert mqtt.default_prefix("Дача") == "solis/home" and mqtt.default_prefix("Home 2") == "solis/home-2"


# ---------------------------------------------------------------- the publisher
def start(tmp_path, cfg=GOOD, device=lambda: None, clock=None):
    s = store(tmp_path)
    if cfg:
        s.set(cfg)
    stop = threading.Event()
    p = mqtt.MqttPublisher(s, site="Home", device=device, factory=FakeClient, **({"monotonic": clock} if clock else {}))
    p.run_in_background(stop)
    return p, stop


def topics_of(client):
    return [t for t, *_ in client.published]


def test_it_connects_says_online_sends_discovery_and_the_state_on_its_interval(tmp_path):
    now = [100.0]
    p, stop = start(tmp_path, clock=lambda: now[0])
    try:
        assert until(lambda: p.status()["state"] == "connected")
        c = FakeClient.instances[0]
        assert c.client_id == "solis-monitor-solis_home" and c.auth == ("solis", "pw") and c.addr == ("192.168.0.20", 1883)
        assert c.will == ("solis/home/status", "offline", 1, True)
        assert c.published[0] == ("solis/home/status", "online", 1, True)
        assert sum(t.startswith("homeassistant/") for t in topics_of(c)) == len(mqtt.SENSORS) + len(mqtt.BINARY)
        p.publish_state(VALS, [])
        now[0] += 5
        p.publish_state(VALS, [])                                  # too soon: the 10-second interval
        now[0] += 5
        p.publish_state(VALS, [])
        states = [x for x in c.published if x[0] == "solis/home/state"]
        assert len(states) == 2 and states[0][3] is True and json.loads(states[0][1])["battery_soc"] == 73
        p.publish_state(VALS, [], force=True)                       # an event: at once
        assert len([x for x in c.published if x[0] == "solis/home/state"]) == 3
        assert p.status()["sent"] == 3 and p.view()["last_message"]["pv_power"] == 2410
        assert "password" not in p.view()
    finally:
        stop.set()


def test_events_wait_for_the_broker_and_go_out_when_it_answers(tmp_path):
    FakeClient.refuse = ConnectionRefusedError()
    p, stop = start(tmp_path)
    try:
        assert until(lambda: p.status()["state"] == "error")
        assert p.status()["error"] == "Connection refused"
        p.publish_event({"kind": "outage", "key": "grid", "name": "Grid outage", "severity": "warning", "state": "start", "soc": 80, "load": 600})
        p.publish_state(VALS, [])                                   # no broker: nothing sent, the poll loop carries on
        FakeClient.refuse = None
        p.reconfigure()
        assert until(lambda: p.status()["state"] == "connected")
        c = FakeClient.instances[-1]
        ev = [json.loads(x[1]) for x in c.published if x[0] == "solis/home/event"]
        assert ev and ev[0]["state"] == "start" and ev[0]["kind"] == "outage" and ev[0]["soc"] == 80
        assert any(x[0] == "solis/home/state" for x in c.published)           # the last state, sent on connect
    finally:
        stop.set()


def test_a_wrong_password_is_said_plainly(tmp_path):
    FakeClient.connack = 5
    p, stop = start(tmp_path)
    try:
        assert until(lambda: p.status()["state"] == "error")
        assert p.status()["error"] == "Wrong username or password"
    finally:
        stop.set()


def test_turning_it_off_says_offline_and_disconnects(tmp_path):
    p, stop = start(tmp_path)
    try:
        assert until(lambda: p.status()["state"] == "connected")
        c = FakeClient.instances[0]
        p.store.set({"enabled": False})
        p.reconfigure()
        assert p.status()["state"] == "off"                          # said at once, before the thread gets to it
        assert until(lambda: c.stopped)
        assert c.published[-1] == ("solis/home/status", "offline", 1, True) and c.disconnected
        p.publish_state(VALS, [])
        assert len(FakeClient.instances) == 1
    finally:
        stop.set()


def test_discovery_off_removes_the_entities_and_the_serial_updates_them(tmp_path):
    info = {"inverter": None}
    p, stop = start(tmp_path, {**GOOD, "discovery": False}, device=lambda: info if info["inverter"] else None)
    try:
        assert until(lambda: p.status()["state"] == "connected")
        c = FakeClient.instances[0]
        cleared = [x for x in c.published if x[0].startswith("homeassistant/")]
        assert cleared and all(x[1] == "" and x[3] is True for x in cleared)
        p.store.set({**GOOD, "password": None})
        p.reconfigure()
        assert until(lambda: len(FakeClient.instances) == 2 and p.status()["state"] == "connected")
        c2 = FakeClient.instances[1]
        n = len(c2.published)
        info["inverter"] = {"serial": "1234567890123456"}
        p.publish_state(VALS, [], force=True)                       # the serial is known now: discovery again, with it
        again = [json.loads(x[1]) for x in c2.published[n:] if x[0].startswith("homeassistant/")]
        assert again and again[0]["device"]["serial_number"] == "1234567890123456"
    finally:
        stop.set()


def test_test_connection_uses_its_own_client_and_reports_the_time(tmp_path):
    p = mqtt.MqttPublisher(store(tmp_path), site="Home", factory=FakeClient)
    r = p.test(GOOD)
    assert r["ok"] is True and r["error"] is None and isinstance(r["ms"], int)
    assert FakeClient.instances[-1].client_id.endswith("-test") and FakeClient.instances[-1].disconnected
    FakeClient.connack = 4
    assert p.test(GOOD) == {"ok": False, "error": "Wrong username or password", "ms": None}
    FakeClient.connack, FakeClient.refuse = 0, OSError("no route")
    assert p.test(GOOD)["error"] == "Broker not reachable"
    assert p.test({**GOOD, "host": ""})["error"] == "Enter the broker’s address."


def test_the_internal_api_needs_the_token_and_never_returns_the_password(tmp_path):
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import api
    import control
    ctl = control.Controller(None, control.Config({"INFLUX_TOKEN": "secret"}), audit=lambda a, **f: audits.append((a, f)))
    audits = []
    ctl.mqtt = mqtt.MqttPublisher(store(tmp_path), site="Home", factory=FakeClient)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), api.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def call(path, body=None, token="secret"):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert call("/mqtt", token="wrong")[0] == 401
        status, body = call("/mqtt", GOOD)
        assert status == 200 and body["host"] == "192.168.0.20" and body["password_set"] is True
        assert "pw" not in json.dumps(body) and "pw" not in json.dumps(audits)
        assert audits[-1][0] == "mqtt" and "192.168.0.20:1883 solis/home" in audits[-1][1]["summary"]
        assert call("/mqtt", {**GOOD, "host": ""})[0] == 400
        assert call("/mqtt", {**GOOD, "subscribe": "#"})[0] == 400
        assert call("/mqtt/test", GOOD)[1]["ok"] is True
    finally:
        srv.shutdown()
