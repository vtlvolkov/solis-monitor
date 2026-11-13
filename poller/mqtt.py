"""MQTT: the readings and the outage/alarm events, published to the owner's broker for Home Assistant, Node-RED or a
smart relay. Publishing only: nothing is ever subscribed to, so no message can reach the inverter.

Topics, under a prefix (default solis/<site>):
  <prefix>/state   one JSON message with the latest readings, every MQTT interval (retained)
  <prefix>/event   an outage or alarm starting or ending, at once (QoS 1, not retained)
  <prefix>/status  "online" / "offline" (retained; the broker sets offline itself if we vanish: the will)
  homeassistant/<component>/<node>/<key>/config   Home Assistant discovery, once per connection (retained)

The connection runs in a background thread. A broker that is down never slows the poll loop: the latest state just
replaces the previous one, and events wait in a short queue (the oldest dropped) until the broker is back.
"""
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as paho

log = logging.getLogger("solis-poller")

INTERVALS = (10, 30, 60)
DISCOVERY_PREFIX = "homeassistant"
HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
PREFIX = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")


class MqttError(ValueError):
    """A setting the user can fix; the message is shown on the Settings card (and translated there)."""


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "home"


def default_prefix(site: str) -> str:
    return f"solis/{slug(site)}"


def _flag(v, default=False) -> bool:
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def validate(raw: dict, current: dict) -> dict:
    """A change from the Settings page -> the full config. An absent or null password keeps the saved one."""
    if not isinstance(raw, dict):
        raise MqttError("body must be a JSON object")
    if not raw.get("enabled"):
        return {**current, "enabled": False}
    host = str(raw.get("host") or "").strip().lower()
    if not host:
        raise MqttError("Enter the broker’s address.")
    if len(host) > 253 or not HOST.match(host):
        raise MqttError("Use just the IP address or host name of the broker, without mqtt://, a port or spaces.")
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MqttError("The port must be between 1 and 65535.")
    prefix = str(raw.get("prefix") or "").strip().strip("/")
    if len(prefix) > 100 or not PREFIX.match(prefix):
        raise MqttError("The topic prefix may use letters, digits, - _ and /, without + or #.")
    interval = raw.get("interval_s")
    if interval not in INTERVALS or isinstance(interval, bool):
        raise MqttError("Send every 10, 30 or 60 seconds.")
    username = str(raw.get("username") or "").strip()[:200]
    password = raw.get("password")
    if password is None:
        password = current.get("password", "") if username else ""
    elif not isinstance(password, str) or len(password) > 500:
        raise MqttError("body must be a JSON object")
    return {"enabled": True, "host": host, "port": port, "username": username, "password": password,
            "tls": bool(raw.get("tls")), "prefix": prefix, "interval_s": interval, "discovery": bool(raw.get("discovery", True)),
            "discovery_prefix": current.get("discovery_prefix") or DISCOVERY_PREFIX}


class MqttStore:
    """Saved from the Settings page in a small JSON file (the poller's /data volume), which wins over MQTT_* in .env.
    The password is kept there like it would be in .env; it is never sent back to the browser."""

    def __init__(self, path: str | None, env=os.environ, site: str = ""):
        self.path = path or None
        self.lock = threading.Lock()
        host = (env.get("MQTT_HOST") or "").strip().lower()
        tls = _flag(env.get("MQTT_TLS"))
        try:
            port = int(env.get("MQTT_PORT") or (8883 if tls else 1883))
        except ValueError:
            port = 8883 if tls else 1883
        try:
            interval = int(env.get("MQTT_INTERVAL") or 10)
        except ValueError:
            interval = 10
        self.env = {"enabled": bool(host), "host": host, "port": port, "username": (env.get("MQTT_USERNAME") or "").strip(),
                    "password": env.get("MQTT_PASSWORD") or "", "tls": tls,
                    "prefix": (env.get("MQTT_PREFIX") or default_prefix(site)).strip().strip("/"),
                    "interval_s": interval if interval in INTERVALS else 10,
                    "discovery": _flag(env.get("MQTT_DISCOVERY"), True),
                    "discovery_prefix": (env.get("MQTT_DISCOVERY_PREFIX") or DISCOVERY_PREFIX).strip().strip("/")}
        self.saved = self._load()

    def _load(self) -> dict | None:
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            return {**self.env, **{k: raw[k] for k in self.env if k in raw}}
        except (OSError, ValueError, TypeError) as exc:
            log.error("MQTT settings file %s is unreadable (%s): using MQTT_* from the environment", self.path, exc)
            return None

    def config(self) -> dict:
        with self.lock:
            return dict(self.saved if self.saved is not None else self.env)

    @property
    def source(self) -> str:
        return "saved" if self.saved is not None else "env" if self.env["enabled"] else "unset"

    def public(self) -> dict:
        c = self.config()
        out = {k: v for k, v in c.items() if k != "password"}
        return {**out, "password_set": bool(c.get("password")), "source": self.source}

    def set(self, raw: dict) -> dict:
        new = validate(raw, self.config())
        with self.lock:
            if self.path:
                tmp = self.path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # it holds the broker password
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(new, f)
                os.replace(tmp, self.path)
            self.saved = new
        return new


