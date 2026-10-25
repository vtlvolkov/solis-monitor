"""
Inverter settings control, used by the web dashboard's Settings page.

Served on an internal HTTP port (CONTROL_PORT, Docker network only, never published)
so the poller stays the only Modbus client. Every request needs the shared secret
(Authorization: Bearer <INFLUX_TOKEN>), which only the web container has.

Safety model
------------
* CONTROL_ENABLED=false (default) -> settings can be read, never written.
* Only the named settings below exist; no raw register writes.
* Currents are bounded by MAX_CHARGE_CURRENT_A / MAX_DISCHARGE_CURRENT_A (0 = refuse),
  SOC values by the inverter's own related limits.
* One change at a time, at most one per MIN_WRITE_INTERVAL_S.
* Read-back verification after 3 s, and an audit point in measurement "solis_control".

Register addresses were checked against a live S6-EH1P (2026-10-18). It uses the six-slot
Time-of-Use layout (43707..43791); newer "FB00" firmware ignores some single-register
writes, so everything except the long-proven 43110/43117/43118 is written with FC16.
"""
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

log = logging.getLogger("solis-control")

REG_CLOCK = 43000                # 43000..43005: year-2000, month, day, hour, minute, second
REG_MAX_CHARGE_SOC = 43010
REG_OVERDISCHARGE_SOC = 43011
REG_FORCE_CHARGE_SOC = 43018
REG_RATED_AH = 43019
REG_BACKUP_SOC = 43024
REG_STORAGE_SWITCH = 43110       # bitfield, see STORAGE_BITS
REG_MAX_CHARGE_A = 43117
REG_MAX_DISCHARGE_A = 43118
REG_OFFGRID_SOC = 43137
REG_EPS_DOD = 43138
REG_TOU_SWITCH = 43707           # bits 0-5 charge slots 1-6, bits 6-11 discharge slots 1-6
REG_TOU_CHARGE = 43708           # 7 registers per slot: SOC, current 0.1 A, voltage 0.1 V, start h, start m, end h, end m
REG_TOU_DISCHARGE = 43750
TOU_SLOTS = 6

BIT_TIME_OF_USE = 1
BIT_RESERVE = 4
BIT_GRID_CHARGE = 5
STORAGE_BITS = {0: "self_use", 1: "time_of_use", 2: "off_grid", 3: "battery_wakeup",
                4: "reserve_battery", 5: "allow_grid_charge", 6: "feed_in_priority",
                7: "batt_ovc", 8: "forcecharge_peakshaving", 9: "battery_current_correction",
                10: "battery_healing", 11: "peak_shaving"}
FLAG_SETTINGS = {"grid_charging": (BIT_GRID_CHARGE, "Grid charging", ("forbidden", "allowed")),
                 "time_of_use_mode": (BIT_TIME_OF_USE, "Time-of-Use mode", ("off", "on")),
                 "reserve_mode": (BIT_RESERVE, "Reserve battery mode", ("off", "on"))}
CURRENT_REGS = {"max_charge_current_a": REG_MAX_CHARGE_A, "max_discharge_current_a": REG_MAX_DISCHARGE_A}
SETTLE_S = 3                     # some firmwares revert a value within seconds
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ControlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Config:
    def __init__(self, env=os.environ):
        self.enabled = env.get("CONTROL_ENABLED", "false").lower() in ("1", "true", "yes")
        self.scale = float(env.get("CURRENT_SCALE", "0.1"))            # amps per raw unit of 43117/43118
        self.max_a = {"max_charge_current_a": float(env.get("MAX_CHARGE_CURRENT_A", "0")),
                      "max_discharge_current_a": float(env.get("MAX_DISCHARGE_CURRENT_A", "0"))}
        self.min_interval = float(env.get("MIN_WRITE_INTERVAL_S", "60"))
        self.token = env.get("INFLUX_TOKEN", "")
        self.port = int(env.get("CONTROL_PORT", "8780"))
        self.tz = env.get("TZ") or "Europe/Kyiv"


def _hhmm(h, m):
    return f"{h:02d}:{m:02d}"


def decode_slots(regs: list[int], switch: int, bit0: int) -> list[dict]:
    out = []
    for i in range(TOU_SLOTS):
        soc, cur, volt, sh, sm, eh, em = regs[i * 7:i * 7 + 7]
        out.append({"slot": i + 1, "enabled": bool(switch >> (bit0 + i) & 1), "soc": soc,
                    "current_a": round(cur / 10, 1), "voltage": round(volt / 10, 1),
                    "start": _hhmm(sh, sm), "end": _hhmm(eh, em)})
    return out


