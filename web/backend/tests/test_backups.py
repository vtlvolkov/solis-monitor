import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.backups import BackupBridge
from app.config import Settings
from app.main import create_app

NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
GOOD = "solis-monitor-Home-20261112-173000.tar.gz"


class Env:
    """An app wired to a temp control folder (the bridge) and a temp backups folder (what the host wrote)."""

    def __init__(self, tmp_path, auth="me:pw", bridge=True, managed_by="", backups_dir=True):
        self.control, self.backups, self.t = tmp_path / "control", tmp_path / "backups", NOW
        self.control.mkdir(parents=True)
        if backups_dir:
            self.backups.mkdir()
        if bridge:
            (self.control / "bridge.json").write_text(json.dumps({"enabled": True, "version": "v0.3.3"}))
            (self.control / "backup-config.json").write_text("{}")   # what dashboard-updates on (v0.4.0+) leaves
        s = Settings(static_dir="/nonexistent", basic_auth=auth, solis_version="v0.3.3", update_dir=str(self.control),
                     backup_dir=str(self.backups), updates_managed_by=managed_by)
        self.client = TestClient(create_app(repo=object(), control=object(), settings=s, now=lambda: self.t))

    def archive(self, name=GOOD, body=b"gz"):
        (self.backups / name).write_bytes(body)

    def status(self, **raw):
        (self.control / "backup-status.json").write_text(json.dumps(raw))

    def get(self):
        return self.client.get("/api/settings/backups", auth=AUTH).json()

    def post(self):
        return self.client.post("/api/settings/backups", headers=JSON, json={}, auth=AUTH)


def test_lists_the_archives_the_host_wrote_newest_first_and_nothing_else(tmp_path):
    e = Env(tmp_path)
    e.archive("solis-monitor-20261110-080000.tar.gz", b"old")
    e.archive(GOOD, b"newer!")
    e.archive("notes.txt"); e.archive("solis-monitor-20261110-080000.tar.gz.tmp"); e.archive(".env")
    body = e.get()
    assert body["can_backup"] is True and body["busy"] is False and body["listed"] is True and body["folder"]["keep"] == 5
    assert [b["name"] for b in body["backups"]] == [GOOD, "solis-monitor-20261110-080000.tar.gz"] or \
           [b["name"] for b in body["backups"]] == ["solis-monitor-20261110-080000.tar.gz", GOOD]     # same mtime second: either order
    assert {b["name"]: b["size"] for b in body["backups"]}[GOOD] == 6
    assert all(b["created"].startswith("20") for b in body["backups"])


def test_download_only_what_the_host_would_have_named(tmp_path):
    e = Env(tmp_path)
    e.archive(GOOD, b"tar.gz bytes")
    r = e.client.get(f"/api/settings/backups/{GOOD}", auth=AUTH)
    assert r.status_code == 200 and r.content == b"tar.gz bytes"
    assert r.headers["content-type"] == "application/gzip" and GOOD in r.headers["content-disposition"]
    (e.backups / "secret.tar.gz").write_bytes(b"x")
    for bad in ("secret.tar.gz", "solis-monitor-20261112-173000.tar.gz", "..%2F..%2Fetc%2Fpasswd", "solis-monitor-x-20261112-173000.tar.gz.tmp"):
        assert e.client.get(f"/api/settings/backups/{bad}", auth=AUTH).status_code == 404, bad


def test_download_needs_the_login(tmp_path):
    e = Env(tmp_path)
    e.archive()
    assert e.client.get(f"/api/settings/backups/{GOOD}").status_code == 401
    assert e.client.get("/api/settings/backups").status_code == 401


def test_request_writes_the_file_for_the_host_once(tmp_path):
    e = Env(tmp_path)
    r = e.post()
    assert r.status_code == 202 and r.json()["busy"] is True
    req = json.loads((e.control / "backup-request").read_text())
    assert req == {"requested_at": NOW.isoformat()}
    assert e.post().status_code == 409                                  # already asked, not picked up yet
    assert e.get()["busy"] is True