# ---------------------------------------------------------------- what is sent
# state key: (source field, name, unit, device class, state class, precision, extra discovery fields)
SENSORS = [
    ("battery_soc", "battery_soc", "Battery state of charge", "%", "battery", "measurement", 0, {}),
    ("battery_power", "battery_power_signed", "Battery power", "W", "power", "measurement", 0, {}),
    ("pv_power", "pv_power", "PV power", "W", "power", "measurement", 0, {}),
    ("grid_power", "grid_power", "Grid power", "W", "power", "measurement", 0, {}),
    ("house_load_power", "house_load_power", "House load", "W", "power", "measurement", 0, {}),
    ("backup_load_power", "backup_load_power", "Backup load", "W", "power", "measurement", 0, {}),
    ("total_load_power", "total_load_power", "Total load", "W", "power", "measurement", 0, {}),
    ("grid_voltage", "grid_voltage", "Grid voltage", "V", "voltage", "measurement", 1, {}),
    ("grid_frequency", "grid_frequency", "Grid frequency", "Hz", "frequency", "measurement", 2, {}),
    ("backup_voltage", "backup_voltage", "Backup voltage", "V", "voltage", "measurement", 1, {}),
    ("battery_voltage", "battery_voltage", "Battery voltage", "V", "voltage", "measurement", 1, {}),
    ("battery_current", "battery_current", "Battery current", "A", "current", "measurement", 1, {}),
    ("battery_soh", "battery_soh", "Battery state of health", "%", None, "measurement", 0, {"icon": "mdi:battery-heart-variant"}),
    ("battery_temperature", "battery_temperature", "Battery temperature", "°C", "temperature", "measurement", 1, {}),
    ("inverter_temperature", "inverter_temperature", "Inverter temperature", "°C", "temperature", "measurement", 1, {"entity_category": "diagnostic"}),
    ("backup_runtime_h", None, "Backup runtime", "h", "duration", "measurement", 1, {"icon": "mdi:timer-sand"}),
    ("pv_energy_today", "pv_energy_today", "PV energy today", "kWh", "energy", "total_increasing", 1, {}),
    ("grid_import_today", "grid_import_today", "Grid import today", "kWh", "energy", "total_increasing", 1, {}),
    ("grid_export_today", "grid_export_today", "Grid export today", "kWh", "energy", "total_increasing", 1, {}),
    ("battery_charge_today", "battery_charge_today", "Battery charged today", "kWh", "energy", "total_increasing", 1, {}),
    ("battery_discharge_today", "battery_discharge_today", "Battery discharged today", "kWh", "energy", "total_increasing", 1, {}),
    ("load_today", "load_consumption_today", "Load today", "kWh", "energy", "total_increasing", 1, {}),
    ("pv_energy_total", "pv_energy_total", "PV energy, lifetime", "kWh", "energy", "total_increasing", 0, {"enabled_by_default": False}),
    ("grid_import_total", "grid_import_total", "Grid import, lifetime", "kWh", "energy", "total_increasing", 0, {"enabled_by_default": False}),
    ("grid_export_total", "grid_export_total", "Grid export, lifetime", "kWh", "energy", "total_increasing", 0, {"enabled_by_default": False}),
    ("next_outage", None, "Next planned outage", None, "timestamp", None, None, {"icon": "mdi:transmission-tower-off"}),
]
BINARY = [
    ("grid", "Grid", "power", "{{ 'ON' if value_json.grid_connected else 'OFF' }}"),
    ("alarm", "Alarm", "problem", "{{ 'ON' if value_json.alarms else 'OFF' }}"),
]


