from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

SETTINGS = {"grid_charging": True, "max_charge_current_a": 100.0, "max_discharge_current_a": 100.0,
            "control": {"enabled": True, "limits": {"max_charge_current_a": 100}}}


class FakeControl:
    def __init__(self, status=200, body=None, down=False, enabled=True):
        self.status, self.down = status, down
        self.body = body or {**SETTINGS, "control": {**SETTINGS["control"], "enabled": enabled}}
        self.applied = []

    def get(self):
        if self.down:
            raise PollerUnavailable("connection refused")
        return self.status, dict(self.body)

    def apply(self, setting, value):
        if self.down:
            raise PollerUnavailable("connection refused")
        self.applied.append((setting, value))
        return self.status, {"applied": f"{setting} -> {value}", "verified": True}


    def devices(self):
        if self.down:
            raise PollerUnavailable("connection refused")
        return 200, {"inverter": {"serial": "1234567890123456"}}


class Repo:
    def control_log(self, limit):
        self.limit = limit
        return [{"time": "2026-10-18T15:00:00+00:00", "action": "grid_charging", "summary": "x", "verified": True}]


AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}


def client(control, auth="me:pw", repo=None):
    app = create_app(repo=repo or Repo(), control=control,
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth),
                     now=lambda: datetime.now(timezone.utc))
    return TestClient(app)


def post(c, body, headers=JSON, auth=AUTH):
    return c.post("/api/settings", json=body, headers=headers, auth=auth)


def test_get_reports_can_change():
    body = client(FakeControl()).get("/api/settings", auth=AUTH).json()
    assert body["grid_charging"] is True
    assert body["web"] == {"auth_enabled": True, "can_change": True, "reason": None}


def test_get_explains_disabled_control():
    body = client(FakeControl(enabled=False)).get("/api/settings", auth=AUTH).json()
    assert body["web"]["can_change"] is False and "CONTROL_ENABLED" in body["web"]["reason"]


def test_get_without_basic_auth_is_view_only():
    body = client(FakeControl(), auth="").get("/api/settings").json()
    assert body["web"]["can_change"] is False and "WEB_BASIC_AUTH" in body["web"]["reason"]


def test_post_refused_without_basic_auth_configured():
    ctl = FakeControl()
    r = post(client(ctl, auth=""), {"setting": "grid_charging", "value": False}, auth=None)
    assert r.status_code == 403 and ctl.applied == []


def test_post_requires_credentials():
    ctl = FakeControl()
    assert post(client(ctl), {"setting": "grid_charging", "value": False}, auth=None).status_code == 401
    assert post(client(ctl), {"setting": "grid_charging", "value": False}, auth=("me", "x")).status_code == 401
    assert ctl.applied == []


def test_post_applies_valid_change():
    ctl = FakeControl()
    r = post(client(ctl), {"setting": "max_charge_current_a", "value": 30})
    assert r.status_code == 200 and r.json()["verified"] is True
    assert ctl.applied == [("max_charge_current_a", 30)]


@pytest.mark.parametrize("body", [
    {"setting": "work_mode", "value": 1},
    {"setting": "grid_charging", "value": "false"},
    {"setting": "grid_charging", "value": 0},
    {"setting": "max_charge_current_a", "value": True},
    {"setting": "max_charge_current_a", "value": "30"},
    {"setting": "max_charge_current_a"},
    [1, 2],
])
def test_post_rejects_bad_body(body):
    ctl = FakeControl()
    assert post(client(ctl), body).status_code == 400
    assert ctl.applied == []


def test_post_rejects_form_and_cross_site():
    ctl, c = FakeControl(), None
    c = client(ctl)
    form = c.post("/api/settings", data={"setting": "grid_charging", "value": "false"}, auth=AUTH)
    assert form.status_code == 415
    body = {"setting": "grid_charging", "value": False}
    assert post(c, body, headers={**JSON, "Origin": "http://evil.example"}).status_code == 403
    assert post(c, body, headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert ctl.applied == []
    assert post(c, body, headers={**JSON, "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_poller_errors_are_passed_through():
    ctl = FakeControl(status=429)
    r = post(client(ctl), {"setting": "grid_charging", "value": False})
    assert r.status_code == 429


def test_poller_down():
    c = client(FakeControl(down=True))
    assert c.get("/api/settings", auth=AUTH).status_code == 502
    assert post(c, {"setting": "grid_charging", "value": False}).status_code == 502


def test_change_log():
    repo = Repo()
    r = client(FakeControl(), repo=repo).get("/api/settings/log?limit=5", auth=AUTH)
    assert r.status_code == 200 and r.json()["changes"][0]["action"] == "grid_charging" and repo.limit == 5
    assert client(FakeControl()).get("/api/settings/log?limit=500", auth=AUTH).status_code == 400


def test_devices_proxy():
    r = client(FakeControl(), auth="").get("/api/devices")
    assert r.status_code == 200 and r.json()["inverter"]["serial"] == "1234567890123456"
    assert client(FakeControl(down=True), auth="").get("/api/devices").status_code == 502
    assert client(FakeControl()).get("/api/devices").status_code == 401        # behind Basic auth when set


def test_settings_only_auth_leaves_dashboard_open():
    app = create_app(repo=Repo(), control=FakeControl(), now=lambda: datetime.now(timezone.utc),
                     settings=Settings(static_dir="/nonexistent", basic_auth="me:pw", auth_settings_only=True))
    c = TestClient(app)
    assert c.get("/api/devices").status_code == 200
    assert c.get("/healthz").status_code == 200
    for path in ("/api/settings", "/api/settings/log?limit=5"):
        r = c.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
        assert c.get(path, auth=("me", "x")).status_code == 401
        assert c.get(path, auth=AUTH).status_code == 200
    assert c.post("/api/settings", json={"setting": "grid_charging", "value": False}, headers=JSON).status_code == 401
    assert post(c, {"setting": "grid_charging", "value": False}).status_code == 200
