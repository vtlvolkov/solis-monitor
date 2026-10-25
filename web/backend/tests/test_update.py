import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import updates
from app.config import Settings
from app.main import create_app
from app.updates import Bridge, ReleaseFeed, ReleaseLookupError, fetch_latest_release, is_newer, next_delay, parse_version

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}


def release(tag="v0.1.4"):
    return {"tag": tag, "url": f"https://github.com/o/r/releases/tag/{tag}", "published": "2026-10-25T10:00:00Z"}


class Env:
    """An app wired to a temp control folder and a fake GitHub."""

    def __init__(self, tmp_path, version="v0.1.3", auth="me:pw", bridge=True, latest=None, lookup_error=False):
        self.dir, self.calls, self.t = tmp_path, 0, NOW
        self.latest, self.lookup_error = latest or release(), lookup_error
        if bridge:
            (tmp_path / "bridge.json").write_text(json.dumps({"enabled": True, "version": version}))
        s = Settings(static_dir="/nonexistent", basic_auth=auth, solis_version=version, solis_repo="o/r", update_dir=str(tmp_path))
        self.client = TestClient(create_app(repo=object(), control=object(), settings=s, now=lambda: self.t, releases=self.lookup))

    def lookup(self):
        self.calls += 1
        if self.lookup_error:
            raise ReleaseLookupError("offline")
        return self.latest

    def get(self):
        return self.client.get("/api/settings/update", auth=AUTH).json()

    def post(self, path="", headers=JSON, body=None):
        return self.client.post(f"/api/settings/update{path}", headers=headers, json=body or {}, auth=AUTH)


def test_versions():
    assert parse_version("v0.1.3") == (0, 1, 3) and parse_version("v10.0.12") == (10, 0, 12)
    for bad in ("0.1.3", "v1.2", "v1.2.3-rc1", "latest", "", None, 5, "v1.2.3\n"):
        assert parse_version(bad) is None, bad
    assert is_newer("v0.1.10", "v0.1.9") and is_newer("v1.0.0", "v0.9.9")
    assert not is_newer("v0.1.3", "v0.1.3") and not is_newer("v0.1.2", "v0.1.3")
    assert not is_newer("garbage", "v0.1.3") and not is_newer("v0.1.4", "")


def test_version_endpoint_is_open(tmp_path):
    e = Env(tmp_path)
    assert e.client.get("/api/version", auth=AUTH).json() == {"version": "v0.1.3", "installed": True, "update": {"available": False, "tag": None}}
    src = Env(tmp_path, version="")
    assert src.client.get("/api/version", auth=AUTH).json() == {"version": None, "installed": False, "update": {"available": False, "tag": None}}
    e.post("/check")                                                   # a manual check also feeds the badge
    assert e.client.get("/api/version", auth=AUTH).json()["update"] == {"available": True, "tag": "v0.1.4"}


def test_state_before_any_check(tmp_path):
    body = Env(tmp_path).get()
    assert body["version"] == "v0.1.3" and body["can_update"] and body["reason"] is None
    assert body["latest"] is None and body["busy"] is False and body["status"] is None


def test_check_finds_a_newer_release(tmp_path):
    e = Env(tmp_path)
    body = e.post("/check").json()
    assert body["latest"]["tag"] == "v0.1.4" and body["latest"]["available"] is True
    assert body["latest"]["url"].startswith("https://github.com/")
    assert e.get()["latest"]["available"] is True                      # remembered for the next page load


def test_check_when_up_to_date(tmp_path):
    e = Env(tmp_path, latest=release("v0.1.3"))
    assert e.post("/check").json()["latest"]["available"] is False


def test_checks_are_rate_limited_and_refreshed_later(tmp_path):
    e = Env(tmp_path)
    e.post("/check"); e.post("/check")
    assert e.calls == 1                                                # within 30 s: no second GitHub request
    e.t = NOW + timedelta(seconds=31)
    e.latest = release("v0.1.5")
    assert e.post("/check").json()["latest"]["tag"] == "v0.1.5" and e.calls == 2


def test_lookup_failure_is_a_clear_502(tmp_path):
    r = Env(tmp_path, lookup_error=True).post("/check")
    assert r.status_code == 502 and "GitHub" in r.json()["detail"]