def decode_settings(h: dict[int, int], scale: float) -> dict:
    """h: holding register -> raw value for the blocks read by Controller.settings."""
    sw = h[REG_STORAGE_SWITCH]
    y, mo, d, hh, mi, s = (h[REG_CLOCK + i] for i in range(6))
    try:
        clock = datetime(2000 + y, mo, d, hh, mi, s).isoformat()
    except ValueError:
        clock = None
    tou_sw = h[REG_TOU_SWITCH]
    ch, dis = h[REG_MAX_CHARGE_A], h[REG_MAX_DISCHARGE_A]
    return {
        "storage_switch_raw": sw,
        "storage_flags": {name: bool(sw >> bit & 1) for bit, name in STORAGE_BITS.items()},
        "grid_charging": bool(sw >> BIT_GRID_CHARGE & 1),
        "time_of_use_mode": bool(sw >> BIT_TIME_OF_USE & 1),
        "reserve_mode": bool(sw >> BIT_RESERVE & 1),
        "max_charge_current_raw": ch,
        "max_discharge_current_raw": dis,
        "max_charge_current_a": round(ch * scale, 1),
        "max_discharge_current_a": round(dis * scale, 1),
        "battery": {
            "max_charge_soc": h[REG_MAX_CHARGE_SOC],
            "overdischarge_soc": h[REG_OVERDISCHARGE_SOC],
            "force_charge_soc": h[REG_FORCE_CHARGE_SOC],
            "backup_soc": h[REG_BACKUP_SOC],
            "offgrid_overdischarge_soc": h[REG_OFFGRID_SOC],
            "eps_dod": h[REG_EPS_DOD],
            "rated_capacity_ah": h[REG_RATED_AH],
            "float_voltage": round(h[43016] / 10, 1),
            "equalize_voltage": round(h[43017] / 10, 1),
        },
        "clock": clock,
        "tou": {
            "switch_raw": tou_sw,
            "charge": decode_slots([h[REG_TOU_CHARGE + i] for i in range(42)], tou_sw, 0),
            "discharge": decode_slots([h[REG_TOU_DISCHARGE + i] for i in range(42)], tou_sw, 6),
        },
    }


# (start, count) holding blocks read for the settings page, each <= 50 registers
SETTINGS_BLOCKS = ((43000, 25), (43110, 9), (43137, 2), (43707, 43), (43750, 42))