def test_follows_the_host_status_and_gives_up_on_a_stale_one(tmp_path):
    e = Env(tmp_path)
    e.status(state="running", message="Backing up the database", started=NOW.isoformat())
    assert e.get()["busy"] is True and e.post().status_code == 409
    e.status(state="ok", message="Backup written", file=GOOD, started=NOW.isoformat(), finished=NOW.isoformat())
    body = e.get()
    assert body["busy"] is False and body["status"]["file"] == GOOD and body["status"]["state"] == "ok"
    e.status(state="ok", file="../../etc/passwd")
    assert e.get()["status"]["file"] is None                            # a name the host would never write
    e.status(state="running", started="2026-11-12T17:00:00+00:00")       # an hour ago, still "running": the helper died
    body = e.get()
    assert body["busy"] is False and body["status"]["state"] == "failed" and "without reporting" in body["status"]["message"]
    e.status(state="weird")
    assert e.get()["status"] is None


def test_refused_without_the_bridge_a_password_or_on_a_managed_platform(tmp_path):
    e = Env(tmp_path, bridge=False)
    body = e.get()
    assert body["can_backup"] is False and "dashboard-updates on" in body["reason"]
    assert e.post().status_code == 409
    e = Env(tmp_path / "b", managed_by="umbrelOS")
    assert e.get()["reason"] == "Backups are handled by umbrelOS." and e.get()["managed_by"] == "umbrelOS"
    e = Env(tmp_path / "c", auth="")
    r = e.client.post("/api/settings/backups", headers=JSON, json={})
    assert r.status_code == 403


def test_a_request_nobody_takes_stops_the_wait_and_can_be_made_again(tmp_path):
    e = Env(tmp_path)
    assert e.post().status_code == 202
    e.t = NOW.replace(minute=1)                                          # a minute: the host may still be starting
    assert e.get()["busy"] is True
    e.t = NOW.replace(minute=3)                                          # no path unit took it
    body = e.get()
    assert body["busy"] is False and body["status"]["state"] == "failed" and "did not pick up" in body["status"]["message"]
    assert e.post().status_code == 202                                  # asking again replaces the stale request
    assert json.loads((e.control / "backup-request").read_text())["requested_at"] == e.t.isoformat()


def test_an_install_set_up_before_backups_existed_is_told_to_set_them_up(tmp_path):
    e = Env(tmp_path)
    (e.control / "backup-config.json").unlink()                         # dashboard-updates on from before v0.4.0
    body = e.get()
    assert body["can_backup"] is False and "dashboard-updates on" in body["reason"]
    assert e.post().status_code == 409 and not (e.control / "backup-request").exists()


def test_without_the_mount_the_page_still_works(tmp_path):
    e = Env(tmp_path, backups_dir=False)
    body = e.get()
    assert body["listed"] is False and body["backups"] == [] and body["can_backup"] is True
    assert BackupBridge(str(e.control), str(e.backups)).path(GOOD) is None


def test_cross_site_post_is_refused(tmp_path):
    e = Env(tmp_path)
    r = e.client.post("/api/settings/backups", headers={**JSON, "Origin": "http://evil.example"}, json={}, auth=AUTH)
    assert r.status_code == 403 and not (e.control / "backup-request").exists()


