"""The Overview's card layout: validation, the file, and the two routes (open read, write behind the login)."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.layout import CARD_IDS, LayoutError, LayoutStore, validate
from app.main import create_app

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
AUTH = ("me", "pw")
JSON = {"Content-Type": "application/json"}
MINE = {"left": ["flow", "power"], "right": ["selfsufficiency", "grid", "heatmap"]}


class Repo:
    pass


class Ctl:
    pass


def client(tmp_path, auth="me:pw", **kw):
    s = Settings(static_dir="/nonexistent", basic_auth=auth, auth_settings_only=True,
                 layout_file=str(tmp_path / "overview-layout.json"), **kw)
    return TestClient(create_app(repo=Repo(), control=Ctl(), settings=s, now=lambda: NOW))


def post(c, body, headers=None):
    return c.post("/api/settings/overview-layout", json=body, headers={**JSON, **(headers or {})}, auth=AUTH)


@pytest.mark.parametrize("bad", [
    None, [], {"left": []}, {"left": [], "right": [], "top": []}, {"left": "flow", "right": []},
    {"left": ["flow"], "right": ["nope"]}, {"left": ["flow"], "right": ["flow"]}, {"left": [1], "right": []},
])
def test_only_known_cards_once_each_in_two_columns(bad):
    with pytest.raises(LayoutError):
        validate(bad)


def test_an_empty_page_and_every_card_are_both_allowed():
    assert validate({"left": [], "right": []}) == {"left": [], "right": []}
    assert validate({"left": list(CARD_IDS), "right": []})["left"] == list(CARD_IDS)


def test_the_store_keeps_the_file_and_null_removes_it(tmp_path):
    path = tmp_path / "overview-layout.json"
    store = LayoutStore(str(path))
    assert store.get() is None
    store.set(MINE)
    assert json.loads(path.read_text()) == MINE and LayoutStore(str(path)).get() == MINE
    store.set(None)
    assert not path.exists() and store.get() is None


def test_a_broken_file_is_ignored(tmp_path):
    path = tmp_path / "overview-layout.json"
    path.write_text('{"left": ["flow", "rm -rf"], "right": []}')
    assert LayoutStore(str(path)).get() is None


def test_anyone_reads_it_and_only_the_login_changes_it(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/overview-layout").json() == {"layout": None, "web": {"can_change": True, "reason": None}}
    assert c.get("/api/settings/overview-layout").status_code == 401           # Customize asks for the password first
    assert c.post("/api/settings/overview-layout", json={"layout": MINE}, headers=JSON).status_code == 401
    r = post(c, {"layout": MINE})
    assert r.status_code == 200 and r.json()["layout"] == MINE
    assert c.get("/api/overview-layout").json()["layout"] == MINE
    assert post(c, {"layout": None}).json()["layout"] is None
    assert not (tmp_path / "overview-layout.json").exists()


def test_bad_bodies_and_cross_site_posts_are_refused(tmp_path):
    c = client(tmp_path)
    assert post(c, {"layout": {"left": ["x"], "right": []}}).status_code == 400
    assert post(c, MINE).status_code == 400                                     # not wrapped in {layout}
    assert post(c, {"layout": MINE}, {"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/settings/overview-layout", content="layout", headers={"Content-Type": "text/plain"}, auth=AUTH).status_code == 415
    assert c.get("/api/overview-layout").json()["layout"] is None


def test_without_a_password_it_cannot_be_changed(tmp_path):
    c = client(tmp_path, auth="")
    body = c.get("/api/overview-layout").json()
    assert body["web"]["can_change"] is False and "WEB_BASIC_AUTH" in body["web"]["reason"]
    assert c.post("/api/settings/overview-layout", json={"layout": MINE}, headers=JSON).status_code == 403