class Controller:
    def __init__(self, inverter, cfg: Config, audit=None, clock=time.time, sleep=time.sleep, now=None):
        self.inv, self.cfg = inverter, cfg
        self.audit = audit or (lambda action, **fields: None)
        self.clock, self.sleep = clock, sleep
        self.now = now or (lambda: datetime.now(ZoneInfo(cfg.tz)).replace(tzinfo=None))
        self.last_write = 0.0
        self.lock = threading.Lock()                 # one change at a time
        self.devices = None                          # device.DeviceReader, set by poller.main
        self._settings_cache = None                  # last settings() result, for /settings/cached
        self._settings_at = -1e9

    def _wait_s(self) -> int:
        return max(0, int(self.cfg.min_interval - (self.clock() - self.last_write) + 0.999))

    def _read(self) -> dict[int, int]:
        h = {}
        for start, n in SETTINGS_BLOCKS:
            for i, v in enumerate(self.inv.read_holding(start, n)):
                h[start + i] = v
        return h

    def settings(self) -> dict:
        s = decode_settings(self._read(), self.cfg.scale)
        s["control"] = {
            "enabled": self.cfg.enabled,
            "current_scale": self.cfg.scale,
            "limits": dict(self.cfg.max_a),
            "min_interval_s": self.cfg.min_interval,
            "next_change_in_s": self._wait_s(),
            "server_time": self.now().isoformat(timespec="seconds"),
        }
        self._settings_cache, self._settings_at = s, self.clock()
        return s

    def cached_settings(self, max_age_s: float = 600) -> dict:
        """Last settings read (refreshed in the background and after every change); live read if stale.
        server_time and the rate-limit countdown are always current."""
        if self._settings_cache is None or self.clock() - self._settings_at > max_age_s:
            return self.settings()
        s = dict(self._settings_cache)
        s["control"] = {**s["control"], "next_change_in_s": self._wait_s(),
                        "server_time": self.now().isoformat(timespec="seconds")}
        s["cached_age_s"] = round(self.clock() - self._settings_at)
        return s

    def apply(self, setting: str, value) -> dict:
        if not self.lock.acquire(blocking=False):
            raise ControlError(409, "Another change is in progress.")
        try:
            return self._apply(setting, value)
        finally:
            self.lock.release()

    # ------------------------------------------------------------------ validation helpers
    @staticmethod
    def _int(value, name, lo, hi):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ControlError(400, f"{name} must be a whole number")
        value = int(value)
        if not lo <= value <= hi:
            raise ControlError(400, f"{name} must be between {lo} and {hi}")
        return value

    def _amps(self, value, limit_key, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ControlError(400, f"{name} must be a number of amps")
        limit = self.cfg.max_a[limit_key]
        if limit <= 0:
            env = "MAX_DISCHARGE_CURRENT_A" if "discharge" in limit_key else "MAX_CHARGE_CURRENT_A"
            raise ControlError(403, f"{env} is not configured on the server.")
        amps = round(float(value), 1)
        if not 0 <= amps <= limit:
            raise ControlError(400, f"{amps:g} A is outside the allowed range 0..{limit:g} A")
        return amps

    # ------------------------------------------------------------------ apply
    def _apply(self, setting, value):
        if not self.cfg.enabled:
            raise ControlError(403, "Control is disabled on the server (CONTROL_ENABLED=false).")
        wait = self._wait_s()
        if wait > 0:
            raise ControlError(429, f"Rate limit: next change allowed in {wait} s.")
        plan = self._plan(setting, value)        # validates and reads current values; no writes yet
        if plan is None:
            raise ControlError(400, f"unknown setting {setting!r}")
        reg, before, expected, summary, multi = plan
        if expected == before:
            return {"no_change": True, "summary": summary, "register": reg, "before_raw": before}

        self.last_write = self.clock()               # counts even if the write fails
        if multi:
            self.inv.write_block(reg, expected)
        else:
            self.inv.write_holding(reg, expected[0])
        extra = getattr(self, "_after_write", None)
        if extra:
            extra()
        self.sleep(SETTLE_S)
        after = self.inv.read_holding(reg, len(expected))
        ok = self._verify(setting, after, expected)
        self.audit(setting, register=reg, before=_fmt(before), written=_fmt(expected), read_back=_fmt(after),
                   verified=ok, summary=summary)
        result = {"applied": summary, "register": reg, "before_raw": before,
                  "written_raw": expected, "read_back_raw": after, "verified": ok}
        self._settings_at = -1e9                     # cached settings are stale now
        if not ok:
            result["warning"] = ("The inverter did not keep the value. This firmware may only allow "
                                 "this setting from the front panel or SolisCloud. Don't keep retrying.")
        return result

    def _verify(self, setting, after, expected):
        if setting == "clock_sync":                  # seconds tick during the settle delay
            return after[:4] == expected[:4] and abs((after[4] * 60 + after[5]) - (expected[4] * 60 + expected[5])) <= SETTLE_S + 5
        if setting == "tou_slot":
            return after == expected and self._tou_switch_ok()
        return after == expected

    def _plan(self, setting, value):
        """-> (register, before[list], expected[list], summary, use_fc16) or None for unknown."""
        self._after_write = None
        if setting in FLAG_SETTINGS:
            if not isinstance(value, bool):
                raise ControlError(400, f"{setting} must be true or false")
            bit, label, words = FLAG_SETTINGS[setting]
            before = self.inv.read_holding(REG_STORAGE_SWITCH)[0]
            new = before | (1 << bit) if value else before & ~(1 << bit)
            return (REG_STORAGE_SWITCH, [before], [new],
                    f"{label}: {words[before >> bit & 1]} -> {words[int(value)]}", False)

        if setting in CURRENT_REGS:
            amps = self._amps(value, setting, setting)
            reg = CURRENT_REGS[setting]
            raw = int(round(amps / self.cfg.scale))
            if not 0 <= raw <= 0xFFFF:
                raise ControlError(400, "value does not fit the register")
            before = self.inv.read_holding(reg)[0]
            which = "charge" if reg == REG_MAX_CHARGE_A else "discharge"
            return reg, [before], [raw], f"Max {which} current: {round(before * self.cfg.scale, 1):g} A -> {amps:g} A", False

        if setting in ("overdischarge_soc", "backup_soc"):
            lim = self.inv.read_holding(REG_MAX_CHARGE_SOC, 15)          # 43010..43024
            max_soc, over, force, backup = lim[0], lim[1], lim[8], lim[14]
            if setting == "overdischarge_soc":
                soc = self._int(value, "Over-discharge SOC", max(5, force + 1), max_soc - 1)
                return (REG_OVERDISCHARGE_SOC, [over], [soc], f"Over-discharge SOC: {over}% -> {soc}%", True)
            soc = self._int(value, "Backup SOC", over, max_soc)
            return REG_BACKUP_SOC, [backup], [soc], f"Backup (reserve) SOC: {backup}% -> {soc}%", True

        if setting == "tou_slot":
            return self._plan_tou(value)

        if setting == "clock_sync":
            if value is not True:
                raise ControlError(400, "clock_sync must be true")
            before = self.inv.read_holding(REG_CLOCK, 6)
            n = self.now()
            new = [n.year - 2000, n.month, n.day, n.hour, n.minute, n.second]
            old = f"20{before[0]:02d}-{before[1]:02d}-{before[2]:02d} {before[3]:02d}:{before[4]:02d}:{before[5]:02d}"
            return REG_CLOCK, before, new, f"Inverter clock: {old} -> {n.strftime('%Y-%m-%d %H:%M:%S')} ({self.cfg.tz})", True

        return None

    def _plan_tou(self, v):
        if not isinstance(v, dict):
            raise ControlError(400, "tou_slot must be an object")
        kind = v.get("kind")
        if kind not in ("charge", "discharge"):
            raise ControlError(400, "kind must be charge or discharge")
        slot = self._int(v.get("slot"), "slot", 1, TOU_SLOTS)
        if not isinstance(v.get("enabled"), bool):
            raise ControlError(400, "enabled must be true or false")
        times = []
        for k in ("start", "end"):
            m = HHMM.match(str(v.get(k, "")))
            if not m:
                raise ControlError(400, f"{k} must be HH:MM (00:00-23:59)")
            times += [int(m.group(1)), int(m.group(2))]
        if times[:2] == times[2:]:
            raise ControlError(400, "start and end must differ")
        limit_key = "max_charge_current_a" if kind == "charge" else "max_discharge_current_a"
        amps = self._amps(v.get("current_a"), limit_key, "current_a")
        floor = self.inv.read_holding(REG_OVERDISCHARGE_SOC)[0]
        soc = self._int(v.get("soc"), "SOC", 10 if kind == "charge" else floor, 100)

        base = (REG_TOU_CHARGE if kind == "charge" else REG_TOU_DISCHARGE) + (slot - 1) * 7
        before = self.inv.read_holding(base, 7)
        new = [soc, int(round(amps * 10)), before[2], *times]       # keep the cut-off voltage as is
        bit = (slot - 1) + (0 if kind == "charge" else 6)
        sw = self.inv.read_holding(REG_TOU_SWITCH)[0]
        sw_new = sw | (1 << bit) if v["enabled"] else sw & ~(1 << bit)

        def write_switch():
            if sw_new != sw:
                self.inv.write_block(REG_TOU_SWITCH, [sw_new])
        self._after_write = write_switch
        self._tou_switch_expected = sw_new
        was = ("on" if sw >> bit & 1 else "off")
        summary = (f"{kind.capitalize()} slot {slot}: "
                   f"{_hhmm(*before[3:5])}-{_hhmm(*before[5:7])} {before[1] / 10:g} A {before[0]}% ({was}) -> "
                   f"{v['start']}-{v['end']} {amps:g} A {soc}% ({'on' if v['enabled'] else 'off'})")
        if new == before and sw_new == sw:
            return base, before, before, summary, True
        if new == before:                            # only the enable bit changes
            self._after_write = None
            return REG_TOU_SWITCH, [sw], [sw_new], summary, True
        return base, before, new, summary, True

    def _tou_switch_ok(self):
        return self.inv.read_holding(REG_TOU_SWITCH)[0] == self._tou_switch_expected


def _fmt(regs):
    return regs[0] if len(regs) == 1 else ",".join(map(str, regs))


def make_handler(ctl: Controller):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # the web service gave up waiting

        def _authorized(self):
            if not ctl.cfg.token:
                return False
            return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {ctl.cfg.token}")

        def _run(self, fn):
            if fn is None:
                return self._send(404, {"detail": "not found"})
            if not self._authorized():
                return self._send(401, {"detail": "unauthorized"})
            try:
                self._send(200, fn())
            except ControlError as exc:
                self._send(exc.status, {"detail": str(exc)})
            except Exception as exc:                 # Modbus timeout, inverter offline ...
                log.warning("control request failed: %s", exc)
                self._send(502, {"detail": f"Inverter not reachable: {exc}"})

        def do_GET(self):
            routes = {"/settings": ctl.settings, "/settings/cached": ctl.cached_settings,
                      "/device": ctl.devices and ctl.devices.read}
            self._run(routes.get(self.path))

        def do_POST(self):
            def apply():
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 1024:
                    raise ControlError(400, "invalid body")
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    raise ControlError(400, "body must be JSON")
                if not isinstance(body, dict):
                    raise ControlError(400, "body must be a JSON object")
                return ctl.apply(body.get("setting"), body.get("value"))
            self._run(apply if self.path == "/settings" else None)

        def log_message(self, fmt, *args):            # keep the poller log quiet
            pass

    return Handler


def serve(ctl: Controller) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", ctl.cfg.port), make_handler(ctl))
    threading.Thread(target=server.serve_forever, daemon=True, name="control-http").start()
    log.info("Control API on :%s (internal) | writes %s", ctl.cfg.port,
             "ENABLED" if ctl.cfg.enabled else "disabled (read-only)")
    return server
