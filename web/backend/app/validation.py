"""Whitelists for every user-supplied query parameter.

Flux has no parameter binding in InfluxDB OSS, so queries are built only from
the canonical values returned here, never from the raw request strings.
"""
from fastapi import HTTPException

# Fields written by the poller (measurement "solis"), raw registers + derived.
FIELDS = (
    "pv1_voltage", "pv1_current", "pv2_voltage", "pv2_current", "pv_power",
    "pv_energy_today", "pv_energy_yesterday", "pv_energy_month", "pv_energy_year", "pv_energy_total",
    "dc_bus_voltage", "grid_voltage", "grid_frequency", "meter_power", "grid_power",
    "inverter_ac_power", "inverter_temperature",
    "status_code", "fault_code_01", "fault_code_02", "fault_code_03", "fault_code_04", "fault_code_05",
    "operating_status", "bms_fault_01", "bms_fault_02",
    "battery_voltage", "battery_current", "battery_direction", "battery_soc", "battery_soh",
    "battery_power", "battery_power_signed",
    "backup_voltage", "backup_current",
    "house_load_power", "backup_load_power", "total_load_power",
    "battery_charge_today", "battery_charge_yesterday", "battery_charge_total",
    "battery_discharge_today", "battery_discharge_yesterday", "battery_discharge_total",
    "grid_import_today", "grid_import_yesterday", "grid_import_total",
    "grid_export_today", "grid_export_yesterday", "grid_export_total",
    "load_consumption_today", "load_yesterday", "load_total",
)
FIELD_SET = frozenset(FIELDS)

ENERGY_FIELDS = (
    "grid_import_today", "grid_export_today", "battery_charge_today",
    "battery_discharge_today", "load_consumption_today", "pv_energy_today",
)

RANGES = {"1h": 3600, "3h": 3 * 3600, "6h": 6 * 3600, "12h": 12 * 3600, "24h": 86400,
          "2d": 2 * 86400, "7d": 7 * 86400, "30d": 30 * 86400}

# Ordered finest -> coarsest; the poller writes one point every ~10 s.
WINDOWS = {"10s": 10, "30s": 30, "1m": 60, "2m": 120, "5m": 300, "10m": 600, "15m": 900,
           "30m": 1800, "1h": 3600, "3h": 3 * 3600, "6h": 6 * 3600, "12h": 12 * 3600, "1d": 86400}

AGGS = frozenset({"mean", "max", "min", "last"})

MAX_POINTS = 500            # target per series for every=auto
HARD_MAX_POINTS = 5000      # explicit every may not exceed this
MAX_FIELDS = 12
MAX_DAYS = 366


def bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def parse_fields(raw: str) -> list[str]:
    names = [f.strip() for f in (raw or "").split(",") if f.strip()]
    if not names:
        raise bad("fields is required")
    if len(names) > MAX_FIELDS:
        raise bad(f"at most {MAX_FIELDS} fields per request")
    unknown = [f for f in names if f not in FIELD_SET]
    if unknown:
        raise bad(f"unknown field(s): {', '.join(sorted(set(unknown)))}")
    return list(dict.fromkeys(names))          # dedupe, keep order


def parse_range(raw: str) -> tuple[str, int]:
    if raw not in RANGES:
        raise bad(f"range must be one of {', '.join(RANGES)}")
    return raw, RANGES[raw]


def parse_agg(raw: str) -> str:
    if raw not in AGGS:
        raise bad(f"agg must be one of {', '.join(sorted(AGGS))}")
    return raw


def auto_window(range_s: int, max_points: int = MAX_POINTS) -> str:
    """Finest whitelisted window that keeps a series at or under max_points."""
    for name, sec in WINDOWS.items():
        if range_s / sec <= max_points:
            return name
    return next(reversed(WINDOWS))


def parse_every(raw: str, range_s: int) -> str:
    if raw == "auto":
        return auto_window(range_s)
    if raw not in WINDOWS:
        raise bad(f"every must be 'auto' or one of {', '.join(WINDOWS)}")
    if range_s / WINDOWS[raw] > HARD_MAX_POINTS:
        raise bad(f"every={raw} over this range exceeds {HARD_MAX_POINTS} points; use a larger window")
    return raw


def parse_days(raw: int) -> int:
    if not 1 <= raw <= MAX_DAYS:
        raise bad(f"days must be 1..{MAX_DAYS}")
    return raw
