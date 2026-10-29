from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
BODY = {"function": 4, "address": 33049, "count": 2}


class FakeControl:
    def __init__(self, down=False, status=200):
        self.down, self.status, self.calls = down, status, []

    def debug_read(self, function, address, count):
        if self.down:
            raise PollerUnavailable("connection refused")
        self.calls.append((function, address, count))
        if self.status != 200:
            return self.status, {"detail": "The count must be a whole number from 1 to 50."}
        return 200, {"ok": True, "registers": [1, 2], "request": "04 81 19 00 02", "response": "04 04 00 01 00 02"}


def client(control=None, auth="me:pw"):
    app = create_app(repo=object(), control=control or FakeControl(),
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth),
                     now=lambda: datetime.now(timezone.utc))
    return TestClient(app)


def post(c, body=BODY, headers=JSON, auth=AUTH):
    return c.post("/api/settings/debug/read", json=body, headers=headers, auth=auth)


def test_a_read_is_passed_to_the_poller():
    ctl = FakeControl()
    r = post(client(ctl))
    assert r.status_code == 200 and r.json()["registers"] == [1, 2]
    assert ctl.calls == [(4, 33049, 2)]


def test_needs_a_password_to_be_configured():
    ctl = FakeControl()
    assert post(client(ctl, auth=""), auth=None).status_code == 403
    assert ctl.calls == []


def test_needs_the_login():
    ctl = FakeControl()
    assert post(client(ctl), auth=None).status_code == 401
    assert ctl.calls == []


def test_cross_site_and_non_json_requests_are_refused():
    ctl = FakeControl()
    c = client(ctl)
    assert post(c, headers={**JSON, "Origin": "https://evil.example"}).status_code == 403
    assert post(c, headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/settings/debug/read", content="x", headers={"Content-Type": "text/plain"}, auth=AUTH).status_code == 415
    assert ctl.calls == []


def test_only_the_three_fields_are_accepted():
    ctl = FakeControl()
    c = client(ctl)
    for body in ({}, {"function": 4, "address": 1}, {**BODY, "value": 5}, [1, 2, 3], "x"):
        assert post(c, body).status_code == 400
    assert c.post("/api/settings/debug/read", content="{nope", headers=JSON, auth=AUTH).status_code == 400
    assert ctl.calls == []


def test_the_poller_decides_what_is_valid():
    r = post(client(FakeControl(status=400)))
    assert r.status_code == 400 and "1 to 50" in r.json()["detail"]


def test_poller_down():
    r = post(client(FakeControl(down=True)))
    assert r.status_code == 502 and r.json()["detail"] == "Poller unreachable"
