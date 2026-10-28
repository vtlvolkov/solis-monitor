import json
import socket
import struct
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import address
import control
import poller
import scan


# ---------------------------------------------------------------- what counts as an address
@pytest.mark.parametrize("raw,ok", [
    ("192.168.0.105", "192.168.0.105"), (" 10.0.0.9 ", "10.0.0.9"), ("Solis-Logger.local", "solis-logger.local"), ("logger", "logger"),
])
def test_good_addresses(raw, ok):
    assert address.check_host(raw) == ok


@pytest.mark.parametrize("raw", [
    "", "   ", None, 5, "999.1.1.1", "1.2.3", "192.168.0.1.5", "127.0.0.1", "0.0.0.0", "224.0.0.1", "169.254.1.1",
    "http://192.168.0.105", "192.168.0.105:502", "a b", "a/b", "-bad.local", "bad-.local", "a" * 300, "192.168.0.105\nx",
])
def test_bad_addresses(raw):
    with pytest.raises(ValueError):
        address.check_host(raw)


def test_port_and_unit_limits():
    assert address.check_port(502) == 502 and address.check_unit(1) == 1 and address.check_unit(247) == 247
    for bad in (0, 65536, -1, 1.5, "502", True, None):
        with pytest.raises(ValueError):
            address.check_port(bad)
    for bad in (0, 248, 2.5, "1", False):
        with pytest.raises(ValueError):
            address.check_unit(bad)


# ---------------------------------------------------------------- saved address over the environment
def test_environment_is_used_until_something_is_saved(tmp_path):
    path = str(tmp_path / "inverter.json")
    s = address.AddressStore(path, "192.168.0.105", 502, 1)
    assert s.value() == {"host": "192.168.0.105", "port": 502, "unit": 1, "source": "env"}
    assert s.set("192.168.1.50", 1502, 3) == {"host": "192.168.1.50", "port": 1502, "unit": 3, "source": "saved"}
    again = address.AddressStore(path, "192.168.0.105", 502, 1)                   # survives a restart
    assert again.value()["host"] == "192.168.1.50" and again.value()["source"] == "saved"
    assert not (tmp_path / "inverter.json.tmp").exists()
    assert again.set(None) == {"host": "192.168.0.105", "port": 502, "unit": 1, "source": "env"}   # back to the environment
    assert not (tmp_path / "inverter.json").exists()


def test_nothing_anywhere_is_unset(tmp_path):
    s = address.AddressStore(str(tmp_path / "x.json"), "", 502, 1)
    assert s.value()["source"] == "unset" and s.value()["host"] == ""


def test_a_bad_value_is_refused_and_nothing_is_written(tmp_path):
    s = address.AddressStore(str(tmp_path / "x.json"), "192.168.0.105")
    with pytest.raises(ValueError):
        s.set("127.0.0.1")
    assert s.value()["source"] == "env" and not (tmp_path / "x.json").exists()


def test_an_unreadable_file_falls_back_to_the_environment(tmp_path):
    p = tmp_path / "x.json"
    for text in ("not json", json.dumps({"host": "127.0.0.1"}), json.dumps({"port": 502}), "[]"):
        p.write_text(text)
        assert address.AddressStore(str(p), "192.168.0.105").value() == {"host": "192.168.0.105", "port": 502, "unit": 1, "source": "env"}


def test_without_a_path_it_lives_in_memory():
    s = address.AddressStore(None, "")
    s.set("192.168.0.9")
    assert s.value()["host"] == "192.168.0.9"


# ---------------------------------------------------------------- is it connected?
def test_poll_status():
    t = [1000.0]
    st = address.PollStatus(clock=lambda: t[0])
    assert st.info(10)["connected"] is False and st.info(10)["last_ok_age_s"] is None
    st.ok(); t[0] += 12
    assert st.info(10) == {"connected": True, "last_ok_age_s": 12.0, "error": None, "failures": 0}
    t[0] += 40                                                                      # no reading for 52 s at a 10 s interval
    st.failed(ConnectionError("cannot connect to 192.168.0.105:502"))
    info = st.info(10)
    assert info["connected"] is False and info["failures"] == 1 and "cannot connect" in info["error"]
    st.reset()
    assert st.info(10)["last_ok_age_s"] is None and st.info(10)["error"] is None


# ---------------------------------------------------------------- scanning
def test_networks_to_scan():
    for text, prefix in (("192.168.0", "192.168.0"), ("192.168.0.0/24", "192.168.0"), ("10.0.5.77", "10.0.5"), ("172.16.4", "172.16.4")):
        assert scan.parse_network(text) == prefix
    for bad in ("8.8.8.0", "192.168.300", "172.32.0", "169.254.1", "127.0.0", "", "192.168", "x", None, "192.168.0.0/16"):
        with pytest.raises(ValueError):
            scan.parse_network(bad)