def test_the_folder_comes_from_the_host_and_the_index_lists_what_this_container_cannot_see(tmp_path):
    e = Env(tmp_path)
    assert e.get()["folder"] == {"dir": None, "default_dir": None, "keep": 5, "keep_min": 1, "keep_max": 50}
    (e.control / "backup-config.json").write_text(json.dumps({"dir": "/mnt/usb/solis", "default_dir": "/opt/solis-monitor/backups", "keep": 12}))
    assert e.get()["folder"] == {"dir": "/mnt/usb/solis", "default_dir": "/opt/solis-monitor/backups", "keep": 12, "keep_min": 1, "keep_max": 50}
    (e.control / "backup-config.json").write_text(json.dumps({"dir": "../x", "default_dir": "/opt/solis-monitor/backups", "keep": 500}))
    assert e.get()["folder"]["dir"] == "/opt/solis-monitor/backups" and e.get()["folder"]["keep"] == 5   # garbage falls back
    e.archive(GOOD, b"here")
    (e.control / "backups.json").write_text(json.dumps({"files": [
        {"name": GOOD, "size": 4, "created": "2026-11-12T17:30:00+00:00"},
        {"name": "solis-monitor-Home-20261113-010000.tar.gz", "size": 212400000, "created": "2026-11-13T01:00:00+00:00"},
        {"name": "../etc/passwd", "size": 1, "created": "x"}, "junk"]}))
    body = e.get()
    assert [(b["name"], b["downloadable"]) for b in body["backups"]] == [("solis-monitor-Home-20261113-010000.tar.gz", False), (GOOD, True)]
    assert e.client.get("/api/settings/backups/solis-monitor-Home-20261113-010000.tar.gz", auth=AUTH).status_code == 404


def test_folder_change_is_checked_here_and_handed_to_the_host(tmp_path):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/folder", headers=JSON, json=body, auth=AUTH)
    for bad in ({"dir": "relative/x", "keep": 5}, {"dir": "/mnt/../etc", "keep": 5}, {"dir": "/mnt/a b", "keep": 5},
                {"dir": "/mnt/usb", "keep": 0}, {"dir": "/mnt/usb", "keep": 51}, {"dir": "/mnt/usb", "keep": True}, {"dir": "/mnt/usb"}):
        assert post(bad).status_code == 400, bad
    assert not (e.control / "backup-request").exists()
    r = post({"dir": "/mnt/usb/solis/", "keep": 10})
    assert r.status_code == 202 and r.json()["busy"] is True
    req = json.loads((e.control / "backup-request").read_text())
    assert req == {"requested_at": NOW.isoformat(), "action": "set-folder", "dir": "/mnt/usb/solis", "keep": 10}
    assert post({"dir": "/mnt/usb", "keep": 5}).status_code == 409                    # one request at a time


def test_removal_is_a_request_to_the_host_for_a_listed_name_only(tmp_path):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/remove", headers=JSON, json=body, auth=AUTH)
    for bad in ({"name": "../.env"}, {"name": "notes.txt"}, {"name": GOOD + ".tmp"}, {"name": 5}, {"file": GOOD}):
        assert post(bad).status_code == 400, bad
    assert not (e.control / "backup-request").exists()
    r = post({"name": GOOD})
    assert r.status_code == 202 and r.json()["busy"] is True
    assert json.loads((e.control / "backup-request").read_text()) == {"requested_at": NOW.isoformat(), "action": "remove", "name": GOOD}
    assert post({"name": GOOD}).status_code == 409


def test_restore_is_a_request_to_the_host_with_the_safety_backup_flag(tmp_path):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/restore", headers=JSON, json=body, auth=AUTH)
    for bad in ({"name": GOOD}, {"name": "../.env", "backup_first": True}, {"name": GOOD, "backup_first": "yes"}, {"name": GOOD, "backup_first": True, "x": 1}):
        assert post(bad).status_code == 400, bad
    assert not (e.control / "backup-request").exists()
    r = post({"name": GOOD, "backup_first": True})
    assert r.status_code == 202 and r.json()["busy"] is True
    assert json.loads((e.control / "backup-request").read_text()) == {"requested_at": NOW.isoformat(), "action": "restore", "name": GOOD, "backup_first": True}
    assert post({"name": GOOD, "backup_first": False}).status_code == 409
    e2 = Env(tmp_path / "m", managed_by="umbrelOS")
    assert e2.client.post("/api/settings/backups/restore", headers=JSON, json={"name": GOOD, "backup_first": True}, auth=AUTH).status_code == 409


