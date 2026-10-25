#!/usr/bin/env python3
"""
Solis hybrid inverter -> InfluxDB 2 poller (Modbus TCP).

Reads input registers (FC04) every POLL_INTERVAL seconds and writes one point
to measurement "solis". Also serves the internal control API (control.py) on the
same Modbus connection, so this process stays the inverter's only client.
Configuration comes from environment variables (see docker-compose.yml / .env).
"""
import inspect
import logging
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from pymodbus.client import ModbusTcpClient

import control
import device
import faults
import notify

INVERTER_HOST = os.getenv("INVERTER_HOST", "")
INVERTER_PORT = int(os.getenv("INVERTER_PORT", "502"))
MODBUS_UNIT = int(os.getenv("MODBUS_UNIT", "1"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "10"))

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "home")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "solis")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("solis-poller")

# field: (register, type, scale)
# Verified against a live Solis hybrid on 2026-10-18 (energy balance checks out); addresses and
# scales per the Solis ESINV-33000ID protocol.
REGISTERS = {
    "pv1_voltage":            (33049, "u16", 0.1),
    "pv1_current":            (33050, "u16", 0.1),
    "pv2_voltage":            (33051, "u16", 0.1),
    "pv2_current":            (33052, "u16", 0.1),
    "pv_power":               (33057, "u32", 1),
    "pv_energy_today":        (33035, "u16", 0.1),
    "pv_energy_yesterday":    (33036, "u16", 0.1),
    "pv_energy_month":        (33031, "u32", 1),
    "pv_energy_year":         (33037, "u32", 1),
    "pv_energy_total":        (33029, "u32", 1),
    "dc_bus_voltage":         (33071, "u16", 0.1),
    "grid_voltage":           (33073, "u16", 0.1),
    "grid_frequency":         (33094, "u16", 0.01),
    "inverter_ac_power":      (33079, "s32", 1),
    "inverter_temperature":   (33093, "s16", 0.1),
    "status_code":            (33095, "u16", 1),     # see faults.STATUS
    "fault_code_01":          (33116, "u16", 1),     # bitfields, see faults.FAULT_BITS
    "fault_code_02":          (33117, "u16", 1),
    "fault_code_03":          (33118, "u16", 1),
    "fault_code_04":          (33119, "u16", 1),
    "fault_code_05":          (33120, "u16", 1),
    "operating_status":       (33121, "u16", 1),
    "meter_power":            (33130, "s32", 1),
    "battery_voltage":        (33133, "u16", 0.1),
    "battery_current":        (33134, "s16", 0.1),
    "battery_direction":      (33135, "u16", 1),   # 0 = charging, 1 = discharging
    "backup_voltage":         (33137, "u16", 0.1),
    "backup_current":         (33138, "u16", 0.1),
    "battery_soc":            (33139, "u16", 1),
    "battery_soh":            (33140, "u16", 1),
    "bms_fault_01":           (33145, "u16", 1),
    "bms_fault_02":           (33146, "u16", 1),
    "house_load_power":       (33147, "u16", 1),
    "backup_load_power":      (33148, "u16", 1),
    "battery_power":          (33149, "s32", 1),
    "battery_charge_total":   (33161, "u32", 1),
    "battery_charge_today":   (33163, "u16", 0.1),
    "battery_charge_yesterday": (33164, "u16", 0.1),
    "battery_discharge_total": (33165, "u32", 1),
    "battery_discharge_today": (33167, "u16", 0.1),
    "battery_discharge_yesterday": (33168, "u16", 0.1),
    "grid_import_total":      (33169, "u32", 1),
    "grid_import_today":      (33171, "u16", 0.1),
    "grid_import_yesterday":  (33172, "u16", 0.1),
    "grid_export_total":      (33173, "u32", 1),
    "grid_export_today":      (33175, "u16", 0.1),
    "grid_export_yesterday":  (33176, "u16", 0.1),
    "load_total":             (33177, "u32", 1),
    "load_consumption_today": (33179, "u16", 0.1),
    "load_yesterday":         (33180, "u16", 0.1),
}

