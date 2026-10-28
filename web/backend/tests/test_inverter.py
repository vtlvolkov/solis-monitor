from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
STATE = {"host": "192.168.0.105", "port": 502, "unit": 1, "source": "env", "configured": True, "env_host": "192.168.0.105",
         "saved": False, "connected": True, "last_ok_age_s": 4.0, "error": None, "failures": 0}


class FakeControl:
    def __init__(self, state=None, down=False, answer_status=200):
        self.state, self.down, self.answer_status = dict(state or STATE), down, answer_status
        self.set_calls, self.scan_calls = [], []

    def _guard(self):
        if self.down:
            raise PollerUnavailable("connection refused")

    def inverter_address(self):
        self._guard()
        return 200, dict(self.state)

    def set_inverter_address(self, host, port, unit):
        self._guard()
        self.set_calls.append((host, port, unit))
        if self.answer_status != 200:
            return self.answer_status, {"detail": "That address cannot be the logger."}
        self.state = {**self.state, "host": host or "192.168.0.105", "port": port, "unit": unit, "source": "saved" if host else "env",
                      "configured": True, "saved": bool(host)}
        return 200, {**self.state, "applied": "Inverter address: a → b"}

    def scan_for_inverter(self, network):
        self._guard()
        self.scan_calls.append(network)
        if self.answer_status != 200:
            return self.answer_status, {"detail": "Only home networks can be scanned."}
        return 200, {"network": network + ".0/24", "found": ["192.168.0.105"]}


def client(control=None, auth="me:pw", **kw):
    app = create_app(repo=object(), control=control or FakeControl(),
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth, **kw),
                     now=lambda: datetime.now(timezone.utc))
    return TestClient(app)


def post(c, path, body, headers=JSON, auth=AUTH):
    return c.post(f"/api/settings/inverter{path}", json=body, headers=headers, auth=auth)


def test_read_the_address_with_what_the_page_needs():
    body = client().get("/api/settings/inverter", auth=AUTH).json()
    assert body["host"] == "192.168.0.105" and body["connected"] is True and body["configured"] is True
    assert body["web"] == {"can_change": True, "reason": None} and body["scan_hint"] == "192.168.0"


def test_scan_hint_comes_from_the_logger_else_from_the_browsers_address_never_the_proxy():
    unset = {**STATE, "host": "", "configured": False, "source": "unset"}
    c = client(FakeControl(unset))
    assert c.get("/api/settings/inverter", auth=AUTH).json()["scan_hint"] is None                   # nothing to go on
    h = c.get("/api/settings/inverter", auth=AUTH, headers={"X-Forwarded-For": "192.168.7.20, 10.21.0.5"}).json()
    assert h["scan_hint"] == "192.168.7"
    h = c.get("/api/settings/inverter", auth=AUTH, headers={"X-Forwarded-For": "8.8.8.8"}).json()   # a public address is no home network
    assert h["scan_hint"] is None


def test_the_page_can_read_but_not_change_without_a_password():
    c = client(auth="")
    body = c.get("/api/settings/inverter").json()
    assert body["web"]["can_change"] is False and "password" in body["web"]["reason"]
    assert c.post("/api/settings/inverter", json={"host": "192.168.0.9"}, headers=JSON).status_code == 403
    assert c.post("/api/settings/inverter/scan", json={"network": "192.168.0"}, headers=JSON).status_code == 403


def test_change_the_address():
    ctl = FakeControl()
    c = client(ctl)
    r = post(c, "", {"host": "192.168.1.50", "port": 1502, "unit": 2})
    assert r.status_code == 200 and r.json()["host"] == "192.168.1.50" and r.json()["web"]["can_change"] is True
    assert ctl.set_calls == [("192.168.1.50", 1502, 2)]
    post(c, "", {"host": "192.168.1.51"})                                                              # port and unit default
    assert ctl.set_calls[-1] == ("192.168.1.51", 502, 1)
    post(c, "", {"host": None})                                                                        # back to INVERTER_HOST
    assert ctl.set_calls[-1] == (None, 502, 1)


def test_refusals_from_the_poller_reach_the_page():
    r = post(client(FakeControl(answer_status=400)), "", {"host": "127.0.0.1"})
    assert r.status_code == 400 and "cannot be the logger" in r.json()["detail"]
    assert post(client(FakeControl(down=True)), "", {"host": "192.168.0.9"}).status_code == 502
    assert client(FakeControl(down=True)).get("/api/settings/inverter", auth=AUTH).status_code == 502


def test_only_well_formed_bodies_get_through():
    ctl = FakeControl()
    c = client(ctl)
    for body in ({}, {"port": 502}, {"host": "1.2.3.4", "extra": 1}, [], "x"):
        assert post(c, "", body).status_code == 400, body
    assert ctl.set_calls == []


def test_change_requests_must_be_same_origin_json():
    ctl = FakeControl()
    c = client(ctl)
    assert post(c, "", {"host": "192.168.0.9"}, headers={"Content-Type": "text/plain"}).status_code == 415
    assert post(c, "", {"host": "192.168.0.9"}, headers={**JSON, "Origin": "https://evil.example"}).status_code == 403
    assert post(c, "", {"host": "192.168.0.9"}, headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post(c, "/scan", {"network": "192.168.0"}, headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert ctl.set_calls == [] and ctl.scan_calls == []


def test_scan():
    ctl = FakeControl()
    c = client(ctl)
    r = post(c, "/scan", {"network": "192.168.0"})
    assert r.status_code == 200 and r.json() == {"network": "192.168.0.0/24", "found": ["192.168.0.105"]}
    assert ctl.scan_calls == ["192.168.0"]
    for body in ({}, {"network": 5}, {"network": "192.168.0", "x": 1}, {"net": "192.168.0"}):
        assert post(c, "/scan", body).status_code == 400, body
    assert post(client(FakeControl(answer_status=400)), "/scan", {"network": "8.8.8"}).status_code == 400


def test_the_login_covers_the_inverter_endpoints_in_settings_only_mode():
    c = client(auth="me:pw", auth_settings_only=True)
    assert c.get("/api/settings/inverter").status_code == 401
    assert c.post("/api/settings/inverter", json={"host": "192.168.0.9"}, headers=JSON).status_code == 401
    assert c.post("/api/settings/inverter/scan", json={"network": "192.168.0"}, headers=JSON).status_code == 401