class Listener:
    """A TCP server on a free local port: Modbus-like (answers a read request) or not."""

    def __init__(self, answer: bytes | None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port, self.answer = self.sock.getsockname()[1], answer
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            try:
                req = c.recv(64)
                if not req:                              # a port check: connected and gone
                    c.close()
                    continue
                if self.answer == b"modbus":
                    c.sendall(struct.pack(">HHHBBBH", struct.unpack(">H", req[:2])[0], 0, 5, req[6], 4, 2, 0))
                elif self.answer:
                    c.sendall(self.answer)
            except OSError:
                pass
            c.close()

    def close(self):
        self.sock.close()


def test_scan_finds_only_what_speaks_modbus():
    good, web = Listener(b"modbus"), Listener(b"HTTP/1.1 400 Bad Request\r\n\r\n")
    try:
        assert scan.speaks_modbus("127.0.0.1", good.port, timeout=2)
        assert not scan.speaks_modbus("127.0.0.1", web.port, timeout=2)           # port open, but not a logger
        assert scan.scan_hosts(["127.0.0.1"], good.port) == ["127.0.0.1"]
        assert scan.scan_hosts(["127.0.0.1"], web.port) == []
    finally:
        good.close(); web.close()
    assert scan.scan_hosts(["127.0.0.1"], good.port) == []                         # nothing listens any more


# ---------------------------------------------------------------- the connection follows the saved address
def test_inverter_reconfigure_and_pause():
    inv = poller.Inverter("", 502, 1)
    with pytest.raises(ConnectionError, match="no inverter address"):
        inv.read_input(33000, 1)
    assert inv.reconfigure("192.168.0.105", 502, 1) is True and inv.host == "192.168.0.105"
    assert inv.reconfigure("192.168.0.105", 502, 1) is False                       # unchanged: the connection is kept
    assert inv.reconfigure("192.168.0.106", 1502, 2) is True and (inv.port, inv.unit) == (1502, 2)
    with inv.paused():
        assert not inv.client.connected
        assert inv.lock.locked()                                                   # nobody else talks to the logger meanwhile
    assert not inv.lock.locked()


# ---------------------------------------------------------------- the control API
class FakeInv:
    host, port, unit = "192.168.0.105", 502, 1

    def __init__(self):
        self.calls, self.paused_count = [], 0

    def reconfigure(self, host, port, unit):
        self.calls.append((host, port, unit))
        self.host, self.port, self.unit = host, port, unit

    def paused(self):
        outer = self

        class P:
            def __enter__(self_inner): outer.paused_count += 1
            def __exit__(self_inner, *a): return False
        return P()


class FakeDevices:
    invalidated = 0

    def read(self):
        return {}

    def invalidate(self):
        self.invalidated += 1


def server(tmp_path, env_host="192.168.0.105"):
    inv, audits = FakeInv(), []
    ctl = control.Controller(inv, control.Config({"INFLUX_TOKEN": "secret"}), audit=lambda a, **f: audits.append((a, f)))
    ctl.addresses = address.AddressStore(str(tmp_path / "inverter.json"), env_host, 502, 1)
    ctl.poll_status, ctl.devices = address.PollStatus(), FakeDevices()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), control.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return ctl, inv, audits, srv


def call(srv, path, body=None, token="secret"):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}{path}", method="POST" if body is not None else "GET",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_read_the_address(tmp_path):
    ctl, inv, audits, srv = server(tmp_path)
    try:
        status, body = call(srv, "/inverter")
        assert status == 200 and body["host"] == "192.168.0.105" and body["source"] == "env" and body["configured"] is True
        assert body["saved"] is False and body["env_host"] == "192.168.0.105" and body["connected"] is False
        assert call(srv, "/inverter", token="wrong")[0] == 401
    finally:
        srv.shutdown()


def test_change_the_address_without_a_restart(tmp_path):
    ctl, inv, audits, srv = server(tmp_path)
    try:
        status, body = call(srv, "/inverter", {"host": "192.168.1.50", "port": 502, "unit": 1})
        assert status == 200 and body["host"] == "192.168.1.50" and body["source"] == "saved" and body["saved"] is True
        assert "192.168.0.105 → 192.168.1.50" in body["applied"]
        assert inv.calls == [("192.168.1.50", 502, 1)] and ctl.devices.invalidated == 1       # the live connection follows at once
        assert audits and audits[0][0] == "inverter_address" and audits[0][1]["verified"] is True
        status, body = call(srv, "/inverter", {"host": None})                                    # back to INVERTER_HOST
        assert status == 200 and body["host"] == "192.168.0.105" and body["source"] == "env" and inv.calls[-1] == ("192.168.0.105", 502, 1)
    finally:
        srv.shutdown()


def test_bad_addresses_change_nothing(tmp_path):
    ctl, inv, audits, srv = server(tmp_path)
    try:
        for body in ({"host": "127.0.0.1"}, {"host": "http://x"}, {"host": "192.168.1.5", "port": 99999}, {"host": "192.168.1.5", "unit": 0},
                     {"port": 502}, {"host": "192.168.1.5", "extra": 1}, {"host": 5}):
            status, answer = call(srv, "/inverter", body)
            assert status == 400, body
        assert inv.calls == [] and audits == [] and ctl.addresses.value()["source"] == "env"
    finally:
        srv.shutdown()


def test_scan_endpoint(tmp_path, monkeypatch):
    ctl, inv, audits, srv = server(tmp_path)
    seen = []
    monkeypatch.setattr(scan, "scan_network", lambda net, port, unit, **kw: seen.append((net, port, unit)) or ["192.168.0.105"])
    try:
        status, body = call(srv, "/inverter/scan", {"network": "192.168.0"})
        assert status == 200 and body == {"network": "192.168.0.0/24", "found": ["192.168.0.105"]}
        assert seen == [("192.168.0", 502, 1)] and inv.paused_count == 1               # the poller's connection was set aside
        assert call(srv, "/inverter/scan", {"network": "8.8.8.0"})[0] == 400
        assert call(srv, "/inverter/scan", {"net": "192.168.0"})[0] == 400
        ctl.scan_lock.acquire()
        try:
            assert call(srv, "/inverter/scan", {"network": "192.168.0"})[0] == 409     # one scan at a time
        finally:
            ctl.scan_lock.release()
    finally:
        srv.shutdown()


def test_unconfigured_poller_reports_it(tmp_path):
    ctl, inv, audits, srv = server(tmp_path, env_host="")
    try:
        status, body = call(srv, "/inverter")
        assert status == 200 and body["configured"] is False and body["source"] == "unset" and body["host"] == ""
    finally:
        srv.shutdown()
