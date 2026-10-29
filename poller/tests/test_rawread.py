import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import control
import rawread
from control import Config, ControlError, Controller


class FakeInverter:
    """Answers raw reads from a dict; registers listed in `refuse` come back as a Modbus exception."""
    unit = 1

    def __init__(self, regs=None, refuse=None):
        self.regs, self.refuse, self.calls = regs or {}, refuse or {}, []

    def raw_read(self, function, address, count):
        self.calls.append((function, address, count))
        for a in range(address, address + count):
            if a in self.refuse:
                return None, self.refuse[a]
        return [self.regs.get(a, 0) for a in range(address, address + count)], None

    def write_holding(self, *a):
        raise AssertionError("the Debug tab never writes")

    write_block = write_holding


def make(**kw):
    inv = FakeInverter(**kw)
    return Controller(inv, Config({"INFLUX_TOKEN": "secret"})), inv


# ---------------------------------------------------------------- what may be asked
def test_limits():
    assert rawread.check(4, 33000, 50) == (4, 33000, 50)
    assert rawread.check(3, 0, 1) == (3, 0, 1)
    assert rawread.check(3, 65535, 1) == (3, 65535, 1)


@pytest.mark.parametrize("args", [
    (6, 43110, 1), (16, 43110, 1), (0, 1, 1), (True, 1, 1), ("4", 1, 1), (None, 1, 1),         # no write or odd functions
    (4, -1, 1), (4, 65536, 1), (4, 1.5, 1), (4, "1", 1), (4, True, 1), (4, None, 1),
    (4, 1, 0), (4, 1, 51), (4, 1, -3), (4, 1, 2.0), (4, 1, True), (4, 65535, 2),
])
def test_bad_requests_are_refused(args):
    with pytest.raises(ValueError):
        rawread.check(*args)


def test_only_reads_exist():
    assert set(rawread.FUNCTIONS) == {3, 4}
    assert not hasattr(rawread, "write")


# ---------------------------------------------------------------- the frames shown on screen
def test_request_and_response_frames():
    assert rawread.request_pdu(4, 33004, 2) == "04 80 EC 00 02"
    assert rawread.response_pdu(4, [0x1234, 0x00FF]) == "04 04 12 34 00 FF"
    assert rawread.exception_pdu(3, 2) == "83 02"


# ---------------------------------------------------------------- the controller
def test_read_returns_registers_frames_and_timing():
    ctl, inv = make(regs={33049: 2300, 33050: 65535})
    out = ctl.debug_read(4, 33049, 2)
    assert out["ok"] is True and out["registers"] == [2300, 65535]
    assert out["request"] == "04 81 19 00 02" and out["response"] == "04 04 08 FC FF FF"
    assert out["function"] == 4 and out["function_name"] == "Read input registers" and out["unit"] == 1
    assert out["address"] == 33049 and out["count"] == 2 and out["ms"] >= 0
    assert inv.calls == [(4, 33049, 2)]


def test_a_modbus_exception_is_an_answer_not_an_error():
    ctl, _ = make(refuse={50000: 2})
    out = ctl.debug_read(3, 50000, 1)
    assert out["ok"] is False and out["exception_code"] == 2 and out["exception"] == "Illegal data address"
    assert out["response"] == "83 02" and "registers" not in out
    assert ctl.debug_read(3, 50000, 1)["request"] == "03 C3 50 00 01"
    out = make(refuse={7: 99})[0].debug_read(3, 7, 1)
    assert out["exception"] == "Exception 99"


def test_bad_input_is_a_400_and_never_reaches_the_inverter():
    ctl, inv = make()
    with pytest.raises(ControlError) as e:
        ctl.debug_read(6, 43110, 1)
    assert e.value.status == 400 and e.value.args[0].endswith(".")
    with pytest.raises(ControlError):
        ctl.debug_read(4, 1, 500)
    assert inv.calls == []


# ---------------------------------------------------------------- over HTTP
@pytest.fixture
def api():
    ctl, inv = make(regs={33000: 1})
    ctl.cfg.port = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), control.make_handler(ctl))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", inv
    server.shutdown()


def post(base, body, token="secret"):
    req = urllib.request.Request(base + "/debug/read", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_read(api):
    base, inv = api
    status, body = post(base, {"function": 4, "address": 33000, "count": 1})
    assert status == 200 and body["registers"] == [1]


def test_http_needs_the_token_and_exact_keys(api):
    base, inv = api
    assert post(base, {"function": 4, "address": 33000, "count": 1}, token="wrong")[0] == 401
    assert post(base, {"function": 4, "address": 33000})[0] == 400
    assert post(base, {"function": 4, "address": 33000, "count": 1, "value": 5})[0] == 400
    assert post(base, {"function": 6, "address": 43110, "count": 1})[0] == 400
    assert inv.calls == []
