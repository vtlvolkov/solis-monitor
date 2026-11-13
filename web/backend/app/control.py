"""Client for the poller's internal API (settings, device details). The web app never talks to the inverter."""
import json
import urllib.error
import urllib.parse
import urllib.request


class PollerUnavailable(Exception):
    pass


class ControlClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def _call(self, method: str, path: str = "/settings", body: dict | None = None, timeout: float | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {"detail": e.reason}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise PollerUnavailable(str(e)) from e

    def get(self) -> tuple[int, dict]:
        return self._call("GET")

    def apply(self, setting: str, value) -> tuple[int, dict]:
        return self._call("POST", "/settings", {"setting": setting, "value": value})

    def cached_settings(self) -> tuple[int, dict]:
        return self._call("GET", "/settings/cached")

    def devices(self) -> tuple[int, dict]:
        return self._call("GET", "/device")

    def battery_capacity(self) -> tuple[int, dict]:
        return self._call("GET", "/battery-capacity")

    def outages(self) -> tuple[int, dict]:
        return self._call("GET", "/outages", timeout=10)

    def outage_config(self) -> tuple[int, dict]:
        return self._call("GET", "/outages/config")

    def set_outage_config(self, source, group, alerts, overview=None) -> tuple[int, dict]:
        body = {"source": source, "group": group, "alerts": alerts}
        if overview is not None:
            body["overview"] = overview
        return self._call("POST", "/outages/config", body)

    def outage_lookup(self, kind: str, **params) -> tuple[int, dict]:
        """streets / houses / group at Yasno, through the poller (it fetches; the browser never talks to Yasno)."""
        assert kind in ("streets", "houses", "group", "groups")
        return self._call("GET", f"/outages/{kind}?{urllib.parse.urlencode(params)}", timeout=25)

    def retention(self) -> tuple[int, dict]:
        return self._call("GET", "/retention", timeout=10)

    def set_retention(self, days: int | None) -> tuple[int, dict]:
        return self._call("POST", "/retention", {"days": days})

    def set_battery_capacity(self, kwh: float | None) -> tuple[int, dict]:
        return self._call("POST", "/battery-capacity", {"kwh": kwh})

    def mqtt(self) -> tuple[int, dict]:
        return self._call("GET", "/mqtt")

    def set_mqtt(self, body: dict) -> tuple[int, dict]:
        return self._call("POST", "/mqtt", body)

    def test_mqtt(self, body: dict) -> tuple[int, dict]:
        return self._call("POST", "/mqtt/test", body, timeout=15)        # the poller waits up to 5 s for the broker

    def inverter_address(self) -> tuple[int, dict]:
        return self._call("GET", "/inverter")

    def set_inverter_address(self, host, port, unit) -> tuple[int, dict]:
        return self._call("POST", "/inverter", {"host": host, "port": port, "unit": unit})

    def debug_read(self, function: int, address: int, count: int) -> tuple[int, dict]:
        return self._call("POST", "/debug/read", {"function": function, "address": address, "count": count}, timeout=15)

    def scan_for_inverter(self, network: str) -> tuple[int, dict]:
        return self._call("POST", "/inverter/scan", {"network": network}, timeout=40)