def test_update_writes_a_request_for_the_host(tmp_path):
    e = Env(tmp_path)
    r = e.post()
    assert r.status_code == 202 and r.json()["busy"] is True
    req = json.loads((tmp_path / "update-request").read_text())
    assert req["to"] == "v0.1.4" and req["requested_at"].startswith("2026-10-25T12:00")
    assert oct((tmp_path / "update-request").stat().st_mode & 0o777) == "0o666"
    assert not (tmp_path / ".update-request.tmp").exists()
    assert e.post().status_code == 409 and "already running" in e.post().json()["detail"]     # one at a time


def test_update_is_refused_while_the_host_reports_running(tmp_path):
    e = Env(tmp_path)
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "from": "v0.1.3", "to": "v0.1.4", "started": "2026-10-25T11:59:00Z"}))
    assert e.get()["busy"] is True
    assert e.post().status_code == 409 and not (tmp_path / "update-request").exists()


def test_a_stuck_running_status_expires(tmp_path):
    e = Env(tmp_path)
    started = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "started": started, "to": "v0.1.4"}))
    assert e.get()["busy"] is True
    e.t = NOW + timedelta(minutes=40)
    body = e.get()
    assert body["status"]["state"] == "failed" and body["busy"] is False
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "started": "garbage"}))
    assert e.get()["status"]["state"] == "failed"                      # no usable start time: do not wait


def test_update_when_already_current(tmp_path):
    e = Env(tmp_path, latest=release("v0.1.3"))
    r = e.post()
    assert r.status_code == 409 and "newest" in r.json()["detail"] and not (tmp_path / "update-request").exists()


def test_update_needs_the_updater_on_the_server(tmp_path):
    e = Env(tmp_path, bridge=False)
    body = e.get()
    assert body["can_update"] is False and "dashboard-updates on" in body["reason"]
    assert e.post().status_code == 409
    (tmp_path / "bridge.json").write_text(json.dumps({"enabled": False}))
    assert e.get()["can_update"] is False
    (tmp_path / "bridge.json").write_text("not json")
    assert e.get()["can_update"] is False


def test_builds_from_source_cannot_update(tmp_path):
    e = Env(tmp_path, version="")
    assert "built from source" in e.get()["reason"]
    assert e.post("/check").status_code == 400
    assert e.post().status_code == 409


def test_no_password_no_updates(tmp_path):
    e = Env(tmp_path, auth="")
    assert "password" in e.get()["reason"]
    assert e.post().status_code == 403 and e.post("/check").status_code == 403


def test_cross_site_requests_are_refused(tmp_path):
    e = Env(tmp_path)
    assert e.post(headers={"Content-Type": "text/plain"}).status_code == 415
    assert e.post(headers={**JSON, "Origin": "https://evil.example"}).status_code == 403
    assert e.post(headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert not (tmp_path / "update-request").exists()


def test_auth_covers_the_update_endpoints_in_settings_only_mode(tmp_path):
    s = Settings(static_dir="/nonexistent", basic_auth="me:pw", auth_settings_only=True, solis_version="v0.1.3",
                 solis_repo="o/r", update_dir=str(tmp_path))
    c = TestClient(create_app(repo=object(), control=object(), settings=s, now=lambda: NOW, releases=lambda: release()))
    assert c.get("/api/settings/update").status_code == 401
    assert c.post("/api/settings/update", headers=JSON, json={}).status_code == 401
    assert c.get("/api/version").status_code == 200                    # the version itself is not a secret


def test_status_file_is_sanitised(tmp_path):
    b = Bridge(str(tmp_path))
    assert b.status() is None
    (tmp_path / "update-status.json").write_text("garbage")
    assert b.status() is None
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "exploded"}))
    assert b.status() is None
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "ok", "from": "v1.0.0", "to": "v1.0.1",
                                                              "message": "x" * 1000, "started": 5, "extra": "<script>"}))
    st = b.status()
    assert st["state"] == "ok" and len(st["message"]) == 300 and st["started"] is None and "extra" not in st


