from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}


class Control:
    def __init__(self, down=False):
        self.down, self.calls = down, []

    def outages(self):
        if self.down:
            raise PollerUnavailable("refused")
        return 200, {"configured": True, "group": "3.1", "days": [], "next": None, "battery": None}

    def outage_config(self):
        return 200, {"source": "yasno-kyiv", "group": "3.1", "alerts": {"before": True, "short": True, "changes": True}, "sources": []}

    def set_outage_config(self, source, group, alerts):
        self.calls.append(("set", source, group, alerts))
        return 200, {"source": source, "group": group, "alerts": alerts, "sources": []}

    def outage_lookup(self, kind, **params):
        self.calls.append((kind, params))
        return 200, {"streets": [{"id": 911, "value": "вул. Хрещатик"}], "houses": [{"id": 39986, "value": "22"}], "group": {"group": "20.1"}}[kind]


def client(control, auth="me:pw", settings_only=True):
    app = create_app(repo=object(), control=control, now=lambda: datetime.now(timezone.utc),
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth, auth_settings_only=settings_only))
    return TestClient(app)


def test_the_overview_reads_the_schedule_without_the_login():
    c = client(Control())
    r = c.get("/api/outage-schedule")
    assert r.status_code == 200 and r.json()["group"] == "3.1"
    assert client(Control(down=True)).get("/api/outage-schedule").status_code == 502


def test_settings_and_the_address_lookup_go_through_the_poller_behind_the_login():
    ctl = Control()
    c = client(ctl)
    assert c.get("/api/settings/outage-schedule").status_code == 401
    body = c.get("/api/settings/outage-schedule", auth=AUTH).json()
    assert body["group"] == "3.1" and body["web"]["can_change"] is True
    assert c.get("/api/settings/outage-schedule/streets?source=yasno-kyiv&q=Хрещ", auth=AUTH).json() == [{"id": 911, "value": "вул. Хрещатик"}]
    assert c.get("/api/settings/outage-schedule/houses?source=yasno-kyiv&street=911&q=22", auth=AUTH).json() == [{"id": 39986, "value": "22"}]
    assert c.get("/api/settings/outage-schedule/group?source=yasno-kyiv&street=911&house=39986", auth=AUTH).json() == {"group": "20.1"}
    assert ("houses", {"source": "yasno-kyiv", "street": 911, "q": "22"}) in ctl.calls
    assert c.get("/api/settings/outage-schedule/houses?source=yasno-kyiv&street=abc&q=22", auth=AUTH).status_code == 422
    r = c.post("/api/settings/outage-schedule", json={"source": "yasno-kyiv", "group": "20.1", "alerts": {"before": True}}, headers=JSON, auth=AUTH)
    assert r.status_code == 200 and ctl.calls[-1] == ("set", "yasno-kyiv", "20.1", {"before": True})
    assert c.post("/api/settings/outage-schedule", json={"source": "x"}, headers=JSON, auth=AUTH).status_code == 400
    assert c.post("/api/settings/outage-schedule", json={"source": None, "group": None, "alerts": {}},
                  headers={**JSON, "Origin": "http://evil.example"}, auth=AUTH).status_code == 403