# Solis protocol: at most 50 registers per request, more than 300 ms between frames.
MAX_BLOCK = 50
FRAME_GAP_S = 0.35

# pymodbus renamed the unit-id kwarg over versions: slave -> device_id
_params = inspect.signature(ModbusTcpClient.read_input_registers).parameters
UNIT_KW = "device_id" if "device_id" in _params else "slave"


def _width(typ):
    return 2 if typ in ("u32", "s32") else 1


def build_blocks():
    """Group registers into contiguous read blocks (max 50 regs per the Solis protocol, gaps <= 15)."""
    spans = sorted((r[0], r[0] + _width(r[1])) for r in REGISTERS.values())
    blocks, cur = [], None
    for s, e in spans:
        if cur and s - cur[1] <= 15 and e - cur[0] <= MAX_BLOCK:
            cur[1] = max(cur[1], e)
        else:
            cur = [s, e]
            blocks.append(cur)
    return blocks


BLOCKS = build_blocks()


def decode(hi, lo, typ):
    if typ == "u16":
        return hi
    if typ == "s16":
        return hi - 0x10000 if hi & 0x8000 else hi
    val = (hi << 16) | lo
    if typ == "s32" and val & 0x80000000:
        val -= 0x100000000
    return val


def read_snapshot(read):
    """read(start, count) -> list of input registers (one paced Modbus request)."""
    raw = {}
    for s, e in BLOCKS:
        for i, v in enumerate(read(s, e - s)):
            raw[s + i] = v

    vals = {}
    for name, (reg, typ, scale) in REGISTERS.items():
        v = decode(raw[reg], raw.get(reg + 1, 0), typ) * scale
        vals[name] = round(float(v), 3)

    # Derived, sign-normalised fields for the dashboard
    sign = 1 if int(vals["battery_direction"]) == 0 else -1
    vals["battery_power_signed"] = sign * abs(vals["battery_power"])   # + charging, - discharging
    vals["grid_power"] = -vals["meter_power"]                          # + import, - export
    vals["total_load_power"] = vals["house_load_power"] + vals["backup_load_power"]
    return vals


class Inverter:
    """The single Modbus connection, shared by the poll loop and the control API."""

    def __init__(self, host=INVERTER_HOST, port=INVERTER_PORT, unit=MODBUS_UNIT):
        self.host, self.port, self.unit = host, port, unit
        self.client = ModbusTcpClient(host, port=port, timeout=3)
        self.lock = threading.Lock()
        self._last = 0.0

    @contextmanager
    def session(self):
        """One request at a time, spaced at least FRAME_GAP_S apart."""
        with self.lock:
            wait = FRAME_GAP_S - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            if not self.client.connected and not self.client.connect():
                raise ConnectionError(f"cannot connect to {self.host}:{self.port}")
            try:
                yield self.client
            except Exception:
                self.client.close()          # force a clean reconnect next time
                raise
            finally:
                self._last = time.monotonic()

    def snapshot(self):
        return read_snapshot(self.read_input)

    def read_holding(self, reg, count=1):
        with self.session() as c:
            rr = c.read_holding_registers(reg, count=count, **{UNIT_KW: self.unit})
            if rr.isError():
                raise IOError(f"read holding {reg} failed: {rr}")
            return rr.registers

    def read_input(self, reg, count=1):
        with self.session() as c:
            rr = c.read_input_registers(reg, count=count, **{UNIT_KW: self.unit})
            if rr.isError():
                raise IOError(f"read input {reg} failed: {rr}")
            return rr.registers

    def write_holding(self, reg, value):
        with self.session() as c:
            rr = c.write_register(reg, value, **{UNIT_KW: self.unit})
            if rr.isError():                 # some firmwares only accept FC16
                rr = c.write_registers(reg, [value], **{UNIT_KW: self.unit})
            if rr.isError():
                raise IOError(f"write {reg}={value} failed: {rr}")

    def write_block(self, reg, values):
        """FC16 (write multiple registers). Newer Solis firmware ignores some FC06 writes."""
        with self.session() as c:
            rr = c.write_registers(reg, list(values), **{UNIT_KW: self.unit})
            if rr.isError():
                raise IOError(f"write {reg}..{reg + len(values) - 1} failed: {rr}")

    def close(self):
        with self.lock:
            self.client.close()


