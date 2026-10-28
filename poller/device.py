"""
Device details read from inverter input registers (FC04), for the dashboard's Devices tab.

Register meanings were checked against a live S6-EH1P on 2026-10-18: the serial number
matches SolisCloud, BMS voltage matches 33133, meter V/W/Hz match the grid readings.
The BMS battery temperature (33043, S16 0.1 C, from the newer Solis map) is not yet checked live.
Values with no confirmed meaning are left out rather than guessed. Battery capacity comes
from the Settings page (CapacityStore), else BATTERY_CAPACITY_KWH, else rated Ah (43019) x 51.2 V nominal.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import faults

# (start, count) input-register blocks read on each request (<= 50 registers each)
BLOCKS = ((33000, 44), (33071, 35), (33116, 6), (33137, 24), (33250, 37), (33297, 4))
HOLDING = ((43010, 15),)        # 43010..43024: SOC limits and rated capacity
NOMINAL_CELL_V = 51.2           # LV LiFePO4 nominal pack voltage, for Ah -> kWh
MAX_CAPACITY_KWH = 1000

log = logging.getLogger("solis-device")

BATTERY_MODELS = {0x0000: "No battery", 0x0001: "Pylontech LV", 0x0002: "User defined", 0x0003: "BYD B-Box LV",
                  0x0004: "Dyness LV"}      # confirmed on this unit: Dyness DL5.0C reports 0x0004
METER_LOCATION = {0x01: "Grid", 0x02: "Load", 0x03: "Grid + PV (two meters)"}
METER_TYPE = {0x01: "Generic 1-phase", 0x02: "Acrel 3-phase", 0x03: "Generic 3-phase",
              0x04: "Eastron 1-phase", 0x05: "Eastron 3-phase", 0x06: "No meter"}


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _s32(hi, lo):
    v = (hi << 16) | lo
    return v - 0x100000000 if v & 0x80000000 else v


def _u32(hi, lo):
    return (hi << 16) | lo


def _ascii(regs):
    chars = "".join(chr(r >> 8) + chr(r & 0xFF) for r in regs)
    return "".join(c for c in chars if c.isprintable() and c != "\x00").strip()


def decode(r: dict[int, int], h: dict[int, int] | None = None, capacity_kwh: float | None = None,
           source: str = "BATTERY_CAPACITY_KWH") -> dict:
    """r: input register -> raw value for every register in BLOCKS; h: holding registers in HOLDING.
    capacity_kwh overrides the rated-Ah estimate; source says where it came from ("saved" or "BATTERY_CAPACITY_KWH")."""
    h = h or {}
    y, mo, d, hh, mi, s = (r[33022 + i] for i in range(6))
    try:
        clock = datetime(2000 + y, mo, d, hh, mi, s).isoformat()
    except ValueError:
        clock = None
    fault_regs = [r[33116 + i] for i in range(5)]
    snapshot = {**{f"fault_code_0{i + 1}": v for i, v in enumerate(fault_regs)},
                "operating_status": r[33121], "bms_fault_01": r[33145], "bms_fault_02": r[33146]}
    alarms = faults.active_alarms(snapshot)
    status = r[33095]
    meter_v = r[33251] / 10
    meter_cfg = r[33300]
    rated_ah = h.get(43019)
    bms_temp = round(_s16(r[33043]) / 10, 1)
    if capacity_kwh is None:
        capacity_kwh, source = rated_kwh(rated_ah), "inverter"
    return {
        "inverter": {
            "serial": _ascii([r[33004 + i] for i in range(16)]) or None,
            "model_code": f"0x{r[33000]:04X}",
            "dsp_version": f"0x{r[33001]:04X}",
            "hmi_version": f"0x{r[33002]:04X}",
            "clock": clock,
            "status_code": status,
            "status": faults.status_text(status),
            "fault_codes": [f"0x{f:04X}" for f in fault_regs],
            "has_fault": any(sev == "fault" for _, sev in alarms.values()),
            "alarms": [{"key": k, "name": n, "severity": sev} for k, (n, sev) in sorted(alarms.items())],
            "no_grid": bool(fault_regs[0] & 1) or status == faults.NO_GRID_STATUS,
            "dc_bus_voltage": round(r[33071] / 10, 1),
            "power_limit_pct": round(r[33104] / 100, 1),
            "grid_standard_code": r[33092],
        },
        "backup": {
            "voltage": round(r[33137] / 10, 1),
            "current": round(r[33138] / 10, 1),
            "eps_dod_pct": r[33298],                     # backup output stops discharging at this SOC
            "offgrid_dod_pct": r[33297],
            "switch_time_ms": r[33299] * 10,             # protocol unit: 10 ms
        },
        "battery": {
            "model": BATTERY_MODELS.get(r[33160], f"Code 0x{r[33160]:04X}"),
            "bms_voltage": round(r[33141] / 100, 2),
            "bms_current": round(_s16(r[33142]) / 10, 1),
            "bms_charge_limit_a": round(r[33143] / 10, 1),
            "bms_discharge_limit_a": round(r[33144] / 10, 1),
            # None without BMS data or outside what a battery can report (an unsupported register reads oddly)
            "bms_temperature": bms_temp if r[33141] > 0 and -40 <= bms_temp <= 90 else None,
            "bms_failure_codes": [f"0x{r[33145]:04X}", f"0x{r[33146]:04X}"],
            "has_fault": bool(r[33145] or r[33146]),
            "connected": r[33141] > 0,
            "rated_capacity_ah": rated_ah,
            "capacity_kwh": capacity_kwh,
            "capacity_source": source,
            "max_charge_soc": h.get(43010),
            "overdischarge_soc": h.get(43011),
            "force_charge_soc": h.get(43018),
            "backup_soc": h.get(43024),
        },
        "meter": {
            "voltage": round(meter_v, 1),
            "current": round(r[33252] / 100, 2),
            "active_power": _s32(r[33263], r[33264]),       # W, - = importing (like meter_power)
            "reactive_power": _s32(r[33271], r[33272]),
            "apparent_power": _s32(r[33279], r[33280]),
            "power_factor": round(_s16(r[33281]) / 100, 2),
            "frequency": round(r[33282] / 100, 2),
            "import_total_kwh": round(_u32(r[33283], r[33284]) / 100, 2),
            "export_total_kwh": round(_u32(r[33285], r[33286]) / 100, 2),
            "connected": meter_v > 0,
            "location": METER_LOCATION.get(meter_cfg >> 8, f"Code 0x{meter_cfg >> 8:02X}"),
            "type": METER_TYPE.get(meter_cfg & 0xFF, f"Code 0x{meter_cfg & 0xFF:02X}"),
        },
    }


def rated_kwh(rated_ah) -> float | None:
    return round(rated_ah * NOMINAL_CELL_V / 1000, 2) if rated_ah else None


def check_kwh(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= MAX_CAPACITY_KWH:
        raise ValueError(f"capacity must be a number of kWh above 0 and at most {MAX_CAPACITY_KWH}")
    return round(float(v), 2)


class CapacityStore:
    """Battery capacity for the runtime estimate: the value saved from the Settings page (a small JSON file in the
    poller's volume, so a battery moved to another inverter needs no .env edit), else BATTERY_CAPACITY_KWH.
    Nothing is written to the inverter."""

    def __init__(self, path: str | None = None, env_kwh: str | None = None):
        self.path = path or None
        self.env_kwh = float(env_kwh) if env_kwh else None
        self.lock = threading.Lock()
        self.saved = self._load()

    def _load(self) -> float | None:
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                return check_kwh(json.load(f)["kwh"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("battery capacity file %s is unreadable (%s): using .env / the inverter", self.path, exc)
            return None

    def value(self) -> tuple[float | None, str | None]:
        """(kWh, source) or (None, None) when the inverter's rated Ah should be used."""
        if self.saved is not None:
            return self.saved, "saved"
        if self.env_kwh is not None:
            return self.env_kwh, "BATTERY_CAPACITY_KWH"
        return None, None

    def set(self, kwh: float | None) -> None:
        """Save a new capacity; None removes the saved one (back to .env or the inverter)."""
        kwh = None if kwh is None else check_kwh(kwh)
        with self.lock:
            if self.path:
                if kwh is None:
                    if os.path.exists(self.path):
                        os.remove(self.path)
                else:
                    os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                    tmp = self.path + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump({"kwh": kwh}, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.path)             # a crash never leaves half a file
            self.saved = kwh


class DeviceReader:
    """Reads and decodes device details, cached briefly to keep Modbus traffic low."""

    def __init__(self, inverter, ttl=30, clock=time.monotonic, capacity: CapacityStore | None = None):
        self.inv, self.ttl, self.clock = inverter, ttl, clock
        self.capacity = capacity or CapacityStore(env_kwh=os.getenv("BATTERY_CAPACITY_KWH"))
        self._cache, self._at = None, -1e9
        self._raw = None                              # last (input, holding) registers, to re-decode after a capacity change

    def read(self) -> dict:
        if self._cache is None or self.clock() - self._at > self.ttl:
            self.refresh()
        return self._cache

    def invalidate(self) -> None:
        """Forget what was read from the previous logger (the address was changed)."""
        self._cache, self._at, self._raw = None, -1e9, None

    def cached(self) -> dict | None:
        """Last read without touching the inverter (None until the first refresh)."""
        return self._cache

    def refresh(self) -> None:
        regs, hold = {}, {}
        for start, n in BLOCKS:
            for i, v in enumerate(self.inv.read_input(start, n)):
                regs[start + i] = v
        for start, n in HOLDING:
            for i, v in enumerate(self.inv.read_holding(start, n)):
                hold[start + i] = v
        self._raw = (regs, hold, datetime.now(timezone.utc).isoformat())
        self._decode()
        self._at = self.clock()

    def _decode(self) -> None:
        regs, hold, read_at = self._raw
        kwh, source = self.capacity.value()
        self._cache = {**decode(regs, hold, kwh, source or "inverter"),
                       "logger": {"host": self.inv.host, "port": self.inv.port, "unit": self.inv.unit},
                       "read_at": read_at}

    def capacity_info(self) -> dict:
        """The capacity in use and every value it could come from (the inverter's from the last read, if any)."""
        kwh, source = self.capacity.value()
        inverter = rated_kwh(self._raw[1].get(43019)) if self._raw else None
        return {"capacity_kwh": kwh if kwh is not None else inverter, "source": source or "inverter",
                "saved_kwh": self.capacity.saved, "env_kwh": self.capacity.env_kwh, "inverter_kwh": inverter,
                "rated_capacity_ah": self._raw[1].get(43019) if self._raw else None, "max_kwh": MAX_CAPACITY_KWH}

    def set_capacity(self, kwh: float | None) -> dict:
        self.capacity.set(kwh)
        if self._raw:
            self._decode()                            # runtime estimates and alerts use it right away
        return self.capacity_info()
