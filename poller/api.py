"""
The poller's internal HTTP API: the transport in front of control.Controller.

Served on CONTROL_PORT (Docker network only, never published) so the poller stays the inverter's only Modbus
client. Every request needs the shared secret (Authorization: Bearer <CONTROL_TOKEN>, or INFLUX_TOKEN where that
is not set), which only the web container has. The one exception is GET /health, the container healthcheck: it
says whether the poll loop is alive and nothing more. What a request may do, and the safety model behind every
write, is control.py's business; this module only parses requests and formats answers.
"""
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from control import ControlError, Controller

log = logging.getLogger("solis-control")


def make_handler(ctl: Controller):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # the web service gave up waiting

        def _authorized(self):
            if not ctl.cfg.token:
                return False
            return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {ctl.cfg.token}")

        def _run(self, fn):
            if fn is None:
                return self._send(404, {"detail": "not found"})
            if not self._authorized():
                return self._send(401, {"detail": "unauthorized"})
            try:
                self._send(200, fn())
            except ControlError as exc:
                self._send(exc.status, {"detail": str(exc)})
            except Exception as exc:                 # Modbus timeout, inverter offline ...
                log.warning("control request failed: %s", exc)
                self._send(502, {"detail": f"Inverter not reachable: {exc}"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1024:
                raise ControlError(400, "invalid body")
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                raise ControlError(400, "body must be JSON") from None
            if not isinstance(body, dict):
                raise ControlError(400, "body must be a JSON object")
            return body

        def do_GET(self):
            if self.path == "/health":                    # the container healthcheck: no secret, nothing beyond liveness
                h = ctl.health()
                return self._send(200 if h["alive"] else 503, h)
            routes = {"/settings": ctl.settings, "/settings/cached": ctl.cached_settings,
                      "/device": ctl.devices and ctl.devices.read, "/battery-capacity": ctl.battery_capacity, "/retention": ctl.retention,
                      "/inverter": ctl.inverter_address}
            self._run(routes.get(self.path))

        def do_POST(self):
            def apply():
                body = self._body()
                return ctl.apply(body.get("setting"), body.get("value"))

            def capacity():
                body = self._body()
                if set(body) != {"kwh"}:
                    raise ControlError(400, "body must be {kwh}")
                return ctl.set_battery_capacity(body["kwh"])

            def retention():
                body = self._body()
                if set(body) != {"days"}:
                    raise ControlError(400, "body must be {days}")
                return ctl.set_retention(body["days"])

            def inverter():
                body = self._body()
                if set(body) - {"host", "port", "unit"} or "host" not in body:
                    raise ControlError(400, "body must be {host, port, unit}")
                return ctl.set_inverter_address(body["host"], body.get("port", 502), body.get("unit", 1))

            def scan_for_logger():
                body = self._body()
                if set(body) != {"network"}:
                    raise ControlError(400, "body must be {network}")
                return ctl.scan_network(body["network"])

            def debug_read():
                body = self._body()
                if set(body) != {"function", "address", "count"}:
                    raise ControlError(400, "body must be {function, address, count}")
                return ctl.debug_read(body["function"], body["address"], body["count"])

            self._run({"/settings": apply, "/battery-capacity": capacity, "/retention": retention, "/inverter": inverter,
                       "/inverter/scan": scan_for_logger, "/debug/read": debug_read}.get(self.path))

        def log_message(self, fmt, *args):            # keep the poller log quiet
            pass

    return Handler


def serve(ctl: Controller) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", ctl.cfg.port), make_handler(ctl))
    threading.Thread(target=server.serve_forever, daemon=True, name="control-http").start()
    log.info("Control API on :%s (internal) | writes %s", ctl.cfg.port,
             "ENABLED" if ctl.cfg.enabled else "disabled (read-only)")
    return server