def main():
    if not INFLUX_TOKEN:
        sys.exit("INFLUX_TOKEN is not set")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not INVERTER_HOST and not stop.is_set():          # a Pi prepared before it reaches its site
        log.error("INVERTER_HOST is not set: run deploy/pi/find-inverter.sh, or set it in .env and run "
                  "`docker compose up -d poller`")
        stop.wait(60)
    if stop.is_set():
        return

    influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    writer = influx.write_api(write_options=SYNCHRONOUS)
    inv = Inverter()

    def audit(action, **fields):
        log.warning("CONTROL %s %s", action, fields)
        try:
            p = Point("solis_control").tag("action", action)
            for k, v in fields.items():
                p = p.field(k, v if isinstance(v, (int, float, bool)) else str(v))
            writer.write(bucket=INFLUX_BUCKET, record=p)
        except Exception as exc:
            log.warning("audit write failed: %s", exc)

    ctl = control.Controller(inv, control.Config(), audit=audit)
    ctl.devices = device.DeviceReader(inv, ttl=90)
    server = control.serve(ctl)

    def keep_caches_warm():                       # device/settings reads take seconds; serve them from cache
        n = 0
        while not stop.is_set():
            try:
                ctl.devices.refresh()
                if n % 5 == 0:                        # settings every 5 minutes
                    ctl.settings()
            except Exception as exc:
                log.debug("cache refresh failed: %s", exc)
            n += 1
            stop.wait(60)
    threading.Thread(target=keep_caches_warm, daemon=True, name="cache-warm").start()

    log.info("Polling %s:%s unit %s every %ss -> %s/%s",
             INVERTER_HOST, INVERTER_PORT, MODBUS_UNIT, POLL_INTERVAL, INFLUX_URL, INFLUX_BUCKET)
    failures = 0
    tracker = faults.EventTracker()

    def battery_for_runtime():                    # (capacity kWh, SOC floor on backup) from cached device info
        d = ctl.devices.cached()
        if d and d["battery"]["capacity_kwh"]:
            return d["battery"]["capacity_kwh"], d["backup"]["eps_dod_pct"]
        return None
    alerts = notify.from_env(battery_for_runtime)

    while not stop.is_set():
        started = time.monotonic()
        try:
            vals = inv.snapshot()
        except Exception as exc:  # keep polling through network blips / reboots
            failures += 1
            if failures == 1 or failures % 30 == 0:
                log.warning("Poll failed (%d in a row): %s", failures, exc)
            alerts.poll_failed(exc)
            stop.wait(max(0.0, POLL_INTERVAL - (time.monotonic() - started)))
            continue
        if failures:
            log.info("Recovered after %d failed polls", failures)
        failures = 0
        alerts.poll_ok()

        point = Point("solis").tag("host", INVERTER_HOST)
        for k, v in vals.items():
            point = point.field(k, v)
        records = [point]
        events = tracker.update(vals)                 # grid outages and alarms, on change only
        for ev in events:
            log.warning("EVENT %s %s %s (%s)", ev["kind"], ev["state"], ev["name"], ev["key"])
            p = Point("solis_event").tag("kind", ev["kind"]).tag("key", ev["key"])
            for k in ("state", "name", "severity", "soc", "load"):
                if ev[k] is not None:
                    p = p.field(k, ev[k])
            records.append(p)
        alerts.events(events, vals)                   # Telegram, independent of InfluxDB
        alerts.snapshot(vals)
        try:
            writer.write(bucket=INFLUX_BUCKET, record=records)
        except Exception as exc:
            log.warning("InfluxDB write failed: %s", exc)
        stop.wait(max(0.0, POLL_INTERVAL - (time.monotonic() - started)))

    server.shutdown()
    inv.close()
    influx.close()
    log.info("Stopped")


if __name__ == "__main__":
    main()
