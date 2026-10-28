"""Find the inverter's data logger on a home network: every address in a /24 with the Modbus TCP port open that
answers a Modbus "read one input register" request. Reads only; the logger accepts one connection at a time, so the
caller closes the poller's own connection first."""
import ipaddress
import re
import socket
import struct
from concurrent.futures import ThreadPoolExecutor


def parse_network(text) -> str:
    """"192.168.0", "192.168.0.0/24" or any address in it -> "192.168.0". Only private (home) networks."""
    if not isinstance(text, str):
        raise ValueError("enter the network to scan, like 192.168.0")
    t = text.strip()
    m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\.(\d{1,3})(?:/24)?)?", t)
    if not m:
        raise ValueError("enter the network to scan, like 192.168.0")
    try:
        ip = ipaddress.IPv4Address(".".join(m.groups()[:3]) + ".1")
    except ValueError:
        raise ValueError("that is not a valid network") from None
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError("only home networks can be scanned (192.168.x.x, 10.x.x.x or 172.16-31.x.x)")
    return ".".join(m.groups()[:3])


def speaks_modbus(host: str, port: int = 502, unit: int = 1, timeout: float = 3.0) -> bool:
    """Does it answer a Modbus request? An exception reply still proves a Modbus server is there."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(struct.pack(">HHHBBHH", 1, 0, 6, unit, 4, 33000, 1))
            r = s.recv(64)
        return len(r) >= 9 and r[:4] == b"\x00\x01\x00\x00" and r[7] & 0x7F == 4
    except OSError:
        return False


def _open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_hosts(hosts, port: int = 502, unit: int = 1, connect_timeout: float = 0.8, workers: int = 64, probe=speaks_modbus) -> list[str]:
    """The hosts of `hosts` that have `port` open and answer Modbus, in the order given."""
    hosts = list(hosts)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        opened = [h for h, ok in zip(hosts, pool.map(lambda h: _open(h, port, connect_timeout), hosts)) if ok]
        return [h for h, ok in zip(opened, pool.map(lambda h: probe(h, port, unit), opened)) if ok]


def scan_network(network: str, port: int = 502, unit: int = 1, **kw) -> list[str]:
    prefix = parse_network(network)
    return scan_hosts((f"{prefix}.{i}" for i in range(1, 255)), port, unit, **kw)
