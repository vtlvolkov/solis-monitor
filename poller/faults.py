"""
Fault/alarm decoding (Solis ESINV-33000ID protocol, Appendix 2/5/6 and BMS 33145/33146)
and grid-outage / alarm event tracking for the dashboard's Events tab.
"""

# (fault register 1..5 = 33116..33120, bit) -> name. 33116 bit 0 "No grid" is tracked as an outage.
FAULT_BITS = {
    (1, 1): "Grid overvoltage", (1, 2): "Grid undervoltage", (1, 3): "Grid overfrequency",
    (1, 4): "Grid underfrequency", (1, 5): "Unbalanced grid", (1, 6): "Grid frequency fluctuation",
    (1, 7): "Grid reverse current", (1, 8): "Grid current tracking error", (1, 9): "Meter communication fail",
    (1, 10): "FailSafe",
    (2, 0): "Backup overvoltage", (2, 1): "Backup overload",
    (3, 0): "Battery not connected", (3, 1): "Battery overvoltage", (3, 2): "Battery undervoltage",
    (4, 0): "DC overvoltage", (4, 1): "DC bus overvoltage", (4, 2): "DC bus unbalanced",
    (4, 3): "DC bus undervoltage", (4, 4): "DC bus unbalanced (2)", (4, 5): "DC overcurrent (A)",
    (4, 6): "DC overcurrent (B)", (4, 7): "DC input interference", (4, 8): "Grid overcurrent",
    (4, 9): "IGBT overcurrent", (4, 10): "Grid interference (2)", (4, 11): "AFCI self-check",
    (4, 12): "Arc fault", (4, 13): "Grid current sampling fault", (4, 14): "DSP self-check error",
    (5, 0): "Grid interference", (5, 1): "DC component too high", (5, 2): "Over-temperature",
    (5, 3): "Relay check fault", (5, 4): "Under-temperature", (5, 5): "PV insulation fault",
    (5, 6): "12 V undervoltage", (5, 7): "Leakage current", (5, 8): "Leakage current self-check",
    (5, 9): "DSP initialisation", (5, 10): "DSP B fault", (5, 11): "Battery hardware overvoltage",
    (5, 12): "LLC hardware overcurrent", (5, 13): "Grid transient overcurrent",
    (5, 14): "Battery communication (CAN) fail", (5, 15): "DSP communication fail",
}

# BMS fault registers, LV hybrid column: (1 = 33145 / 2 = 33146, bit) -> name
BMS_BITS = {
    (1, 1): "BMS overvoltage", (1, 2): "BMS undervoltage", (1, 3): "BMS over-temperature",
    (1, 4): "BMS under-temperature", (1, 5): "BMS over-temperature while charging",
    (1, 6): "BMS under-temperature while charging", (1, 7): "BMS discharge overcurrent",
    (2, 0): "BMS charge overcurrent", (2, 3): "BMS internal protection", (2, 4): "BMS unbalanced modules",
}

# Operating status 33121 (Appendix 6): bits worth an alarm
OPERATING_BITS = {
    3: ("Fault caused shutdown", "fault"), 5: ("Limited operation (temperature/frequency)", "warning"),
    6: ("Limited operation (external)", "warning"), 7: ("Backup overload", "fault"),
    12: ("Grid surge", "warning"), 13: ("Fan fault", "warning"),
}

