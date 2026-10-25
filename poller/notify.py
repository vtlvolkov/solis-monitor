"""
Telegram alerts from the poller: grid outage start/end, battery levels during an outage,
inverter/BMS alarms, and the inverter becoming unreachable.

Uses the Bot API's sendMessage only, so it can share a bot that another program (e.g. Hermes)
reads updates from. Messages are queued and sent from a background thread so a slow or
unreachable Telegram never delays polling.

    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   both required; alerts are off without them
    ALERT_SOC_LEVELS=50,30,20               battery levels reported during an outage
    ALERT_OFFLINE_MIN=5                     minutes of failed polls before "inverter unreachable"

Test from the host:  docker exec solis-poller python notify.py --test
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("solis-notify")


def fmt_duration(seconds: float) -> str:
    m = int(round(seconds / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def fmt_runtime(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 1 / 6:
        return "< 10 min"
    return "≈ " + fmt_duration(round(hours * 6) / 6 * 3600)        # nearest 10 minutes


class Telegram:
    def __init__(self, token: str, chat_id: str, post=None, retries: int = 3, backoff_s: float = 5, site: str = ""):
        self.token, self.chat_id = token or "", chat_id or ""
        self.site = site.strip()                 # SITE_NAME: tells two installations apart in one chat
        self.post = post or self._post
        self.retries, self.backoff_s = retries, backoff_s
        self.q: queue.Queue = queue.Queue(maxsize=100)
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="telegram").start()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        if self.site:
            text = f"[{self.site}] {text}"
        try:
            self.q.put_nowait(text)
        except queue.Full:
            log.warning("telegram queue full, dropping: %s", text)

    def _run(self):
        while True:
            text = self.q.get()
            for attempt in range(self.retries):
                try:
                    self.post(text)
                    break
                except Exception as exc:
                    log.warning("telegram send failed (%d/%d): %s", attempt + 1, self.retries, exc)
                    time.sleep(self.backoff_s * (attempt + 1))

    def _post(self, text: str) -> None:
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram error"))


class Alerts:
    """Turns poller events and snapshots into messages. `battery` returns (capacity_kwh, floor_soc)
    or None, for runtime estimates."""

    def __init__(self, send, soc_levels=(50, 30, 20), offline_after_s=300, battery=lambda: None,
                 tz="Europe/Kyiv", clock=time.time):
        self.send, self.battery, self.clock = send, battery, clock
        self.soc_levels = sorted({int(x) for x in soc_levels}, reverse=True)
        self.offline_after_s = offline_after_s
        self.zone = ZoneInfo(tz)
        self.outage = None                       # (start_ts, soc_at_start)
        self.alarms: dict[str, float] = {}       # key -> start_ts
        self.soc_sent: set[int] = set()
        self.offline_sent = False
        self.first_failure: float | None = None

    def _hhmm(self, ts):
        return datetime.fromtimestamp(ts, self.zone).strftime("%H:%M")

    def _runtime(self, soc, load):
        b = self.battery()
        if not b or soc is None or not load or load < 20:
            return None
        cap, floor = b
        return cap * max(0.0, soc - floor) / 100 / (load / 1000)

    @staticmethod
    def _kw(w):
        return f"{w / 1000:.2f} kW" if w is not None else "unknown load"

    def events(self, events: list[dict], vals: dict) -> None:
        now = self.clock()
        for ev in events:
            soc, load = ev.get("soc"), ev.get("load")
            if ev["kind"] == "outage" and ev["state"] == "start":
                self.outage = (now, soc)
                self.soc_sent = {lvl for lvl in self.soc_levels if soc is not None and soc <= lvl}
                self.send(f"⚡ Power outage at {self._hhmm(now)}. Running on battery: {_pct(soc)}, "
                          f"{fmt_runtime(self._runtime(soc, load))} at {self._kw(load)}.")
            elif ev["kind"] == "outage" and ev["state"] == "end":
                start, soc0 = self.outage or (None, None)
                lasted = f"Outage lasted {fmt_duration(now - start)}; battery {_pct(soc0)} → {_pct(soc)}." \
                    if start else f"Battery {_pct(soc)}."
                self.send(f"✅ Power is back ({self._hhmm(now)}). {lasted}")
                self.outage = None
            elif ev["kind"] == "alarm" and ev["state"] == "start":
                self.alarms[ev["key"]] = now
                self.send(f"⚠️ Inverter alarm: {ev['name']} ({ev['severity']}).")
            elif ev["kind"] == "alarm" and ev["state"] == "end":
                start = self.alarms.pop(ev["key"], None)
                after = f" after {fmt_duration(now - start)}" if start else ""
                self.send(f"Alarm cleared: {ev['name']}{after}.")

    def snapshot(self, vals: dict) -> None:
        """Battery levels during an outage: one message per level, the lowest newly crossed one."""
        if not self.outage:
            return
        soc = vals.get("battery_soc")
        if soc is None:
            return
        crossed = [lvl for lvl in self.soc_levels if soc <= lvl and lvl not in self.soc_sent]
        if crossed:
            self.soc_sent.update(crossed)
            load = vals.get("total_load_power")
            self.send(f"🔋 Battery at {_pct(soc)} during the outage: "
                      f"{fmt_runtime(self._runtime(soc, load))} left at {self._kw(load)}.")

    def poll_failed(self, error) -> None:
        now = self.clock()
        if self.first_failure is None:
            self.first_failure = now
        if not self.offline_sent and now - self.first_failure >= self.offline_after_s:
            self.offline_sent = True
            self.send(f"📡 Inverter not reachable for {fmt_duration(now - self.first_failure)} "
                      f"(last error: {str(error)[:120]}). The dashboard shows stale data.")

    def poll_ok(self) -> None:
        if self.offline_sent:
            self.send(f"📡 Inverter reachable again after {fmt_duration(self.clock() - self.first_failure)}.")
        self.first_failure, self.offline_sent = None, False


def _pct(v):
    return "unknown" if v is None else f"{round(v)}%"


def telegram_from_env() -> Telegram:
    return Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""), site=os.getenv("SITE_NAME", ""))


def from_env(battery=lambda: None) -> Alerts:
    tg = telegram_from_env()
    levels = [x for x in os.getenv("ALERT_SOC_LEVELS", "50,30,20").split(",") if x.strip()]
    alerts = Alerts(tg.send, soc_levels=levels, offline_after_s=float(os.getenv("ALERT_OFFLINE_MIN", "5")) * 60,
                    battery=battery, tz=os.getenv("TZ") or "Europe/Kyiv")
    alerts.telegram = tg
    log.info("Telegram alerts %s", "enabled" if tg.enabled else "off (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
    return alerts


if __name__ == "__main__" and "--test" in sys.argv:
    tg = telegram_from_env()
    if not tg.enabled:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    tg._post((f"[{tg.site}] " if tg.site else "") + "✅ Solis alerts are working. You'll get messages about outages, battery levels and alarms here.")
    print("sent")
