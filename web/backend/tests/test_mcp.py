import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
HDR = {"Authorization": "Bearer mcp-secret", "Accept": "application/json, text/event-stream",
       "Content-Type": "application/json"}


class Repo:
    def latest(self):
        return NOW - timedelta(seconds=5), {"battery_soc": 98.0, "battery_power_signed": 0.0, "grid_power": 650.0,
                                             "grid_voltage": 214.0, "total_load_power": 650.0, "fault_code_01": 0.0,
                                             "status_code": 3.0, "grid_import_today": 9.8, "grid_import_total": 9.0}

    def history(self, fields, range_s, every, agg):
        ts = [NOW - timedelta(minutes=10 * i) for i in range(5, 0, -1)]
        return ts, {f: [90.0, 92.0, None, 95.0, 98.0] for f in fields}

    def window_max(self, fields, start, every):
        return []

    def events(self, days):
        return [{"time": NOW - timedelta(hours=3), "kind": "outage", "key": "grid", "state": "start",
                 "name": "Grid outage", "severity": "warning", "soc": 99.0, "load": 600.0},
                {"time": NOW - timedelta(hours=1), "kind": "outage", "key": "grid", "state": "end",
                 "name": "Grid outage", "severity": "warning", "soc": 70.0, "load": 600.0}]


class Ctl:
    def devices(self):
        return 200, {"backup": {"eps_dod_pct": 20}, "battery": {"capacity_kwh": 5.12},
                     "inverter": {"status": "Generating", "alarms": []}}

    def cached_settings(self):
        slot = {"slot": 1, "enabled": True, "soc": 100, "current_a": 40.0, "voltage": 49.0, "start": "00:00", "end": "23:59"}
        off = {**slot, "slot": 2, "enabled": False}
        return 200, {"grid_charging": True, "time_of_use_mode": False, "reserve_mode": False,
                     "battery": {"overdischarge_soc": 20}, "max_charge_current_a": 40.0, "max_discharge_current_a": 50.0,
                     "tou": {"charge": [slot, off], "discharge": [off]}, "clock": "2026-10-18T14:58:00",
                     "control": {"server_time": "2026-10-18T15:00:00"}}

    def apply(self, *a):
        raise AssertionError("MCP must never change settings")


def app(token="mcp-secret", basic=""):
    return create_app(repo=Repo(), control=Ctl(), now=lambda: NOW,
                      settings=Settings(static_dir="/nonexistent", mcp_token=token, basic_auth=basic, tariff_day=None))


def rpc(c, method, params=None, headers=HDR):
    r = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    return r


def call_tool(c, name, args=None):
    r = rpc(c, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert not res.get("isError"), res
    return json.loads(res["content"][0]["text"])


def test_requires_its_own_token():
    with TestClient(app(basic="me:pw")) as c:
        assert rpc(c, "tools/list", headers={**HDR, "Authorization": "Bearer wrong"}).status_code == 401
        assert rpc(c, "tools/list", headers={k: v for k, v in HDR.items() if k != "Authorization"}).status_code == 401
        # the dashboard password does not open /mcp, and the MCP token does not open the dashboard
        assert rpc(c, "tools/list", headers={**HDR, "Authorization": "Basic bWU6cHc="}).status_code == 401
        assert c.get("/api/live", headers={"Authorization": "Bearer mcp-secret"}).status_code == 401
        assert rpc(c, "tools/list").status_code == 200


def test_disabled_without_token():
    with TestClient(app(token="")) as c:
        assert rpc(c, "tools/list", headers={**HDR, "Authorization": "Bearer "}).status_code == 401


def test_only_read_tools():
    with TestClient(app()) as c:
        names = sorted(t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"])
        assert names == ["get_devices", "get_energy", "get_history", "get_outages_and_alarms", "get_schedule", "get_status"]


def test_get_status():
    with TestClient(app()) as c:
        s = call_tool(c, "get_status")
        assert s["online"] is True and s["grid"]["state"] == "connected" and s["battery"]["soc_pct"] == 98.0
        assert s["battery"]["state"] == "idle"
        assert s["backup_runtime"] == {"hours": 6.1, "down_to_soc_pct": 20, "capacity_kwh": 5.12, "at_load_w": 650.0}


def test_get_history_and_validation():
    with TestClient(app()) as c:
        h = call_tool(c, "get_history", {"field": "battery_soc", "hours": 5})
        assert h["range"] == "6h" and h["min"] == 90.0 and h["max"] == 98.0 and len(h["points"]) == 4
        r = rpc(c, "tools/call", {"name": "get_history", "arguments": {"field": "drop table"}})
        assert r.json()["result"]["isError"] is True


def test_outages_schedule_energy():
    with TestClient(app()) as c:
        e = call_tool(c, "get_outages_and_alarms", {"days": 7})
        assert e["outage_count"] == 1 and e["outages"][0]["duration_s"] == 7200
        sch = call_tool(c, "get_schedule")
        assert [x["slot"] for x in sch["enabled_slots"]["charge"]] == [1] and sch["enabled_slots"]["discharge"] == []
        en = call_tool(c, "get_energy")
        assert en["energy"]["grid_import"]["today"] == 9.8 and en["cost"]["configured"] is False
