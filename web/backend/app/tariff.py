"""Electricity tariff as a list of versions, each valid from a given moment on.

A saved change applies only to consumption from its start time onward, so the cost of everything
earlier keeps the price (and day/night hours) it had when it was consumed. The prices from .env are
the first version, valid from the beginning of time; the file only appears with the first change.
"""
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("solis-web")

WINDOW_S = 900                  # cost is calculated per 15-minute window, so changes start on a window boundary
MAX_PRICE = 10_000
MAX_AHEAD_DAYS = 366
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TariffError(ValueError):
    """A change the user can fix: bad numbers, nothing to change, a date too far ahead."""


@dataclass(frozen=True)
class Tariff:
    day: float
    night: float | None = None            # None = one price all day
    night_start: str | None = None
    night_end: str | None = None

    def as_dict(self) -> dict:
        return {"day": self.day, "night": self.night, "night_start": self.night_start, "night_end": self.night_end}

    @staticmethod
    def from_dict(d: dict) -> "Tariff":
        return Tariff(day=float(d["day"]), night=None if d.get("night") is None else float(d["night"]),
                      night_start=d.get("night_start"), night_end=d.get("night_end"))


def next_boundary(t: datetime) -> datetime:
    """The first 15-minute boundary strictly after `t` (UTC; every zone's local quarter-hours line up with it)."""
    ts = int(t.timestamp()) // WINDOW_S * WINDOW_S + WINDOW_S
    return datetime.fromtimestamp(ts, timezone.utc)


def _price(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= MAX_PRICE:
        raise TariffError(f"{name} must be a number above 0")
    return round(float(v), 4)


def validate(day, night, night_start, night_end) -> Tariff:
    """Checks the numbers and hours of a new tariff (the zone hours are only used with a night price)."""
    d = _price(day, "day price")
    if night is None:
        return Tariff(day=d)
    n = _price(night, "night price")
    if not (isinstance(night_start, str) and HHMM.match(night_start) and isinstance(night_end, str) and HHMM.match(night_end)):
        raise TariffError("night hours must be HH:MM")
    if night_start == night_end:
        raise TariffError("night start and end must differ")
    return Tariff(day=d, night=n, night_start=night_start, night_end=night_end)


def _parse_from(v) -> datetime | None:
    return None if v is None else datetime.fromisoformat(v).astimezone(timezone.utc)


class TariffStore:
    """The versions in time order, kept in memory and (if `path` is set) in a small JSON file."""

    def __init__(self, baseline: Tariff | None, path: str | None = None):
        self.path = path or None
        self.lock = threading.Lock()
        loaded = self._load()
        self.saved = loaded is not None
        self.versions: list[tuple[datetime | None, Tariff]] = loaded if loaded is not None else (
            [(None, baseline)] if baseline else [])

    # ------------------------------------------------------------------ reading
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                raw = json.load(f)["versions"]
            versions = [(_parse_from(v["from"]), Tariff.from_dict(v)) for v in raw]
            starts = [s for s, _ in versions]
            if (not versions or starts[0] is not None or any(s is None for s in starts[1:])
                    or any(x >= y for x, y in zip(starts[1:], starts[2:]))):
                raise ValueError("versions are not in time order, or the first one does not start at the beginning")
            return versions
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("tariff file %s is unreadable (%s): moved aside, using the .env prices", self.path, exc)
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            return None

    def at(self, t: datetime) -> Tariff | None:
        """The tariff in force at instant `t`; None if no tariff was ever configured."""
        for start, tariff in reversed(self.versions):
            if start is None or start <= t:
                return tariff
        return None

    def upcoming(self, now: datetime) -> list[tuple[datetime, Tariff]]:
        return [(s, tf) for s, tf in self.versions if s is not None and s > now]

    def between(self, start: datetime, stop: datetime) -> list[tuple[datetime | None, Tariff]]:
        """The versions in force at some point of [start, stop): the one at `start`, then every later start."""
        out = []
        for s, tf in self.versions:
            if s is not None and s >= stop:
                break
            out.append((s, tf))
        first_in = max((i for i, (s, _) in enumerate(out) if s is None or s <= start), default=0)
        return out[first_in:]

    @property
    def source(self) -> str:
        return "saved" if self.saved else "env"

    # ------------------------------------------------------------------ changing
    def set(self, tariff: Tariff, now: datetime, requested: datetime | None = None) -> datetime:
        """Make `tariff` valid from the next quarter-hour, or from `requested` if that is later.
        Returns the start time. Nothing earlier changes; changes still waiting to start are replaced."""
        eff = next_boundary(now)
        if requested is not None and requested > eff:
            eff = next_boundary(requested - timedelta(seconds=1))
        if eff > now + timedelta(days=MAX_AHEAD_DAYS):
            raise TariffError("that date is too far ahead")
        with self.lock:
            kept = [(s, tf) for s, tf in self.versions if s is None or s < eff]
            if kept and kept[-1][1] == tariff:
                raise TariffError("these are already the prices from then on")
            versions = kept + [(eff if kept else None, tariff)]       # the very first tariff also covers the past
            self._write(versions)
            self.versions, self.saved = versions, True
        return versions[-1][0] or eff

    def _write(self, versions) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {"versions": [{"from": s.isoformat() if s else None, **tf.as_dict()} for s, tf in versions]}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)                                      # a crash never leaves half a file


def version_dict(start: datetime | None, tariff: Tariff) -> dict:
    return {"from": start.isoformat() if start else None, **tariff.as_dict()}