def test_fetch_latest_release(monkeypatch):
    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return json.dumps(self.body).encode()

    seen = {}
    def fake(req, timeout):
        seen["url"] = req.full_url
        return Resp({"tag_name": "v1.2.3", "html_url": "https://github.com/o/r/releases/tag/v1.2.3", "published_at": "2026-10-25T10:00:00Z"})
    monkeypatch.setattr(updates.urllib.request, "urlopen", fake)
    assert fetch_latest_release("o/r")["tag"] == "v1.2.3" and seen["url"].endswith("/repos/o/r/releases/latest")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: Resp({"tag_name": "nightly"}))
    with pytest.raises(ReleaseLookupError):
        fetch_latest_release("o/r")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: Resp({"tag_name": "v1.0.0", "html_url": "http://evil.example/x"}))
    assert fetch_latest_release("o/r")["url"] is None                  # only github.com links are shown
    for bad_repo in ("", "no-slash", "a/b; rm -rf /", "../../x"):
        with pytest.raises(ReleaseLookupError):
            fetch_latest_release(bad_repo)


# ---------------------------------------------------------------- background checks (the badge)
def background_app(tmp_path, hours=6, version="v0.1.3", lookup=None):
    calls = []
    def feed():
        calls.append(1)
        return (lookup or (lambda: release()))()
    s = Settings(static_dir="/nonexistent", basic_auth="me:pw", solis_version=version, solis_repo="o/r", update_dir=str(tmp_path),
                 update_check_hours=hours, update_check_first_delay_s=0.0)
    return create_app(repo=object(), control=object(), settings=s, now=lambda: NOW, releases=feed), calls


def wait_for(fn, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_background_check_feeds_the_badge_without_any_click(tmp_path):
    app, calls = background_app(tmp_path)
    with TestClient(app) as c:
        assert wait_for(lambda: c.get("/api/version", auth=AUTH).json()["update"]["available"])
        assert c.get("/api/version", auth=AUTH).json()["update"]["tag"] == "v0.1.4"
        assert len(calls) == 1                                         # one request, then it sleeps for hours


def test_no_badge_when_up_to_date(tmp_path):
    app, calls = background_app(tmp_path, lookup=lambda: release("v0.1.3"))
    with TestClient(app) as c:
        assert wait_for(lambda: calls)
        assert c.get("/api/version", auth=AUTH).json()["update"] == {"available": False, "tag": None}


def test_background_checks_can_be_switched_off_and_skip_source_builds(tmp_path):
    for kwargs in ({"hours": 0}, {"version": ""}):
        app, calls = background_app(tmp_path, **kwargs)
        with TestClient(app):
            time.sleep(0.3)
        assert calls == [], kwargs


def test_a_failed_background_check_does_not_break_anything(tmp_path):
    def boom():
        raise ReleaseLookupError("offline")
    app, calls = background_app(tmp_path, lookup=boom)
    with TestClient(app) as c:
        assert wait_for(lambda: calls)
        assert c.get("/api/version", auth=AUTH).json()["update"]["available"] is False
        assert c.get("/api/settings/update", auth=AUTH).status_code == 200


def test_check_schedule():
    for r in (0.0, 0.5, 1.0):
        assert 6 * 3600 * 0.9 <= next_delay(6, 0, r) <= 6 * 3600 * 1.1          # jittered, so installs do not all ask together
    assert [next_delay(6, n, 0.5) for n in (1, 2, 3, 4, 5)] == [3600, 7200, 14400, 21600, 21600]   # back off, never beyond the interval
    assert next_delay(1, 3, 0.5) == 3600


def test_feed_sends_the_etag_and_keeps_the_answer_on_304(monkeypatch):
    class Resp:
        headers = {"ETag": 'W/"abc"'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return json.dumps({"tag_name": "v1.2.3", "html_url": "https://github.com/o/r/releases/tag/v1.2.3"}).encode()

    sent = []
    answers = [Resp(), "304"]
    def fake(req, timeout):
        sent.append(req.get_header("If-none-match"))
        a = answers.pop(0)
        if a == "304":
            raise updates.urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return a
    monkeypatch.setattr(updates.urllib.request, "urlopen", fake)
    feed = ReleaseFeed("o/r")
    assert feed.latest()["tag"] == "v1.2.3"
    assert feed.latest()["tag"] == "v1.2.3"                            # unchanged: the earlier answer is kept
    assert sent == [None, 'W/"abc"']
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(
        updates.urllib.error.HTTPError(req.full_url, 403, "rate limited", {}, None)))
    with pytest.raises(ReleaseLookupError):
        ReleaseFeed("o/r").latest()