def _round(v, digits):
    if v is None or isinstance(v, bool):
        return v
    try:
        return round(float(v), digits) if digits else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def build_state(vals: dict, active: list[dict], runtime_h: float | None, next_outage: str | None, now: float) -> dict:
    """The <prefix>/state message: readings by their published names, plus what the dashboard works out."""
    msg = {"time": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"), "online": True,
           "grid_connected": not any(e["kind"] == "outage" for e in active),
           "alarms": [e["name"] for e in active if e["kind"] == "alarm"]}
    for key, src, *_rest in SENSORS:
        if src is not None:
            digits = _rest[4]
            msg[key] = _round(vals.get(src), digits)
    msg["backup_runtime_h"] = None if runtime_h is None else round(runtime_h, 1)
    msg["next_outage"] = next_outage
    return msg


def event_message(ev: dict, now: float) -> dict:
    return {"time": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            **{k: ev.get(k) for k in ("kind", "key", "name", "severity", "state", "soc", "load")}}


def node_id(prefix: str) -> str:
    return "solis_" + re.sub(r"[^a-z0-9]+", "_", prefix.lower().removeprefix("solis/")).strip("_")


def discovery(cfg: dict, site: str, device_info: dict | None) -> list[tuple[str, dict]]:
    """Home Assistant MQTT discovery: (topic, config) for every sensor; the entities share one device."""
    node, prefix, dp = node_id(cfg["prefix"]), cfg["prefix"], cfg.get("discovery_prefix") or DISCOVERY_PREFIX
    inv = (device_info or {}).get("inverter") or {}
    device = {"identifiers": [node], "name": f"Solis · {site}" if site else "Solis inverter", "manufacturer": "Solis",
              "model": "Hybrid inverter"}
    if inv.get("model_code"):
        device["model_id"] = inv["model_code"]
    if inv.get("serial"):
        device["serial_number"] = inv["serial"]
    if inv.get("dsp_version"):
        device["sw_version"] = f"DSP {inv['dsp_version']} · HMI {inv.get('hmi_version', '?')}"
    common = {"state_topic": f"{prefix}/state", "availability_topic": f"{prefix}/status", "device": device,
              "origin": {"name": "Solis Monitor", "url": "https://github.com/vtlvolkov/solis-monitor"}}
    out = []
    for key, _src, name, unit, dclass, sclass, digits, extra in SENSORS:
        c = {**common, "name": name, "unique_id": f"{node}_{key}", "value_template": f"{{{{ value_json.{key} }}}}", **extra}
        if unit:
            c["unit_of_measurement"] = unit
        if dclass:
            c["device_class"] = dclass
        if sclass:
            c["state_class"] = sclass
        if digits is not None:
            c["suggested_display_precision"] = digits
        out.append((f"{dp}/sensor/{node}/{key}/config", c))
    for key, name, dclass, tmpl in BINARY:
        out.append((f"{dp}/binary_sensor/{node}/{key}/config",
                    {**common, "name": name, "unique_id": f"{node}_{key}", "device_class": dclass, "value_template": tmpl}))
    return out


def topics(cfg: dict) -> dict:
    p = cfg["prefix"]
    return {"state": f"{p}/state", "event": f"{p}/event", "availability": f"{p}/status",
            "discovery": f"{cfg.get('discovery_prefix') or DISCOVERY_PREFIX}/sensor/{node_id(p)}/…/config" if cfg.get("discovery") else None}


# ---------------------------------------------------------------- the connection
def describe(exc: BaseException) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return "Connection refused"
    if isinstance(exc, ssl.SSLError):
        return "TLS handshake failed"
    if isinstance(exc, (socket.timeout, TimeoutError, socket.gaierror, OSError)):
        return "Broker not reachable"
    return str(exc)[:120] or exc.__class__.__name__


def describe_rc(rc) -> str:
    value = getattr(rc, "value", rc)
    if value in (4, 5, 134, 135):
        return "Wrong username or password"
    return f"Refused by the broker: {rc}"


def paho_client(client_id: str):
    return paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=paho.MQTTv311)