def b64(v: str) -> str:
    import base64
    return base64.b64encode(v.encode()).decode()


def test_the_daily_time_and_the_copy_server_come_from_the_host(tmp_path):
    e = Env(tmp_path)
    body = e.get()
    assert body["schedule"] == {"time": None, "available": False} and body["remote"] is None
    assert body["remote_keep"] == {"min": 1, "max": 100, "default": 14}
    (e.control / "backup-config.json").write_text(json.dumps({"keep": 5, "schedule": "03:30", "scheduler": True}))
    (e.control / "backup-remote.json").write_text(json.dumps({
        "kind": "nextcloud", "url": "http://100.106.168.116:8081", "user": "vitalii", "folder": "Backups/дача", "keep": 14,
        "last": {"state": "ok", "message": "Copied to Nextcloud.", "file": GOOD, "at": "2026-11-12T01:31:00Z"}}))
    body = e.get()
    assert body["schedule"] == {"time": "03:30", "available": True}
    assert body["remote"] == {"kind": "nextcloud", "url": "http://100.106.168.116:8081", "user": "vitalii", "folder": "Backups/дача",
                              "keep": 14, "last": {"state": "ok", "message": "Copied to Nextcloud.", "file": GOOD, "at": "2026-11-12T01:31:00Z"}}
    (e.control / "backup-config.json").write_text(json.dumps({"schedule": "25:00; rm", "scheduler": "yes"}))
    (e.control / "backup-remote.json").write_text(json.dumps({"kind": "ftp", "url": "ftp://x"}))
    body = e.get()
    assert body["schedule"] == {"time": None, "available": False} and body["remote"] is None     # garbage is not shown


def test_the_status_says_how_the_copy_went(tmp_path):
    e = Env(tmp_path)
    e.status(state="ok", message="Backup written", file=GOOD, started="2026-11-12T17:59:00Z", finished="2026-11-12T17:59:30Z",
             scheduled=True, copy="failed", copy_message="The server could not be reached.")
    st = e.get()["status"]
    assert st["scheduled"] is True and st["copy"] == "failed" and st["copy_message"] == "The server could not be reached."
    e.status(state="ok", message="Backup written", file=GOOD, started="2026-11-12T17:59:00Z", copy="", copy_message="x")
    st = e.get()["status"]
    assert st["scheduled"] is False and st["copy"] is None and st["copy_message"] is None


def test_daily_time_is_checked_here_and_needs_the_hosts_timer(tmp_path):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/schedule", headers=JSON, json=body, auth=AUTH)
    for bad in ({"time": "3:30"}, {"time": "24:00"}, {"time": "03:30; rm"}, {"time": 330}, {}, {"time": "03:30", "x": 1}):
        assert post(bad).status_code == 400, bad
    r = post({"time": "03:30"})
    assert r.status_code == 409 and "dashboard-updates on" in r.json()["detail"] and not (e.control / "backup-request").exists()
    (e.control / "backup-config.json").write_text(json.dumps({"scheduler": True}))
    assert post({"time": "03:30"}).status_code == 202
    assert json.loads((e.control / "backup-request").read_text()) == {"requested_at": NOW.isoformat(), "action": "set-schedule", "time": "03:30"}
    (e.control / "backup-request").unlink()
    assert post({"time": None}).status_code == 202                       # off works without the timer too
    assert json.loads((e.control / "backup-request").read_text())["time"] == "off"


GOOD_REMOTE = {"kind": "nextcloud", "url": "http://100.106.168.116:8081/index.php/apps/files/?dir=/", "user": " vitalii ",
               "password": 'Ab "c\\ $d', "folder": "/Backups/Сонце дача/", "keep": 14}


