import base64
import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import LoginError, LoginStore, TooManyAttempts, check_new_password
from app.config import Settings
from app.main import create_app

JSON = {"Content-Type": "application/json"}


def header(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    s = LoginStore("admin:Original:pass1", str(tmp_path / "auth.json"), clock=clock)
    s.clock_for_test = clock
    return s


# ---------------------------------------------------------------- the original login
def test_the_environment_login_works_as_before(store):
    assert store.verify_header(header("admin", "Original:pass1"))                  # a colon inside the password is fine
    assert store.source() == "env" and store.info()["changed_at"] is None
    for bad in (header("admin", "wrong"), header("root", "Original:pass1"), header("admin", ""), "Basic", "Basic !!!", "Bearer abc", "", "basic",
                "Basic " + base64.b64encode(b"no-colon").decode(), "Basic " + base64.b64encode(b"\xff\xfe:\xff").decode()):
        assert not store.verify_header(bad), bad
    assert store.verify_header(header("admin", "Original:pass1").replace("Basic", "basic"))   # the scheme is case-insensitive


# ---------------------------------------------------------------- changing it
def test_a_changed_password_replaces_the_old_one_at_once(store, tmp_path):
    assert store.verify_header(header("admin", "Original:pass1"))                  # remembered as good ...
    info = store.change("Original:pass1", "A-new-password-7")
    assert info["source"] == "saved" and info["user"] == "admin" and info["changed_at"]
    assert not store.verify_header(header("admin", "Original:pass1"))              # ... and forgotten the moment the password changes
    assert store.verify_header(header("admin", "A-new-password-7"))
    assert not store.verify_header(header("root", "A-new-password-7"))
    raw = (tmp_path / "auth.json").read_text()
    assert "A-new-password-7" not in raw and "Original" not in raw                  # only a salted hash is kept
    assert json.loads(raw)["algo"] == "scrypt" and len(json.loads(raw)["salt"]) == 32
    assert oct(os.stat(tmp_path / "auth.json").st_mode & 0o777) == "0o600"
    assert not (tmp_path / "auth.json.tmp").exists()


def test_it_survives_a_restart_and_two_logins_do_not_share_a_salt(store, tmp_path):
    store.change("Original:pass1", "A-new-password-7")
    again = LoginStore("admin:Original:pass1", str(tmp_path / "auth.json"))
    assert again.verify_header(header("admin", "A-new-password-7")) and not again.verify_header(header("admin", "Original:pass1"))
    salt1 = json.loads((tmp_path / "auth.json").read_text())["salt"]
    again.change("A-new-password-7", "A-new-password-7!")
    assert json.loads((tmp_path / "auth.json").read_text())["salt"] != salt1


def test_going_back_to_the_original(store, tmp_path):
    store.change("Original:pass1", "A-new-password-7")
    store.change("A-new-password-7", None)
    assert store.source() == "env" and not (tmp_path / "auth.json").exists()
    assert store.verify_header(header("admin", "Original:pass1")) and not store.verify_header(header("admin", "A-new-password-7"))


def test_removing_the_file_by_hand_resets_a_running_server(store, tmp_path):
    store.change("Original:pass1", "A-new-password-7")
    assert store.verify_header(header("admin", "A-new-password-7"))
    os.remove(tmp_path / "auth.json")                                              # what `solis-monitor reset-login` does
    assert not store.verify_header(header("admin", "A-new-password-7"))
    assert store.verify_header(header("admin", "Original:pass1")) and store.source() == "env"


def test_an_unreadable_file_falls_back_to_the_original(store, tmp_path):
    for text in ("garbage", "{}", json.dumps({"user": "admin", "salt": "zz", "hash": "00", "n": 1, "r": 1, "p": 1}), "[]"):
        (tmp_path / "auth.json").write_text(text)
        os.utime(tmp_path / "auth.json", ns=(1, hash(text) % 10**9 + 2))
        assert store.verify_header(header("admin", "Original:pass1")), text
        assert store.source() == "env"


# ---------------------------------------------------------------- what it refuses
@pytest.mark.parametrize("new,why", [
    ("short", "8 to 128"), ("x" * 129, "8 to 128"), ("has\ttab-char", "control"), (" leading-space1", "start or end"),
    ("trailing-space1 ", "start or end"), (12345678, "text"), (None, "text"), ("Original:pass1", "same"),
])
def test_weak_or_odd_passwords_are_refused(store, new, why):
    if new is None:                                                                # None means "go back", so use another type
        new = ["list"]
    with pytest.raises(LoginError, match=why):
        check_new_password(new, "Original:pass1")


def test_a_wrong_current_password_changes_nothing(store, tmp_path):
    for current in ("wrong", "", None, 5):
        with pytest.raises(LoginError, match="current password is not right"):
            store.change(current, "A-new-password-7")
    assert store.source() == "env" and not (tmp_path / "auth.json").exists()


def test_guessing_the_current_password_is_rate_limited(store):
    for _ in range(auth.ATTEMPTS):
        with pytest.raises(LoginError, match="not right"):
            store.change("wrong", "A-new-password-7")
    with pytest.raises(TooManyAttempts) as e:
        store.change("Original:pass1", "A-new-password-7")                          # even the right one waits
    assert 0 < e.value.retry_s <= auth.WINDOW_S and "Try again in" in str(e.value)
    store.clock_for_test.t += auth.WINDOW_S + 1
    assert store.change("Original:pass1", "A-new-password-7")["source"] == "saved"


def test_a_right_password_clears_the_count(store):
    for _ in range(auth.ATTEMPTS - 1):
        with pytest.raises(LoginError):
            store.change("wrong", "A-new-password-7")
    store.change("Original:pass1", "A-new-password-7")
    for _ in range(auth.ATTEMPTS - 1):
        with pytest.raises(LoginError, match="not right"):
            store.change("wrong", "Another-pass-8")


def test_there_is_nothing_to_change_without_a_login(tmp_path):
    with pytest.raises(LoginError, match="no login"):
        LoginStore("", str(tmp_path / "a.json")).change("x", "A-new-password-7")


def test_without_a_file_path_nothing_can_be_saved():
    with pytest.raises(LoginError, match="cannot save"):
        LoginStore("admin:Original:pass1", None).change("Original:pass1", "A-new-password-7")


# ---------------------------------------------------------------- through the web app
def make_app(tmp_path, auth_mode="all", user="me", password="pw-original-1", file=True):
    s = Settings(static_dir="/nonexistent", basic_auth=f"{user}:{password}" if password else "", auth_settings_only=(auth_mode == "settings"),
                 auth_file=str(tmp_path / "auth.json") if file else "", mcp_token="mcp-secret")
    return create_app(repo=object(), control=object(), settings=s, now=lambda: datetime.now(timezone.utc))


def client(*a, **kw):
    return TestClient(make_app(*a, **kw))


def post(c, body, auth_pair=("me", "pw-original-1"), headers=JSON):
    return c.post("/api/settings/login", json=body, headers=headers, auth=auth_pair)


def test_change_the_password_through_the_api(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/settings/login", auth=("me", "pw-original-1")).json()["source"] == "env"
    r = post(c, {"current": "pw-original-1", "new": "A-new-password-7"})
    assert r.status_code == 200 and r.json()["source"] == "saved" and r.json()["web"]["can_change"] is True
    assert c.get("/api/settings/login", auth=("me", "pw-original-1")).status_code == 401           # the old one is dead
    assert c.get("/api/settings/login", auth=("me", "A-new-password-7")).status_code == 200
    assert c.get("/api/version", auth=("me", "pw-original-1")).status_code == 401                     # also on every other page in this mode
    assert c.get("/api/version", auth=("me", "A-new-password-7")).status_code == 200
    assert c.get("/healthz").status_code == 200                                                       # the health check stays open
    back = post(c, {"current": "A-new-password-7", "new": None}, auth_pair=("me", "A-new-password-7"))
    assert back.status_code == 200 and back.json()["source"] == "env"
    assert c.get("/api/settings/login", auth=("me", "pw-original-1")).status_code == 200


def test_refusals_are_400_not_401_so_the_browser_does_not_prompt_again(tmp_path):
    c = client(tmp_path)
    r = post(c, {"current": "wrong", "new": "A-new-password-7"})
    assert r.status_code == 400 and r.json()["detail"] == "The current password is not right." and "www-authenticate" not in r.headers
    assert post(c, {"current": "pw-original-1", "new": "short"}).status_code == 400
    for _ in range(auth.ATTEMPTS):
        post(c, {"current": "wrong", "new": "A-new-password-7"})
    r = post(c, {"current": "pw-original-1", "new": "A-new-password-7"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_only_well_formed_same_origin_requests_are_accepted(tmp_path):
    c = client(tmp_path)
    for body in ({}, {"current": "pw-original-1"}, {"new": "A-new-password-7"}, {"current": "x", "new": "y", "extra": 1}, [], "x"):
        assert post(c, body).status_code == 400, body
    ok = {"current": "pw-original-1", "new": "A-new-password-7"}
    assert post(c, ok, headers={"Content-Type": "text/plain"}).status_code == 415
    assert post(c, ok, headers={**JSON, "Origin": "https://evil.example"}).status_code == 403
    assert post(c, ok, headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert not (tmp_path / "auth.json").exists()


def test_it_needs_a_login_to_exist_and_to_be_signed_in(tmp_path):
    open_dashboard = client(tmp_path, password="")                                                    # no WEB_BASIC_AUTH: nobody may set one
    r = open_dashboard.post("/api/settings/login", json={"current": "", "new": "A-new-password-7"}, headers=JSON)
    assert r.status_code == 403 and not (tmp_path / "auth.json").exists()
    c = client(tmp_path, auth_mode="settings")
    assert c.get("/api/settings/login").status_code == 401
    assert c.post("/api/settings/login", json={"current": "pw-original-1", "new": "A-new-password-7"}, headers=JSON).status_code == 401


def test_the_agent_endpoint_has_its_own_token_and_is_not_affected(tmp_path):
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    h = {"Accept": "application/json, text/event-stream", **JSON}
    with TestClient(make_app(tmp_path)) as c:                                                         # the MCP transport needs the app's lifespan
        post(c, {"current": "pw-original-1", "new": "A-new-password-7"})
        assert c.post("/mcp", json=rpc, headers={**h, "Authorization": "Bearer mcp-secret"}).status_code == 200
        assert c.post("/mcp", json=rpc, headers={**h, "Authorization": "Basic " + base64.b64encode(b"me:A-new-password-7").decode()}).status_code == 401


def test_a_dashboard_without_a_data_folder_says_it_cannot_save(tmp_path):
    c = client(tmp_path, file=False)
    r = post(c, {"current": "pw-original-1", "new": "A-new-password-7"})
    assert r.status_code == 400 and "cannot save" in r.json()["detail"]