class MqttPublisher:
    def __init__(self, store: MqttStore, site: str = "", device=lambda: None, factory=paho_client,
                 clock=time.time, monotonic=time.monotonic):
        self.store, self.site, self.device, self.factory, self.clock, self.monotonic = store, site, device, factory, clock, monotonic
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.client = None
        self.cfg = store.config()
        self.state_name, self.error = ("connecting" if self.cfg["enabled"] else "off"), None
        self.connected = False
        self.sent, self.dropped = 0, 0
        self.last_at: float | None = None                  # monotonic time of the last state message
        self.last_wall: float | None = None
        self.last_message: dict | None = None
        self.pending: deque = deque(maxlen=50)             # events while the broker is away
        self.serial_sent: str | None = None

    # ------------------------------------------------ settings and status (the internal API)
    def reconfigure(self) -> None:
        """The settings changed: the connection thread starts again with them (the status says so at once)."""
        enabled = self.store.config()["enabled"]
        self._set("connecting" if enabled else "off", None)
        self.wake.set()

    def status(self) -> dict:
        with self.lock:
            age = None if self.last_wall is None else max(0.0, self.clock() - self.last_wall)
            return {"state": self.state_name, "error": self.error, "last_publish_age_s": None if age is None else round(age, 1),
                    "sent": self.sent, "dropped": self.dropped}

    def view(self) -> dict:
        cfg = self.store.config()
        return {**self.store.public(), "status": self.status(), "topics": topics(cfg),
                "last_message": self.last_message if cfg["enabled"] else None}

    def _set(self, state: str, error: str | None) -> None:
        with self.lock:
            self.state_name, self.error = state, error

    # ------------------------------------------------ from the poll loop
    def publish_state(self, vals: dict, active: list[dict], runtime_h=None, next_outage=None, force=False) -> None:
        cfg = self.cfg
        if not cfg["enabled"]:
            return
        now = self.monotonic()
        if not force and self.last_at is not None and now - self.last_at < cfg["interval_s"] - 0.5:
            return
        msg = build_state(vals, active, runtime_h, next_outage, self.clock())
        self.last_at, self.last_message = now, msg
        with self.lock:
            client = self.client if self.connected else None
        if client is not None:
            client.publish(f"{cfg['prefix']}/state", json.dumps(msg), qos=0, retain=True)
            with self.lock:
                self.sent += 1
                self.last_wall = self.clock()
        self._refresh_discovery(client, cfg)

    def publish_event(self, ev: dict) -> None:
        if not self.cfg["enabled"]:
            return
        msg = event_message(ev, self.clock())
        with self.lock:
            client = self.client if self.connected else None
            if client is None:
                if len(self.pending) == self.pending.maxlen:
                    self.dropped += 1
                self.pending.append(msg)
                return
        client.publish(f"{self.cfg['prefix']}/event", json.dumps(msg), qos=1, retain=False)
        with self.lock:
            self.sent += 1

    def _refresh_discovery(self, client, cfg) -> None:
        """The inverter's serial arrives from the device read a little after the first connection: send it then."""
        if client is None or not cfg.get("discovery"):
            return
        serial = ((self.device() or {}).get("inverter") or {}).get("serial")
        if serial and serial != self.serial_sent:
            self._send_discovery(client, cfg)

    def _send_discovery(self, client, cfg) -> None:
        info = self.device()
        for topic, conf in discovery(cfg, self.site, info):
            client.publish(topic, json.dumps(conf), qos=1, retain=True)
        self.serial_sent = ((info or {}).get("inverter") or {}).get("serial")

    def _clear_discovery(self, client, cfg) -> None:
        for topic, _conf in discovery(cfg, self.site, None):
            client.publish(topic, "", qos=1, retain=True)         # an empty retained config removes the entity

    # ------------------------------------------------ the connection thread
    def _build(self, cfg, suffix=""):
        client = self.factory(f"solis-monitor-{node_id(cfg['prefix'])}{suffix}")
        if cfg.get("username"):
            client.username_pw_set(cfg["username"], cfg.get("password") or None)
        if cfg.get("tls"):
            client.tls_set()
        return client

    def _on_connect(self, client, _userdata, _flags, rc, _props=None):
        if getattr(rc, "is_failure", False) or (isinstance(rc, int) and rc != 0):
            with self.lock:
                self.connected = False
            self._set("error", describe_rc(rc))
            log.warning("MQTT: %s refused the connection: %s", self.cfg["host"], rc)
            return
        cfg = self.cfg
        client.publish(f"{cfg['prefix']}/status", "online", qos=1, retain=True)
        if cfg.get("discovery"):
            self._send_discovery(client, cfg)
        else:                                            # turned off (perhaps before a restart): take the entities away
            self._clear_discovery(client, cfg)
            self.serial_sent = None
        if self.last_message:
            client.publish(f"{cfg['prefix']}/state", json.dumps(self.last_message), qos=0, retain=True)
        with self.lock:
            backlog, self.pending = list(self.pending), deque(maxlen=self.pending.maxlen)
            self.connected = True
        for msg in backlog:
            client.publish(f"{cfg['prefix']}/event", json.dumps(msg), qos=1, retain=False)
        self._set("connected", None)
        log.info("MQTT: connected to %s:%s, publishing under %s/", cfg["host"], cfg["port"], cfg["prefix"])

    def _on_disconnect(self, _client, _userdata, _flags, _rc, _props=None):
        with self.lock:
            was = self.connected
            self.connected = False
        if was and self.state_name != "error":
            self._set("connecting", "Broker not reachable")
            log.warning("MQTT: lost the connection to %s; reconnecting", self.cfg["host"])

    def run_in_background(self, stop: threading.Event) -> threading.Thread:
        t = threading.Thread(target=self._run, args=(stop,), daemon=True, name="mqtt")
        t.start()
        return t

    def _run(self, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            self.wake.clear()
            cfg = self.cfg = self.store.config()
            if not cfg["enabled"]:
                self._set("off", None)
                while not stop.is_set() and not self.wake.wait(1):
                    pass
                continue
            client = self._build(cfg)
            client.on_connect, client.on_disconnect = self._on_connect, self._on_disconnect
            client.will_set(f"{cfg['prefix']}/status", "offline", qos=1, retain=True)
            client.reconnect_delay_set(1, 60)
            self._set("connecting", None)
            try:
                client.connect(cfg["host"], cfg["port"], keepalive=60)
            except Exception as exc:                     # refused, no route, DNS, TLS ...
                self._set("error", describe(exc))
                log.warning("MQTT: cannot connect to %s:%s: %s", cfg["host"], cfg["port"], exc)
                deadline = self.monotonic() + backoff
                while not stop.is_set() and not self.wake.is_set() and self.monotonic() < deadline:
                    stop.wait(0.5)
                backoff = min(backoff * 2, 60.0)
                continue
            backoff = 1.0
            with self.lock:
                self.client = client
            client.loop_start()                          # paho reconnects by itself if the broker goes away later
            while not stop.is_set() and not self.wake.wait(1):
                pass
            with self.lock:
                self.client, self.connected = None, False
            try:
                info = client.publish(f"{cfg['prefix']}/status", "offline", qos=1, retain=True)
                info.wait_for_publish(2)
            except Exception:                            # not connected at the moment: the will says it
                pass
            try:
                client.disconnect()
            finally:
                client.loop_stop()

    # ------------------------------------------------ Test connection on the Settings card
    def test(self, raw: dict, timeout: float = 5.0) -> dict:
        try:
            cfg = validate({**raw, "enabled": True}, self.store.config())
        except MqttError as exc:
            return {"ok": False, "error": str(exc), "ms": None}
        client = self._build(cfg, "-test")                 # its own id: the same one would push our connection off
        result: dict = {}
        client.on_connect = lambda _c, _u, _f, rc, _p=None: result.update(rc=rc)
        started = time.monotonic()
        try:
            client.connect(cfg["host"], cfg["port"], keepalive=10)
            while "rc" not in result and time.monotonic() - started < timeout:
                client.loop(timeout=0.2)
        except Exception as exc:
            return {"ok": False, "error": describe(exc), "ms": None}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
        if "rc" not in result:
            return {"ok": False, "error": "Broker not reachable", "ms": None}
        rc = result["rc"]
        if getattr(rc, "is_failure", False) or (isinstance(rc, int) and rc != 0):
            return {"ok": False, "error": describe_rc(rc), "ms": None}
        return {"ok": True, "error": None, "ms": round((time.monotonic() - started) * 1000)}
