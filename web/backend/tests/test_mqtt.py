"""The MQTT settings routes: behind the login, same-origin JSON only, only the known keys, the poller does the rest."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
VIEW = {"enabled": True, "host": "192.168.0.20", "port": 1883, "username": "solis", "password_set": True, "tls": False,
        "prefix": "solis/home", "interval_s": 10, "discovery": True, "source": "saved",
        "status": {"state": "connected", "error": None, "last_publish_age_s": 3.0, "sent": 12, "dropped": 0},
        "topics": {"state": "solis/home/state", "event": "solis/home/event", "availability": "solis/home/status", "discovery": None},
        "last_message": None}
FORM = {"enabled": True, "host": "192.168.0.20", "port": 1883, "username": "solis", "password": "secret", "tls": False,
        "prefix": "solis/home", "interval_s": 10, "discovery": True}


class FakeControl:
    def __init__(self, down=False):
        self.down, self.sets, self.tests = down, [], []

    def mqtt(self):
        if self.down:
            raise PollerUnavailable("connection refused")
        return 200, dict(VIEW)

    def set_mqtt(self, body):
        self.sets.append(body)
        if body.get("host") == "bad host":
            return 400, {"detail": "Use just the IP address or host name of the broker, without mqtt://, a port or spaces."}
        return 200, dict(VIEW)

    def test_mqtt(self, body):
        self.tests.append(body)
        return 200, {"ok": True, "error": None, "ms": 9}


def client(control=None, auth="me:pw"):
    app = create_app(repo=object(), control=control or FakeControl(),
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth, auth_settings_only=True),
                     now=lambda: datetime.now(timezone.utc))
    return TestClient(app)


def test_reading_it_needs_the_login_and_says_whether_it_can_change():
    c = client()
    assert c.get("/api/settings/mqtt").status_code == 401
    body = c.get("/api/settings/mqtt", auth=AUTH).json()
    assert body["host"] == "192.168.0.20" and body["web"] == {"can_change": True, "reason": None} and "password" not in body


def test_a_change_and_a_test_go_to_the_poller_as_sent(caplog):
    ctl = FakeControl()
    c = client(ctl)
    r = c.post("/api/settings/mqtt", json=FORM, headers=JSON, auth=AUTH)
    assert r.status_code == 200 and ctl.sets == [FORM] and r.json()["web"]["can_change"] is True
    assert "secret" not in caplog.text                                       # the password is never logged
    assert c.post("/api/settings/mqtt", json={"enabled": False}, headers=JSON, auth=AUTH).status_code == 200
    r = c.post("/api/settings/mqtt/test", json=FORM, headers=JSON, auth=AUTH)
    assert r.json() == {"ok": True, "error": None, "ms": 9} and ctl.tests == [FORM]
    bad = c.post("/api/settings/mqtt", json={**FORM, "host": "bad host"}, headers=JSON, auth=AUTH)
    assert bad.status_code == 400 and "IP address" in bad.json()["detail"]


def test_unknown_keys_cross_site_posts_and_no_password_are_refused():
    ctl = FakeControl()
    c = client(ctl)
    assert c.post("/api/settings/mqtt", json={**FORM, "subscribe": "#"}, headers=JSON, auth=AUTH).status_code == 400
    assert c.post("/api/settings/mqtt", json={"host": "x"}, headers=JSON, auth=AUTH).status_code == 400      # no "enabled"
    assert c.post("/api/settings/mqtt", json=FORM, headers={**JSON, "Origin": "https://evil.example"}, auth=AUTH).status_code == 403
    assert c.post("/api/settings/mqtt", json=FORM, headers=JSON).status_code == 401
    assert client(ctl, auth="").post("/api/settings/mqtt", json=FORM, headers=JSON).status_code == 403
    assert ctl.sets == []


def test_a_poller_that_does_not_answer_is_a_502():
    assert client(FakeControl(down=True)).get("/api/settings/mqtt", auth=AUTH).status_code == 502
