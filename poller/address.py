"""Where the inverter's data logger is.

The address saved from the Settings page (a small JSON file in the poller's volume) wins over INVERTER_HOST,
INVERTER_PORT and MODBUS_UNIT from the environment, so it can be set and changed in the dashboard without editing
files or restarting anything. Nothing here talks to the inverter.
"""
import ipaddress
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("solis-poller")

LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HOSTNAME = re.compile(rf"{LABEL}(?:\.{LABEL})*")


def check_host(value) -> str:
    """An IPv4 address or a host name (like solis-logger.local). Rejects URLs, ports, spaces and addresses
    that can never be a logger on the network (this machine, multicast, link-local)."""
    if not isinstance(value, str):
        raise ValueError("the address must be text")
    host = value.strip()
    if not host or len(host) > 253:
        raise ValueError("enter the logger's IP address or host name")
    if re.fullmatch(r"[0-9.]+", host):                       # looks like an IPv4 address: it must be a valid one
        try:
            ip = ipaddress.IPv4Address(host)
        except ValueError:
            raise ValueError("that is not a valid IPv4 address") from None
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
            raise ValueError("that address cannot be the logger (use its address on your home network)")
        return str(ip)
    if not HOSTNAME.fullmatch(host):
        raise ValueError("use just the IP address or host name, without http://, a port or spaces")
    return host.lower()


def _number(value, name, lo, hi) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ValueError(f"{name} must be a whole number")
    if not lo <= int(value) <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return int(value)


def check_port(value) -> int:
    return _number(value, "the port", 1, 65535)


def check_unit(value) -> int:
    return _number(value, "the Modbus unit", 1, 247)


class AddressStore:
    """The saved address if there is one, else the environment's."""

    def __init__(self, path: str | None, env_host: str = "", env_port: int = 502, env_unit: int = 1):
        self.path = path or None
        self.env = {"host": (env_host or "").strip(), "port": env_port, "unit": env_unit}
        self.lock = threading.Lock()
        self.saved = self._load()

    def _load(self) -> dict | None:
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                raw = json.load(f)
            return {"host": check_host(raw["host"]), "port": check_port(raw.get("port", 502)), "unit": check_unit(raw.get("unit", 1))}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("inverter address file %s is unreadable (%s): using INVERTER_HOST", self.path, exc)
            return None

    def value(self) -> dict:
        """{host, port, unit, source}; source is "saved", "env" or "unset" (no address anywhere)."""
        if self.saved is not None:
            return {**self.saved, "source": "saved"}
        return {**self.env, "source": "env" if self.env["host"] else "unset"}

    def set(self, host, port=502, unit=1) -> dict:
        """Save a new address (validated); host None removes the saved one (back to INVERTER_HOST)."""
        new = None if host is None else {"host": check_host(host), "port": check_port(port), "unit": check_unit(unit)}
        with self.lock:
            if self.path:
                if new is None:
                    if os.path.exists(self.path):
                        os.remove(self.path)
                else:
                    os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                    tmp = self.path + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(new, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.path)             # a crash never leaves half a file
            self.saved = new
        return self.value()


class PollStatus:
    """What the last polls did, for the Settings page: connected, or why not."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.last_ok: float | None = None
        self.error: str | None = None
        self.failures = 0
        self.lock = threading.Lock()

    def ok(self) -> None:
        with self.lock:
            self.last_ok, self.error, self.failures = self.clock(), None, 0

    def failed(self, exc) -> None:
        with self.lock:
            self.failures += 1
            self.error = str(exc)[:200]

    def reset(self) -> None:
        """A new address: what happened with the old one says nothing about it."""
        with self.lock:
            self.last_ok, self.error, self.failures = None, None, 0

    def info(self, interval_s: float) -> dict:
        with self.lock:
            age = None if self.last_ok is None else self.clock() - self.last_ok
            return {"connected": age is not None and age <= max(30.0, 3 * interval_s),
                    "last_ok_age_s": None if age is None else round(age, 1),
                    "error": self.error, "failures": self.failures}