# Inverter status 33095 (Appendix 2, 1-phase hybrid column)
STATUS = {
    0x0000: "Waiting", 0x0001: "Open run", 0x0002: "Soft run", 0x0003: "Generating", 0x0004: "Standby",
    0x0005: "Standby (synchronising)", 0x0006: "Grid to load", 0x000F: "Normal", 0x1004: "Grid off",
    0x1010: "Grid overvoltage", 0x1011: "Grid undervoltage", 0x1012: "Grid overfrequency",
    0x1013: "Grid underfrequency", 0x1014: "Grid reverse current", 0x1015: "No grid",
    0x1016: "Unbalanced grid", 0x1017: "Grid frequency fluctuation", 0x1018: "Grid overcurrent",
    0x1019: "Grid current sampling error", 0x1020: "DC overvoltage", 0x1021: "DC bus overvoltage",
    0x1022: "DC bus unbalanced", 0x1023: "DC bus undervoltage", 0x1024: "DC bus unbalanced (2)",
    0x1025: "DC overcurrent (A)", 0x1026: "DC overcurrent (B)", 0x1027: "DC interference",
    0x1028: "DC reverse connection", 0x1029: "PV midpoint grounding fault", 0x1030: "Grid interference",
    0x1031: "DSP initial protection", 0x1032: "Over-temperature", 0x1033: "PV insulation fault",
    0x1034: "Leakage current", 0x1035: "Relay check fault", 0x1036: "DSP B fault",
    0x1037: "DC injection", 0x1038: "12 V undervoltage", 0x1039: "Leakage current check",
    0x103A: "Under-temperature", 0x1040: "AFCI check fault", 0x1041: "Arc fault",
    0x1042: "DSP SRAM fault", 0x1043: "DSP flash fault", 0x1044: "DSP PC fault", 0x1045: "DSP register fault",
    0x1046: "Grid interference (2)", 0x1047: "Grid current sampling error", 0x1048: "IGBT overcurrent",
    0x1050: "Grid transient overcurrent", 0x1051: "Battery hardware overvoltage",
    0x1052: "LLC hardware overcurrent", 0x1053: "Battery overvoltage", 0x1054: "Battery undervoltage",
    0x1055: "Battery not connected", 0x1056: "Backup overvoltage", 0x1057: "Backup overload",
    0x1058: "DSP self-check error", 0x2010: "Fail safe", 0x2011: "Meter communication fail",
    0x2012: "Battery communication fail", 0x2014: "DSP communication fail", 0x2015: "BMS alarm",
    0x2016: "Battery selection mismatch", 0x2017: "BMS alarm (2)", 0x2018: "DRM connect fail",
    0x2019: "Meter selection fail", 0x2020: "Lead-acid battery high temperature",
    0x2021: "Lead-acid battery low temperature", 0xF010: "Grid surge (alarm)", 0xF011: "Fan fault (alarm)",
}

NO_GRID_STATUS = 0x1015
GRID_LOST_BELOW_V = 100


def status_text(code) -> str:
    code = int(code)
    return STATUS.get(code, f"Code 0x{code:04X}")


def _bits(v):
    v = int(v or 0)
    return [b for b in range(16) if v >> b & 1]


def active_alarms(vals: dict) -> dict[str, tuple[str, str]]:
    """key -> (name, severity) for every alarm bit set in a poll snapshot."""
    out = {}
    for n in range(1, 6):
        for b in _bits(vals.get(f"fault_code_0{n}")):
            if (n, b) == (1, 0):
                continue                                     # no grid: an outage, not an alarm
            out[f"f{n}b{b}"] = (FAULT_BITS.get((n, b), f"Fault {n} bit {b}"), "fault")
    for n in (1, 2):
        for b in _bits(vals.get(f"bms_fault_0{n}")):
            out[f"bms{n}b{b}"] = (BMS_BITS.get((n, b), f"BMS fault {n} bit {b}"), "fault")
    for b in _bits(vals.get("operating_status")):
        if b in OPERATING_BITS:
            out[f"op{b}"] = OPERATING_BITS[b]
    return out


def grid_lost(vals: dict) -> bool:
    if int(vals.get("fault_code_01") or 0) & 1 or int(vals.get("status_code") or 0) == NO_GRID_STATUS:
        return True
    v = vals.get("grid_voltage")
    return v is not None and v < GRID_LOST_BELOW_V


class EventTracker:
    """Turns poll snapshots into start/end events. A change must be seen `debounce`
    polls in a row before it counts, so a single odd reading doesn't create an event."""

    def __init__(self, debounce: int = 2):
        self.debounce = debounce
        self.active: dict[str, tuple[str, str, str]] = {}     # key -> (kind, name, severity)
        self.pending: dict[str, int] = {}

    def update(self, vals: dict) -> list[dict]:
        observed = {f"alarm:{k}": ("alarm", n, s) for k, (n, s) in active_alarms(vals).items()}
        if grid_lost(vals):
            observed["outage:grid"] = ("outage", "Grid outage", "warning")
        events = []
        for key in set(observed) | set(self.active) | set(self.pending):
            if (key in observed) == (key in self.active):
                self.pending.pop(key, None)
                continue
            self.pending[key] = self.pending.get(key, 0) + 1
            if self.pending[key] < self.debounce:
                continue
            self.pending.pop(key)
            if key in observed:
                self.active[key] = observed[key]
                kind, name, sev = observed[key]
                state = "start"
            else:
                kind, name, sev = self.active.pop(key)
                state = "end"
            events.append({"kind": kind, "key": key.split(":", 1)[1], "name": name, "severity": sev,
                           "state": state, "soc": vals.get("battery_soc"), "load": vals.get("total_load_power")})
        return sorted(events, key=lambda e: (e["kind"], e["key"]))
