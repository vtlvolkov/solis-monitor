from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.control import PollerUnavailable
from app.main import create_app

AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
BODY = {"lat": 50.45, "lon": 30.52, "arrays": [{"kwp": 4.2, "tilt": 35, "azimuth": 180}], "losses": 14,
        "correct": True, "telegram": False, "below_pct": 50, "overview": True}


class Control:
    def __init__(self, down=False):
        self.down, self.calls = down, []

    def forecast(self):
        if self.down:
            raise PollerUnavailable("refused")
        return 200, {"configured": True, "days": [{"date": "2026-11-19", "kwh": 2.9}]}

    def forecast_hours(self):
        return 200, {"configured": True, "hours": [[1763456400, 812.5]]}

    def forecast_config(self):
        return 200, {"config": BODY, "configured": True, "error": None}

    def set_forecast_config(self, body):
        self.calls.append(body)
        if body["lat"] == 91:
            return 400, {"detail": "Latitude must be a number from -90 to 90."}
        return 200, {"config": body, "configured": body["lat"] is not None, "error": None}


def client(control, auth="me:pw"):
    app = create_app(repo=object(), control=control, now=lambda: datetime.now(timezone.utc),
                     settings=Settings(static_dir="/nonexistent", basic_auth=auth, auth_settings_only=True))
    return TestClient(app)


def test_the_overview_and_charts_read_the_forecast_without_the_login():
    c = client(Control())
    assert c.get("/api/forecast").json()["days"][0]["kwh"] == 2.9
    assert c.get("/api/forecast/hours").json()["hours"] == [[1763456400, 812.5]]
    assert client(Control(down=True)).get("/api/forecast").status_code == 502


def test_the_settings_go_through_the_poller_behind_the_login():
    ctl = Control()
    c = client(ctl)
    assert c.get("/api/settings/forecast").status_code == 401
    body = c.get("/api/settings/forecast", auth=AUTH).json()
    assert body["config"]["arrays"][0]["kwp"] == 4.2 and body["web"]["can_change"] is True
    r = c.post("/api/settings/forecast", json=BODY, headers=JSON, auth=AUTH)
    assert r.status_code == 200 and ctl.calls[-1] == BODY
    r = c.post("/api/settings/forecast", json={**BODY, "lat": None}, headers=JSON, auth=AUTH)
    assert r.status_code == 200 and r.json()["configured"] is False
    r = c.post("/api/settings/forecast", json={**BODY, "lat": 91}, headers=JSON, auth=AUTH)
    assert r.status_code == 400 and r.json()["detail"].startswith("Latitude")
    assert c.post("/api/settings/forecast", json={**BODY, "x": 1}, headers=JSON, auth=AUTH).status_code == 400
    assert c.post("/api/settings/forecast", json={"lat": 1}, headers=JSON, auth=AUTH).status_code == 400
    assert c.post("/api/settings/forecast", json=BODY, headers=JSON).status_code == 401
    assert c.post("/api/settings/forecast", json=BODY, headers={**JSON, "Origin": "http://evil.example"}, auth=AUTH).status_code == 403
    assert len(ctl.calls) == 3