def test_the_copy_server_is_checked_here_and_handed_over_base64_encoded(tmp_path, caplog):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/remote", headers=JSON, json=body, auth=AUTH)
    for change in ({"kind": "ftp"}, {"url": "ftp://x"}, {"url": "http://a b"}, {"url": "http://x/$(id)"}, {"user": ""},
                   {"user": "a:b"}, {"password": ""}, {"password": None}, {"password": "a\nb"}, {"folder": "a/../b"},
                   {"folder": 'a"b'}, {"keep": 0}, {"keep": 101}, {"keep": True}):
        assert post({**GOOD_REMOTE, **change}).status_code == 400, change
    assert post({k: v for k, v in GOOD_REMOTE.items() if k != "keep"}).status_code == 400
    assert not (e.control / "backup-request").exists()
    with caplog.at_level("WARNING"):
        r = post(GOOD_REMOTE)
    assert r.status_code == 202 and r.json()["busy"] is True
    req = json.loads((e.control / "backup-request").read_text())
    assert req == {"requested_at": NOW.isoformat(), "action": "set-remote", "kind": "nextcloud",
                   "url_b64": b64("http://100.106.168.116:8081"), "user_b64": b64("vitalii"), "password_b64": b64('Ab "c\\ $d'),
                   "folder_b64": b64("Backups/Сонце дача"), "keep": 14}
    assert "Ab " not in caplog.text and "vitalii" in caplog.text                       # the password is never logged


def test_an_empty_password_keeps_the_saved_one_only_when_there_is_one(tmp_path):
    e = Env(tmp_path)
    post = lambda body: e.client.post("/api/settings/backups/remote", headers=JSON, json=body, auth=AUTH)
    other = {**GOOD_REMOTE, "kind": "webdav", "url": "https://dav.example.com/remote/", "password": ""}
    assert post(other).status_code == 400 and post({**other, "password": None}).status_code == 400
    (e.control / "backup-remote.json").write_text(json.dumps({"kind": "nextcloud", "url": "http://nc:8081", "user": "v", "folder": "", "keep": 3}))
    assert post(other).status_code == 202
    req = json.loads((e.control / "backup-request").read_text())
    assert req["password_b64"] == "" and req["url_b64"] == b64("https://dav.example.com/remote")    # a WebDAV address is used as it is


def test_stop_copying_and_the_usual_refusals(tmp_path):
    e = Env(tmp_path)
    post = lambda path, body: e.client.post(f"/api/settings/backups/{path}", headers=JSON, json=body, auth=AUTH)
    assert post("remote/remove", {"x": 1}).status_code == 400
    assert post("remote/remove", {}).status_code == 202
    assert json.loads((e.control / "backup-request").read_text()) == {"requested_at": NOW.isoformat(), "action": "remove-remote"}
    assert post("remote", GOOD_REMOTE).status_code == 409                       # one request at a time
    r = e.client.post("/api/settings/backups/remote", json=GOOD_REMOTE, auth=AUTH, headers={**JSON, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert e.client.post("/api/settings/backups/remote", headers=JSON, json=GOOD_REMOTE).status_code == 401
    m = Env(tmp_path / "m", managed_by="umbrelOS")
    assert m.client.post("/api/settings/backups/schedule", headers=JSON, json={"time": None}, auth=AUTH).status_code == 409


def test_nextcloud_addresses_copied_from_the_browser_are_cut_to_the_server():
    from app.backups import nextcloud_base
    assert nextcloud_base("https://cloud.example.com/index.php/apps/files/?dir=/Backups") == "https://cloud.example.com"
    assert nextcloud_base("http://100.106.168.116:8081/apps/dashboard/") == "http://100.106.168.116:8081"
    assert nextcloud_base("https://example.com/nextcloud/remote.php/dav/files/v/") == "https://example.com/nextcloud"
    assert nextcloud_base("https://example.com/nextcloud/") == "https://example.com/nextcloud"
    assert nextcloud_base(" http://umbrel.local:8081 ") == "http://umbrel.local:8081"
